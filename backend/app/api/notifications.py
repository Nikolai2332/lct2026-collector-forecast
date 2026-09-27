import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.api.predictions import load_predictions
from app.audit import audit
from app.config import get_settings
from app.access import Access, get_access, load_access, require
from app.db import SessionLocal, get_db
from app.deps import Page, at_param, page_param
from app.models import Prediction, User
from app.notifications import (
    broker,
    critical_transitions,
    group_counts,
    notifications_for,
    slice_notifications,
    sse_message,
)
from app.security import get_current_user, oauth2_scheme, sse_tickets, user_from_token
from app.services import ObjectIndex, snapshot_at

router = APIRouter(prefix="/api/notifications", tags=["Уведомления"])


def _window(db: Session, at, hours: int):
    """Лента считается от последнего среза не позже at: иначе «сейчас» после конца данных дало бы пустую ленту."""
    end = snapshot_at(db, at) or at
    return end - timedelta(hours=hours) + timedelta(microseconds=1), end


@router.get(
    "",
    response_model=schemas.NotificationList,
    summary="Лента уведомлений: новые переходы в «Критично» за последние часы до at",
    description=(
        "Только **новые** критичные: канал критичен в срезе и не был критичным ни в одном срезе за 24 ч до него "
        "(один канал — не больше одного раза за сутки). Период — `hours` часов до последнего среза не позже `at`. "
        "`groups` — число переходов по срезам (для сводок «ещё N датчиков перешли в «Критично»»)."
    ),
    dependencies=[Depends(get_current_user)],
)
def feed(
    hours: int = Query(24, ge=1, le=168, description="Глубина ленты, часов"),
    page: Page = Depends(page_param),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    scope = ObjectIndex.load(db, access).channel_objects
    transitions = critical_transitions(db, *_window(db, at, hours), scope)
    ids = [t.prediction_id for t in transitions[page.offset : page.offset + page.limit]]
    return schemas.NotificationList(
        at=at, total=len(transitions), items=notifications_for(db, load_predictions(db, ids), at),
        groups=group_counts(transitions),
    )


@router.post(
    "/ticket",
    response_model=schemas.SseTicketOut,
    summary="Одноразовый тикет для подключения к SSE",
    description=(
        "EventSource не умеет заголовок Authorization, а JWT в адресе попадает в логи прокси и историю браузера. "
        "Поэтому по обычному JWT выдаётся случайный тикет: он живёт 60 секунд, принимается только "
        "`/api/notifications/stream?ticket=` и сгорает при первом подключении. Для переподключения — новый тикет."
    ),
)
def issue_ticket(user: User = Depends(get_current_user)):
    ttl = get_settings().sse_ticket_ttl_seconds
    return schemas.SseTicketOut(ticket=sse_tickets.issue(user, ttl), expires_in=ttl)


def stream_user(
    ticket: str | None = Query(None, max_length=128, description="Тикет из POST /api/notifications/ticket"),
    header_token: str | None = Depends(oauth2_scheme),
) -> int:
    """Пользователь потока: по одноразовому тикету (браузер) или по заголовку Authorization (curl, тесты).

    Возвращает id: сессию БД на всё время потока не держим."""
    if ticket:
        user_id = sse_tickets.redeem(ticket)
        if user_id is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Тикет недействителен или уже использован")
        with SessionLocal() as db:
            user = db.get(User, user_id)
            if user is None or not user.is_active:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Пользователь не найден или заблокирован")
        return user_id
    if not header_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Требуется тикет или токен",
                            headers={"WWW-Authenticate": "Bearer"})
    with SessionLocal() as db:
        return user_from_token(header_token, db).id


@router.get(
    "/stream",
    summary="SSE: критические прогнозы в реальном времени",
    description=(
        "`text/event-stream`. Сразу после подключения приходит событие `snapshot` — новые переходы в «Критично» "
        "за 24 ч до `at` (массив NotificationItem; по срезу до трёх отдельных и сводка `critical_summary` «ещё N»). "
        "Дальше — событие `critical_prediction` (один NotificationItem, `kind` — отдельный или сводка) на каждый "
        "новый переход в «Критично» и комментарий `: ping` раз в 15 секунд. "
        "Во время симуляции — ещё служебное событие `sim_clock` (JSON: active, day, speed, model_time, snapshot_at) "
        "при смене среза, запуске и остановке; клиенты, которым оно не нужно, его игнорируют. "
        "Доступ — одноразовый `?ticket=` из `POST /api/notifications/ticket` (браузер) или заголовок "
        "Authorization (curl). JWT в адресе не принимается."
    ),
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "Поток событий SSE"}},
)
async def stream(
    request: Request,
    at=Depends(at_param),
    _user_id: int = Depends(stream_user),
):
    # Первичный срез читаем своей сессией: сессия из Depends закроется до конца потока.
    # Область видимости подписчика фиксируется при подключении (переподключение берёт новую)
    def initial() -> tuple[str, frozenset[int] | None]:
        with SessionLocal() as db:
            user = db.get(User, _user_id)
            scope = ObjectIndex.load(db, load_access(db, user)).channel_objects
            items = slice_notifications(db, critical_transitions(db, *_window(db, at, 24), scope), at)
            return "[" + ",".join(i.model_dump_json() for i in items) + "]", scope

    first, scope = await asyncio.to_thread(initial)
    queue = broker.subscribe(scope)
    ping = get_settings().sse_ping_seconds

    async def events():
        try:
            yield "retry: 5000\n\n"
            yield sse_message("snapshot", first)
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event, payload = await asyncio.wait_for(queue.get(), timeout=ping)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield sse_message(event, payload)
        finally:
            broker.unsubscribe(queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post(
    "/test",
    response_model=schemas.NotificationList,
    summary="Разослать в SSE критические прогнозы среза at (для проверки интерфейса)",
    description="Без ML и симулятора новых прогнозов нет; этот вызов позволяет проверить всплывающее окно и звук.",
)
def send_test(
    request: Request,
    limit: int = Query(1, ge=1, le=20),
    at=Depends(at_param),
    _access: Access = Depends(require("data_import")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    snap = snapshot_at(db, at)
    if snap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "На этот момент прогнозов нет")
    ids = db.scalars(
        select(Prediction.id)
        .where(Prediction.at == snap, Prediction.risk_level == "critical")
        .order_by(Prediction.prob.desc())
        .limit(limit)
    ).all()
    items = notifications_for(db, load_predictions(db, list(ids)), at)
    for item in items:
        broker.publish(item)
    audit(db, request, user, "notification_test", details={"sent": len(items), "subscribers": broker.subscribers})
    db.commit()
    return schemas.NotificationList(at=at, total=len(items), items=items)
