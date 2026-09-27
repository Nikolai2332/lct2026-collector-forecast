from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access, require
from app.audit import audit
from app.db import get_db
from app.deps import Page, at_param, page_param
from app.factors_human import humanize
from app.labels import WORK_ORDER_STATUS_LABELS, WORK_ORDER_TRANSITIONS
from app.models import Channel, Prediction, Reason, Recommendation, User, WorkOrder
from app.recommendations.engine import PRIORITY_BY_RISK, Advice, advice_for
from app.risk import level_of
from app.security import get_current_user
from app.services import ObjectIndex, get_or_404, work_order_out

router = APIRouter(prefix="/api/work-orders", tags=["Заявки"])

DUE_BY_PRIORITY = {
    "critical": timedelta(hours=4),
    "high": timedelta(hours=24),
    "medium": timedelta(days=3),
    "low": timedelta(days=7),
}
CLOSED_STATUSES = ("done", "cancelled")


def _check_refs(db: Session, reason_id: int | None, recommendation_id: int | None) -> Recommendation | None:
    if reason_id is not None and db.get(Reason, reason_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Причина не найдена в справочнике")
    if recommendation_id is None:
        return None
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Рекомендация не найдена в справочнике")
    return rec


def _default_recommendation(db: Session, sensor_type: str) -> Recommendation | None:
    return db.scalar(
        select(Recommendation)
        .where(Recommendation.is_active, or_(Recommendation.sensor_type == sensor_type, Recommendation.sensor_type.is_(None)))
        # DIALECT: сортировка по boolean (false < true) — так в PostgreSQL и SQLite; сначала рекомендации для типа
        .order_by(Recommendation.sensor_type.is_(None), Recommendation.id)
        .limit(1)
    )


def advice_description(pred: Prediction, advice: Advice | None) -> str:
    """Описание черновика: вероятность, рекомендация с обоснованием и причины прогноза языком диспетчера."""
    lines = [f"Прогноз отказа на 24 ч: вероятность {round(pred.prob * 100)}%, индекс здоровья {pred.health}."]
    if advice is not None:
        lines.append(f"Рекомендация ({advice.title}): {advice.reason}")
        lines += [f"Не делать: {x}" for x in advice.avoid]
    phrases = [h.text for h in humanize(pred.top_factors)]
    if phrases:
        lines.append("Причины прогноза:")
        lines += [f"— {p}" for p in phrases[:3] if p]
    return "\n".join(lines)


def new_work_order(
    db: Session,
    request: Request,
    user: User,
    at,
    body: schemas.WorkOrderIn,
    pred: Prediction | None,
    channel: Channel,
    advice: Advice | None,
    source: str = "manual",
) -> WorkOrder:
    """Черновик заявки. Из прогноза поля по умолчанию берутся из рекомендации по ТО (движок правил);
    всё, что передано явно, важнее. Коммит — за вызывающим."""
    rec = _check_refs(db, body.reason_id, body.recommendation_id)
    if rec is None and advice is not None and advice.recommendation_id is not None:
        rec = db.get(Recommendation, advice.recommendation_id)
    if rec is None:
        rec = _default_recommendation(db, channel.sensor_type)
    if body.priority:
        priority = body.priority
    elif advice is not None:
        priority = advice.priority
    else:
        priority = PRIORITY_BY_RISK[level_of(pred)] if pred else "medium"
    created_at = max(at, pred.at) if pred else at
    if body.due_at:
        due_at = body.due_at
    elif advice is not None and body.priority is None:
        due_at = created_at + timedelta(hours=advice.due_hours)
    else:
        due_at = created_at + DUE_BY_PRIORITY[priority]
    description = body.description
    if description is None and pred is not None:
        description = advice_description(pred, advice)
    wo = WorkOrder(
        number="",
        prediction_id=pred.id if pred else None,
        channel_id=channel.id,
        object_id=channel.object_id,
        status="draft",
        priority=priority,
        reason_id=body.reason_id,
        recommendation_id=rec.id if rec else None,
        recommendation_text=body.recommendation_text or (advice.action if advice is not None else (rec.text if rec else None)),
        description=description,
        assignee=body.assignee if body.assignee is not None else (advice.assignee if advice is not None and advice.assignee != "—" else None),
        due_at=due_at,
        created_by=user.id,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(wo)
    db.flush()
    wo.number = f"ЗН-{created_at.year}-{wo.id:06d}"
    audit(db, request, user, "work_order_create", "work_order", wo.id,
          {"prediction_id": wo.prediction_id, "channel_id": wo.channel_id, "priority": priority,
           "rule_id": advice.rule_id if advice is not None else None, "source": source})
    return wo


@router.post(
    "",
    response_model=schemas.WorkOrderOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать черновик заявки (из прогноза — с автозаполнением)",
    description=(
        "Из `prediction_id` подставляются датчик, объект и — по рекомендации движка правил ТО "
        "(`GET /api/predictions/{id}/recommendation`) — приоритет, срок, исполнитель, рекомендация и описание "
        "с обоснованием и причинами прогноза. Любое поле можно передать явно. "
        "`at` в запросе — момент «машины времени», которым датируется заявка."
    ),
    responses={404: {"model": schemas.ErrorResponse}, 403: {"model": schemas.ErrorResponse}},
)
def create_work_order(
    body: schemas.WorkOrderIn,
    request: Request,
    at=Depends(at_param),
    access: Access = Depends(require("work_orders")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    pred = get_or_404(db, Prediction, body.prediction_id, "Прогноз не найден") if body.prediction_id else None
    if pred is not None and not idx.channel_visible(pred.channel.object_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Прогноз не найден")
    if pred is not None and body.channel_id is not None and body.channel_id != pred.channel_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Датчик не совпадает с датчиком прогноза")
    channel = pred.channel if pred else get_or_404(db, Channel, body.channel_id, "Датчик не найден")
    if not idx.channel_visible(channel.object_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Датчик не найден")
    advice = advice_for(db, pred) if pred is not None else None
    wo = new_work_order(db, request, user, at, body, pred, channel, advice)
    db.commit()
    db.refresh(wo)
    return work_order_out(wo, idx)


def _work_order_or_404(db: Session, work_order_id: int, idx: ObjectIndex) -> WorkOrder:
    """Заявка по датчику вне области пользователя — 404, как несуществующая."""
    wo = db.get(WorkOrder, work_order_id)
    if wo is None or not idx.channel_visible(wo.channel.object_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Заявка не найдена")
    return wo


def load_work_orders(db: Session, ids) -> list[WorkOrder]:
    """Заявки по id в порядке списка. Черновик могли удалить между выборкой id и этой загрузкой (параллельный
    DELETE — нашёл нагрузочный тест, раньше это был 500) — такие id пропускаются."""
    by_id = {w.id: w for w in db.scalars(select(WorkOrder).where(WorkOrder.id.in_(ids)))} if ids else {}
    return [by_id[i] for i in ids if i in by_id]


@router.get(
    "",
    response_model=schemas.WorkOrderList,
    summary="Список заявок с фильтрами",
    dependencies=[Depends(get_current_user)],
)
def list_work_orders(
    status_: list[schemas.WorkOrderStatus] | None = Query(None, alias="status", description="Можно несколько"),
    priority: list[schemas.WorkOrderPriority] | None = Query(None),
    object_id: int | None = Query(None, description="Объект; включает все вложенные"),
    channel_id: int | None = None,
    prediction_id: int | None = None,
    q: str | None = Query(None, description="Поиск по номеру, датчику, исполнителю"),
    page: Page = Depends(page_param),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    base = select(WorkOrder.id).join(Channel, Channel.id == WorkOrder.channel_id).where(WorkOrder.created_at <= at)
    if idx.channel_objects is not None:
        base = base.where(Channel.object_id.in_(sorted(idx.channel_objects)))
    if status_:
        base = base.where(WorkOrder.status.in_(status_))
    if priority:
        base = base.where(WorkOrder.priority.in_(priority))
    if object_id is not None:
        base = base.where(WorkOrder.object_id.in_(idx.subtree_ids(object_id)))
    if channel_id is not None:
        base = base.where(WorkOrder.channel_id == channel_id)
    if prediction_id is not None:
        base = base.where(WorkOrder.prediction_id == prediction_id)
    if q:
        pattern = f"%{q.strip()}%"
        # DIALECT: ilike — ILIKE в PostgreSQL, lower() LIKE lower() в SQLite
        base = base.where(or_(WorkOrder.number.ilike(pattern), Channel.name.ilike(pattern), WorkOrder.assignee.ilike(pattern)))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    ids = db.scalars(base.order_by(WorkOrder.created_at.desc(), WorkOrder.id.desc()).limit(page.limit).offset(page.offset)).all()
    return schemas.WorkOrderList(at=at, total=total, items=[work_order_out(w, idx) for w in load_work_orders(db, ids)])


@router.get(
    "/{work_order_id}",
    response_model=schemas.WorkOrderOut,
    summary="Заявка",
    responses={404: {"model": schemas.ErrorResponse}},
    dependencies=[Depends(get_current_user)],
)
def get_work_order(
    work_order_id: int, _at=Depends(at_param), access: Access = Depends(get_access), db: Session = Depends(get_db)
):
    idx = ObjectIndex.load(db, access)
    return work_order_out(_work_order_or_404(db, work_order_id, idx), idx)


@router.patch(
    "/{work_order_id}",
    response_model=schemas.WorkOrderOut,
    summary="Изменить заявку и сменить статус",
    description=(
        "Переходы статусов: черновик → отправлена/отменена; отправлена → в работе/черновик/отменена; "
        "в работе → выполнена/отменена. Закрытую заявку менять нельзя (409). "
        "Техник (право «смена статуса») может менять только статус заявок своей области."
    ),
    responses={404: {"model": schemas.ErrorResponse}, 409: {"model": schemas.ErrorResponse}},
)
def update_work_order(
    work_order_id: int,
    body: schemas.WorkOrderPatch,
    request: Request,
    at=Depends(at_param),
    access: Access = Depends(require("work_order_status")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    wo = _work_order_or_404(db, work_order_id, idx)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нет полей для изменения")
    if not access.can("work_orders") and set(changes) - {"status"}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Техник может менять только статус заявки")
    if wo.status in CLOSED_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "Заявка закрыта, изменить её нельзя")
    new_status = changes.get("status")
    if new_status and new_status != wo.status and new_status not in WORK_ORDER_TRANSITIONS[wo.status]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Нельзя перевести заявку из статуса «{WORK_ORDER_STATUS_LABELS[wo.status]}» "
            f"в «{WORK_ORDER_STATUS_LABELS[new_status]}»",
        )
    _check_refs(db, changes.get("reason_id"), changes.get("recommendation_id"))

    old_status = wo.status
    for field, value in changes.items():
        setattr(wo, field, value)
    now = max(at, wo.created_at)
    wo.updated_at = now
    if wo.status in CLOSED_STATUSES:
        wo.closed_at = now
    audit(db, request, user, "work_order_update", "work_order", wo.id,
          {"changes": {k: str(v) for k, v in changes.items()}, "old_status": old_status})
    db.commit()
    db.refresh(wo)
    return work_order_out(wo, idx)


@router.delete(
    "/{work_order_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить черновик заявки",
    description=(
        "Удалить можно только черновик (созданный по ошибке или при проверке) — автору или администратору. "
        "Отправленные и закрытые заявки не удаляются (409): их отменяют сменой статуса. Удаление пишется в журнал аудита."
    ),
    responses={403: {"model": schemas.ErrorResponse}, 404: {"model": schemas.ErrorResponse}, 409: {"model": schemas.ErrorResponse}},
)
def delete_work_order(
    work_order_id: int,
    request: Request,
    access: Access = Depends(require("work_orders")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    wo = _work_order_or_404(db, work_order_id, ObjectIndex.load(db, access))
    if wo.status != "draft":
        raise HTTPException(status.HTTP_409_CONFLICT, "Удалить можно только черновик; отправленную заявку отмените")
    if not access.can("delete_any_draft") and wo.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Удалить черновик может только его автор или администратор")
    audit(db, request, user, "work_order_delete", "work_order", wo.id,
          {"number": wo.number, "channel_id": wo.channel_id, "prediction_id": wo.prediction_id})
    db.delete(wo)
    db.commit()
