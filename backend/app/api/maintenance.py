"""Рекомендованные работы: поддержка планирования профилактики (ТЗ, раздел 3) и черновики заявок по рекомендациям."""

from collections import defaultdict
from datetime import timedelta

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import schemas
from app.api.predictions import load_predictions
from app.api.work_orders import OpenWorkOrderExists, new_work_order
from app.access import Access, get_access, require
from app.cache import cached
from app.db import get_db
from app.deps import at_param
from app.models import Channel, ChannelDaily, ChannelFault, Prediction, User, WorkOrder
from app.recommendations.engine import PRIORITIES, RULES_PATH, advise_predictions, load_rules, to_schema
from app.risk import level_in, level_of
from app.security import get_current_user
from app.services import ObjectIndex, apply_channel_filters, channel_ref, snapshot_at, work_order_out

router = APIRouter(prefix="/api/maintenance", tags=["Рекомендации по ТО"])

# Кандидаты в профилактику: прогноз «внимание» и выше или повторные отказы за 30 дней
REPEAT_MIN_30D = 2


def repeat_channels(db: Session, snap):
    """Подзапрос: каналы с повторными отказами за 30 дней до среза (в демо — дни с сообщениями о неисправности)."""
    if db.scalar(select(ChannelFault.id).limit(1)) is not None:
        return (
            select(ChannelFault.channel_id)
            .where(ChannelFault.ts > snap - timedelta(days=30), ChannelFault.ts <= snap)
            .group_by(ChannelFault.channel_id)
            .having(func.count() >= REPEAT_MIN_30D)
        )
    return (
        select(ChannelDaily.channel_id)
        .where(ChannelDaily.fault_count > 0, ChannelDaily.day > (snap - timedelta(days=30)).date(), ChannelDaily.day < snap.date())
        .group_by(ChannelDaily.channel_id)
        .having(func.count() >= REPEAT_MIN_30D)
    )


@router.get(
    "/plan",
    response_model=schemas.MaintenancePlan,
    summary="Рекомендованные работы: профилактика на ближайшие дни по правилам ТО",
    description=(
        "Датчики среза, которым по правилам нужна профилактика (повторные отказы, повтор после ремонта, "
        "деградация показаний, агрегаты, питание и связь объекта) и по которым ещё нет открытой заявки. "
        "Сгруппированы по объектам: сначала объекты с самым высоким приоритетом. Кандидаты — прогнозы "
        f"«Внимание» и выше и датчики с {REPEAT_MIN_30D}+ отказами за 30 дней."
    ),
    dependencies=[Depends(get_current_user)],
)
def plan(
    object_id: int | None = Query(None, description="Объект; включает вложенные"),
    limit: int = Query(50, ge=1, le=500, description="Объектов на странице"),
    offset: int = Query(0, ge=0),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    rules = load_rules()
    snap = snapshot_at(db, at)
    if snap is None:
        return schemas.MaintenancePlan(at=at, snapshot_at=None, total_items=0, total_objects=0, groups=[], source=rules.source)
    groups = plan_groups(db, snap, object_id, ObjectIndex.load(db, access))
    return schemas.MaintenancePlan(
        at=at, snapshot_at=snap, total_items=sum(len(g.items) for g in groups), total_objects=len(groups),
        groups=groups[offset : offset + limit], source=rules.source,
    )


def work_orders_version(db: Session) -> tuple:
    """Меняется при любой новой, изменённой или удалённой заявке — «Рекомендованные работы» учитывают открытые заявки."""
    return tuple(db.execute(select(func.count(WorkOrder.id), func.max(WorkOrder.id), func.max(WorkOrder.updated_at))).one())


def plan_groups(db: Session, snap, object_id: int | None, idx: ObjectIndex) -> list[schemas.MaintenancePlanGroup]:
    """Все группы плана на срез. На реальных данных это ≈ 1 200 работ и 1,4 с расчёта, поэтому результат кэшируется
    (app.cache): ключ — срез, объект, версия заявок и файла правил; прогнозы, исходы и настройки сбрасывают кэш сами."""
    if object_id is not None:
        idx.subtree_ids(object_id)  # чужой объект — 404 до кэша
    key = ("maintenance_plan", snap, object_id, idx.scope_key, work_orders_version(db), RULES_PATH.stat().st_mtime)
    return cached(db, key, lambda: _compute_groups(db, snap, object_id, idx))


def _compute_groups(db: Session, snap, object_id: int | None, idx: ObjectIndex) -> list[schemas.MaintenancePlanGroup]:
    stmt = (
        select(Prediction.id)
        .join(Channel, Channel.id == Prediction.channel_id)
        .where(Prediction.at == snap, or_(level_in(("attention", "risk", "critical")),
                                          Prediction.channel_id.in_(repeat_channels(db, snap))))
    )
    stmt = apply_channel_filters(stmt, idx, object_id)
    preds = load_predictions(db, list(db.scalars(stmt.order_by(Prediction.prob.desc(), Prediction.id))))
    advice = advise_predictions(db, preds)

    by_object: dict[int, list[schemas.MaintenancePlanItem]] = defaultdict(list)
    for p in preds:
        a = advice[p.id]
        if not (a.plan and a.work_order) or a.has_open_order:
            continue
        by_object[p.channel.object_id].append(schemas.MaintenancePlanItem(
            prediction_id=p.id, channel=channel_ref(p.channel), prob=round(p.prob, 4), risk_level=level_of(p),
            advice=to_schema(p, a),
        ))
    rank = {pr: i for i, pr in enumerate(PRIORITIES)}
    groups = []
    for oid, items in by_object.items():
        items.sort(key=lambda it: (-rank[it.advice.priority], -it.prob, it.prediction_id))
        groups.append(schemas.MaintenancePlanGroup(object=idx.ref(oid), top_priority=items[0].advice.priority, items=items))
    groups.sort(key=lambda g: (-rank[g.top_priority], -len(g.items), g.object.name))
    return groups


@router.post(
    "/drafts",
    response_model=schemas.MaintenanceDraftsOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать черновики заявок по рекомендациям (выбранным прогнозам)",
    description=(
        "Для каждого прогноза — черновик по рекомендации ТО, как `POST /api/work-orders` с `prediction_id`. "
        "Пропускаются: датчики с открытой заявкой, рекомендации без заявки (норма, наблюдение). "
        "Для правил «на объект» (массовая потеря связи, питание объекта) — одна заявка на объект, "
        "остальные датчики перечисляются в её описании. Каждая заявка пишется в `audit_log`."
    ),
    responses={403: {"model": schemas.ErrorResponse}},
)
def drafts(
    body: schemas.MaintenanceDraftsIn,
    request: Request,
    at=Depends(at_param),
    access: Access = Depends(require("work_orders")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    ids = list(dict.fromkeys(body.prediction_ids))
    # Прогнозы вне области — «не найден», как несуществующие
    preds = [p for p in load_predictions(db, ids) if idx.channel_visible(p.channel.object_id)]
    found = {p.id for p in preds}
    skipped = [schemas.MaintenanceDraftSkipped(prediction_id=i, reason="Прогноз не найден") for i in ids if i not in found]
    advice = advise_predictions(db, preds)
    created, per_object = [], {}
    for p in preds:
        a = advice[p.id]
        if not a.work_order:
            skipped.append(schemas.MaintenanceDraftSkipped(prediction_id=p.id, reason=f"Заявка не нужна: {a.title.lower()}"))
            continue
        if a.has_open_order:
            skipped.append(schemas.MaintenanceDraftSkipped(prediction_id=p.id, reason="По датчику уже открыта заявка"))
            continue
        key = (p.channel.object_id, a.rule_id)
        if a.per_object and key in per_object:
            wo = per_object[key]
            wo.description = f"{wo.description}\n— «{p.channel.name}» (вероятность {round(p.prob * 100)}%)"
            skipped.append(schemas.MaintenanceDraftSkipped(prediction_id=p.id, reason=f"Включён в заявку {wo.number} на объект"))
            continue
        try:
            wo = new_work_order(db, request, user, at, schemas.WorkOrderIn(prediction_id=p.id), p, p.channel, a,
                                source="maintenance_plan")
        except OpenWorkOrderExists as e:
            # Открытая сейчас (в том числе созданная позже момента «машины времени» или параллельным запросом)
            skipped.append(schemas.MaintenanceDraftSkipped(prediction_id=p.id, reason=f"По датчику уже открыта заявка {e.wo.number}"))
            continue
        if a.per_object:
            wo.description = f"{wo.description}\nДатчики объекта в этой заявке:\n— «{p.channel.name}» (вероятность {round(p.prob * 100)}%)"
            per_object[key] = wo
        created.append(wo)
    db.commit()
    for wo in created:
        db.refresh(wo)
    return schemas.MaintenanceDraftsOut(created=[work_order_out(wo, idx) for wo in created], skipped=skipped)
