"""Брокер уведомлений для SSE: рассылает критические прогнозы всем подписчикам процесса.

Работает в одном процессе uvicorn. Для нескольких воркеров нужен общий канал
(PostgreSQL LISTEN/NOTIFY) — не сделано: API запускается одним процессом (docs/ARCHITECTURE.md).

В очередь подписчика кладётся пара (тип события SSE, данные JSON): critical_prediction — уведомление,
sim_clock — служебное событие часов симуляции (старые клиенты его просто не слушают).

Области видимости (app.access): у каждого подписчика — набор объектов, датчики которых он видит (None — всё).
Уведомления собираются отдельно для каждой области среди подписчиков (publish_scoped): и отдельные, и сводки
«ещё N» считаются только по датчикам области — чужие счётчики не утекают. Одиночное уведомление (publish)
получают только подписчики, чья область содержит объект датчика.
"""

import asyncio
import json
import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.access import FULL_ACCESS
from app.models import Channel, Prediction
from app.risk import level_in, level_of
from app.runtime_settings import current
from app.services import ObjectIndex, prediction_items

Scope = frozenset[int] | None  # объекты, датчики которых видит подписчик; None — всё

log = logging.getLogger(__name__)


class Broker:
    def __init__(self) -> None:
        self._subscribers: dict[asyncio.Queue, Scope] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self, scope: Scope = None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers[queue] = scope
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue, None)

    @property
    def subscribers(self) -> int:
        return len(self._subscribers)

    def scopes(self) -> set[Scope]:
        with self._lock:
            return set(self._subscribers.values())

    def _deliver(self, message: tuple[str, str], only: Callable[[Scope], bool] | None = None) -> None:
        with self._lock:
            targets = [q for q, sc in self._subscribers.items() if only is None or only(sc)]
        for queue in targets:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:  # медленный клиент — пропускаем, чтобы не держать память
                log.warning("SSE: очередь подписчика переполнена, уведомление пропущено")

    def publish(self, item: schemas.NotificationItem) -> None:
        """Одно уведомление — только подписчикам, чья область содержит объект датчика. Можно звать из
        синхронных обработчиков (они работают в пуле потоков)."""
        object_id = item.prediction.object.id
        self._publish(("critical_prediction", item.model_dump_json()), lambda sc: sc is None or object_id in sc)

    def publish_scoped(self, build: Callable[[Scope], Sequence[schemas.NotificationItem]]) -> int:
        """Для каждой области среди подписчиков — свои уведомления build(scope), отдельные и сводки.
        Возвращает число уведомлений полной области (счётчик симуляции считает их, как раньше)."""
        sent = 0
        for scope in self.scopes() | {None}:
            items = build(scope)
            if scope is None:
                sent = len(items)
            for item in items:
                self._publish(("critical_prediction", item.model_dump_json()), lambda sc, s=scope: sc == s)
        return sent

    def publish_event(self, event: str, data: dict) -> None:
        """Служебное событие SSE (например, sim_clock) — всем: данных датчиков в нём нет."""
        self._publish((event, json.dumps(data, ensure_ascii=False, default=str)))

    def _publish(self, payload: tuple[str, str], only: Callable[[Scope], bool] | None = None) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self._loop:
            self._deliver(payload, only)
        else:
            self._loop.call_soon_threadsafe(self._deliver, payload, only)


broker = Broker()


# «Новый критичный» — канал критичен в срезе и не был критичным ни в одном срезе за N ч до него (по умолчанию 24).
# Так уведомление не повторяется каждый час, пока датчик «мигает» у порога (на реальных данных за сутки
# 2 770 критичных прогнозов, из них новых — около 180). N и число отдельных уведомлений на срез (по умолчанию 3,
# остальные — одной сводкой) администратор меняет в «Настройках» (app.runtime_settings).


@dataclass
class Transition:
    prediction_id: int
    at: datetime
    prob: float


def critical_transitions(
    db: Session, date_from: datetime, date_to: datetime, scope: Scope = None
) -> list[Transition]:
    """Новые переходы в «Критично» в срезах [date_from, date_to]; новые срезы и более вероятные — первыми.
    scope — объекты области видимости (None — все).

    Читает только критичные прогнозы за период и 24 ч до него (индекс ix_predictions_at_risk)."""
    cooldown = timedelta(hours=current().notify_cooldown_hours)
    stmt = (
        select(Prediction.id, Prediction.channel_id, Prediction.at, Prediction.prob)
        .where(level_in(["critical"]), Prediction.at >= date_from - cooldown, Prediction.at <= date_to)
        .order_by(Prediction.at)
    )
    if scope is not None:
        stmt = stmt.where(Prediction.channel_id.in_(select(Channel.id).where(Channel.object_id.in_(sorted(scope)))))
    rows = db.execute(stmt).all()
    last_critical: dict[int, datetime] = {}
    result = []
    for pid, channel_id, at, prob in rows:
        prev = last_critical.get(channel_id)
        if at >= date_from and (prev is None or prev < at - cooldown):
            result.append(Transition(pid, at, prob))
        last_critical[channel_id] = at
    result.sort(key=lambda t: (t.at, t.prob, -t.prediction_id), reverse=True)
    return result


def group_counts(transitions: Sequence[Transition]) -> list[schemas.NotificationGroup]:
    counts: dict[datetime, int] = {}
    for t in transitions:
        counts[t.at] = counts.get(t.at, 0) + 1
    return [schemas.NotificationGroup(snapshot_at=at, count=n) for at, n in sorted(counts.items(), reverse=True)]


def notification_from(item: schemas.PredictionItem, at) -> schemas.NotificationItem:
    return schemas.NotificationItem(
        id=item.prediction_id,
        created_at=at,
        title=f"Критический риск: {item.channel.name}",
        message=f"{item.object.name} · вероятность отказа {round(item.prob * 100)} %",
        prediction=item,
    )


def notifications_for(db: Session, preds: Sequence[Prediction], at=None) -> list[schemas.NotificationItem]:
    if not preds:
        return []
    # Прогнозы уже отобраны по области вызывающим кодом; полное дерево — только для путей «район → объект»
    idx = ObjectIndex.load(db, FULL_ACCESS)
    items = prediction_items(db, preds, idx, at)
    return [notification_from(item, p.at) for item, p in zip(items, preds)]


def load_predictions_ordered(db: Session, ids: Sequence[int]) -> list[Prediction]:
    by_id = {p.id: p for p in db.scalars(select(Prediction).where(Prediction.id.in_(ids)))} if ids else {}
    return [by_id[i] for i in ids if i in by_id]


def sensors_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "датчик перешёл"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "датчика перешли"
    return "датчиков перешли"


def slice_notifications(db: Session, transitions: Sequence[Transition], moment=None,
                        per_slice: int | None = None) -> list[schemas.NotificationItem]:
    """По каждому срезу: до per_slice уведомлений по отдельности, остальные — одной сводкой «ещё N»."""
    per_slice = per_slice or current().notify_per_slice
    by_slice: dict[datetime, list[Transition]] = {}
    for t in transitions:
        by_slice.setdefault(t.at, []).append(t)
    head_ids, summaries = [], []
    for at, ts in by_slice.items():
        head_ids += [t.prediction_id for t in ts[:per_slice]]
        if len(ts) > per_slice:
            summaries.append((at, ts[per_slice].prediction_id, len(ts) - per_slice))
    items = {n.id: n for n in notifications_for(db, load_predictions_ordered(db, head_ids), moment)}
    tops = {n.id: n for n in notifications_for(db, load_predictions_ordered(db, [s[1] for s in summaries]), moment)}
    result = [items[i] for i in head_ids if i in items]
    for at, top_id, rest in summaries:
        top = tops.get(top_id)
        if top is None:
            continue
        result.append(schemas.NotificationItem(
            id=top.id,
            kind="critical_summary",
            count=rest,
            created_at=at,
            title=f"Ещё {rest} {sensors_word(rest)} в «Критично»",
            message=f"Срез {at:%d.%m %H:%M} · самый вероятный: {top.prediction.channel.name}, "
                    f"{round(top.prediction.prob * 100)} %",
            prediction=top.prediction,
        ))
    # Новые срезы сверху; внутри среза — отдельные уведомления, затем сводка
    result.sort(key=lambda n: (n.created_at, n.kind == "critical_prediction"), reverse=True)
    return result


def publish_critical(db: Session, preds: Sequence[Prediction], per_slice: int = 5) -> int:
    """Импорт прогнозов: в SSE уходят только новые переходы в «Критично», по срезу — до per_slice и сводка;
    каждой области подписчиков — свои."""
    sent = 0
    for snap in sorted({p.at for p in preds if level_of(p) == "critical"}):
        sent += broker.publish_scoped(
            lambda scope, s=snap: slice_notifications(db, critical_transitions(db, s, s, scope), per_slice=per_slice)
        )
    return sent


def sse_message(event: str, data: str | dict, event_id: int | None = None) -> str:
    if not isinstance(data, str):
        data = json.dumps(data, ensure_ascii=False, default=str)
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {data}\n\n"
