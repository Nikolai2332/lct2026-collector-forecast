from collections import defaultdict
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access
from app.db import get_db
from app.deps import Page, at_param, page_param
from app.importer import FAULT_STATUSES
from app.labels import OPEN_WORK_ORDER_STATUSES
from app.models import Channel, ChannelDaily, ChannelFault, EventRecent, Prediction, WorkOrder
from app.risk import level_expr, level_in, level_of
from app.security import get_current_user
from app.services import (
    ObjectIndex,
    apply_channel_filters,
    latest_decisions,
    prediction_detail,
    snapshot_at,
)

router = APIRouter(prefix="/api/channels", tags=["Датчики"], dependencies=[Depends(get_current_user)])


def channel_page(
    db: Session,
    idx: ObjectIndex,
    at: datetime,
    snap: datetime | None,
    page: Page,
    object_id: int | None = None,
    system_type: str | None = None,
    sensor_type: str | None = None,
    risk_level: list[str] | None = None,
    q: str | None = None,
    sort: str = "risk",
) -> schemas.ChannelList:
    """Страница датчиков с их прогнозом на срез snap. Сначала id страницы, потом полные строки."""
    base = (
        select(Channel.id, Prediction.id.label("pid"))
        .select_from(Channel)
        .outerjoin(Prediction, and_(Prediction.channel_id == Channel.id, Prediction.at == snap))
    )
    base = apply_channel_filters(base, idx, object_id, system_type, sensor_type, q)
    if risk_level:
        base = base.where(level_in(risk_level))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    if sort == "name":
        order = (Channel.name, Channel.id)
    else:
        # DIALECT: NULLS LAST — PostgreSQL и SQLite ≥ 3.30. Датчики без прогноза — в конце.
        order = (Prediction.prob.desc().nulls_last(), Channel.id)
    rows = db.execute(base.order_by(*order).limit(page.limit).offset(page.offset)).all()

    channels = {c.id: c for c in db.scalars(select(Channel).where(Channel.id.in_([r.id for r in rows])))}
    pred_ids = [r.pid for r in rows if r.pid is not None]
    preds = {p.id: p for p in db.scalars(select(Prediction).where(Prediction.id.in_(pred_ids)))} if pred_ids else {}
    items = []
    for r in rows:
        ch, p = channels[r.id], preds.get(r.pid)
        items.append(
            schemas.ChannelListItem(
                id=ch.id,
                name=ch.name,
                sensor_type=ch.sensor_type,
                system_type=ch.system_type,
                tag=ch.tag,
                object=idx.ref(ch.object_id),
                prediction=(
                    schemas.ChannelPredictionShort(
                        prediction_id=p.id, prob=round(p.prob, 4), health=p.health, risk_level=level_of(p)
                    )
                    if p
                    else None
                ),
            )
        )
    return schemas.ChannelList(at=at, total=total, items=items)


@router.get("", response_model=schemas.ChannelList, summary="Датчики с фильтрами и прогнозом на момент at")
def list_channels(
    object_id: int | None = Query(None, description="Объект; включает все вложенные"),
    system_type: str | None = None,
    sensor_type: str | None = None,
    risk_level: list[schemas.RiskLevel] | None = Query(None, description="Можно несколько"),
    q: str | None = Query(None, description="Поиск по названию, тегу или id"),
    sort: Literal["risk", "name"] = Query("risk", description="risk — по убыванию вероятности отказа"),
    page: Page = Depends(page_param),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    return channel_page(
        db, idx, at, snapshot_at(db, at), page, object_id, system_type, sensor_type, risk_level, q, sort
    )


def _channel_or_404(db: Session, channel_id: int, idx: ObjectIndex) -> Channel:
    """Датчик вне области пользователя — 404, как несуществующий."""
    ch = db.get(Channel, channel_id)
    if ch is None or not idx.channel_visible(ch.object_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Датчик не найден")
    return ch


@router.get(
    "/{channel_id}",
    response_model=schemas.ChannelCard,
    summary="Карточка датчика: прогноз, причины, решения, заявки",
    responses={404: {"model": schemas.ErrorResponse}},
)
def channel_card(
    channel_id: int, at=Depends(at_param), access: Access = Depends(get_access), db: Session = Depends(get_db)
):
    idx = ObjectIndex.load(db, access)
    ch = _channel_or_404(db, channel_id, idx)
    snap = snapshot_at(db, at)
    pred = (
        db.scalar(select(Prediction).where(Prediction.channel_id == ch.id, Prediction.at == snap))
        if snap
        else None
    )
    last_event = db.scalar(
        select(EventRecent)
        .where(EventRecent.channel_id == ch.id, EventRecent.ts <= at)
        .order_by(EventRecent.ts.desc())
        .limit(1)
    )
    orders = db.scalars(
        select(WorkOrder)
        .where(WorkOrder.channel_id == ch.id, WorkOrder.created_at <= at, WorkOrder.status.in_(OPEN_WORK_ORDER_STATUSES))
        .order_by(WorkOrder.created_at.desc())
    ).all()
    # «На контроле» — последнее решение по этому датчику до at было «Наблюдение»
    recent_pred_ids = db.scalars(
        select(Prediction.id).where(Prediction.channel_id == ch.id, Prediction.at <= at, Prediction.at > at - timedelta(days=7))
    ).all()
    decisions = latest_decisions(db, recent_pred_ids, at)
    last_decision = max(decisions.values(), key=lambda d: d.id) if decisions else None
    return schemas.ChannelCard(
        at=at,
        id=ch.id,
        name=ch.name,
        sensor_type=ch.sensor_type,
        system_type=ch.system_type,
        tag=ch.tag,
        tag_levels=[lvl for lvl in (ch.tag_l1, ch.tag_l2, ch.tag_l3, ch.tag_l4, ch.tag_l5) if lvl],
        object=idx.ref(ch.object_id),
        prediction=prediction_detail(db, pred, idx, at) if pred else None,
        last_event=(
            schemas.EventOut(
                ts=last_event.ts, is_alarm=last_event.is_alarm, value=last_event.value_raw, value_num=last_event.value_num
            )
            if last_event
            else None
        ),
        on_watch=bool(last_decision and last_decision.decision_type == "monitor"),
        open_work_orders=[schemas.WorkOrderShort.model_validate(o) for o in orders],
    )


def _hourly_points(events: list[EventRecent], date_from: datetime, date_to: datetime) -> list[schemas.HistoryPoint]:
    buckets: dict[datetime, list[EventRecent]] = defaultdict(list)
    for e in events:
        buckets[e.ts.replace(minute=0, second=0)].append(e)
    # Ровно столько часов, сколько в периоде (30 дней — 720 точек): последний час — тот, где лежит date_to
    points = []
    last = date_to.replace(minute=0, second=0, microsecond=0)
    t = last - timedelta(hours=round((date_to - date_from).total_seconds() / 3600) - 1)
    while t <= last:
        evs = buckets.get(t, [])
        nums = [e.value_num for e in evs if e.value_num is not None]
        texts = [e.value_text for e in evs if e.value_text]
        points.append(
            schemas.HistoryPoint(
                t=t,
                events_count=len(evs),
                alarm_count=sum(e.is_alarm for e in evs),
                fault_count=sum(e.value_text in FAULT_STATUSES for e in evs),
                uncertain_count=sum(e.value_text == "Неопределен" for e in evs),
                status_changes=sum(a != b for a, b in zip(texts, texts[1:])),
                value_avg=round(sum(nums) / len(nums), 4) if nums else None,
                value_min=min(nums) if nums else None,
                value_max=max(nums) if nums else None,
            )
        )
        t += timedelta(hours=1)
    return points


@router.get(
    "/{channel_id}/history",
    response_model=schemas.ChannelHistory,
    summary="Суточные (или почасовые) агрегаты, отказы и прогнозы для графика",
    responses={404: {"model": schemas.ErrorResponse}},
)
def channel_history(
    channel_id: int,
    days: int = Query(30, ge=1, le=90, description="Глубина истории, дней"),
    granularity: Literal["day", "hour"] = Query("day", description="hour — до 30 дней (720 точек)"),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    ch = _channel_or_404(db, channel_id, ObjectIndex.load(db, access))
    if granularity == "hour" and days > 30:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Почасовая история доступна не более чем за 30 дней")
    date_to = at
    date_from = at - timedelta(days=days)

    events = db.scalars(
        select(EventRecent)
        .where(EventRecent.channel_id == ch.id, EventRecent.ts > date_from, EventRecent.ts <= date_to)
        .order_by(EventRecent.ts)
    ).all()
    if granularity == "hour":
        points = _hourly_points(events, date_from, date_to)
    else:
        rows = db.scalars(
            select(ChannelDaily)
            .where(ChannelDaily.channel_id == ch.id, ChannelDaily.day > date_from.date(), ChannelDaily.day <= date_to.date())
            .order_by(ChannelDaily.day)
        ).all()
        points = [
            schemas.HistoryPoint(
                t=datetime.combine(r.day, datetime.min.time()),
                events_count=r.events_count,
                alarm_count=r.alarm_count,
                fault_count=r.fault_count,
                uncertain_count=r.uncertain_count,
                status_changes=r.status_changes,
                value_avg=r.value_avg,
                value_min=r.value_min,
                value_max=r.value_max,
            )
            for r in rows
        ]
    # Отказы: разметка ML (все 3 вида, включая пропадание связи) + статусы-отказы из событий
    # (демо-сид и входящий поток пишут только события). Совпадения по (время, вид) схлопываются.
    ml_faults = db.execute(
        select(ChannelFault.ts, ChannelFault.kind)
        .where(ChannelFault.channel_id == ch.id, ChannelFault.ts > date_from, ChannelFault.ts <= date_to)
    ).all()
    fault_marks = {(f.ts, f.kind) for f in ml_faults}
    fault_marks |= {(e.ts, e.value_text) for e in events if e.value_text in FAULT_STATUSES}
    preds = db.execute(
        select(Prediction.id, Prediction.at, Prediction.prob, Prediction.health, level_expr().label("risk_level"))
        .where(Prediction.channel_id == ch.id, Prediction.at > date_from, Prediction.at <= date_to)
        .order_by(Prediction.at)
    ).all()
    return schemas.ChannelHistory(
        channel_id=ch.id,
        at=at,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
        points=points,
        faults=[schemas.FaultMark(ts=ts, kind=kind) for ts, kind in sorted(fault_marks)],
        predictions=[
            schemas.PredictionPoint(prediction_id=p.id, at=p.at, prob=round(p.prob, 4), health=p.health, risk_level=p.risk_level)
            for p in preds
        ],
    )
