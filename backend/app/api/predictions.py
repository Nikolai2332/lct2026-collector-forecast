from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access, require
from app.audit import audit
from app.db import get_db
from app.deps import Page, at_param, page_param
from app.models import Channel, Decision, Prediction, Reason, User
from app.recommendations.engine import advice_for, to_schema
from app.risk import level_in
from app.security import get_current_user
from app.services import ObjectIndex, apply_channel_filters, prediction_detail, prediction_items, snapshot_at

router = APIRouter(prefix="/api/predictions", tags=["Прогнозы"])


def load_predictions(db: Session, ids: list[int]) -> list[Prediction]:
    """Загружает прогнозы по id, сохраняя порядок списка."""
    if not ids:
        return []
    by_id = {p.id: p for p in db.scalars(select(Prediction).where(Prediction.id.in_(ids)))}
    return [by_id[i] for i in ids if i in by_id]


@router.get(
    "",
    response_model=schemas.PredictionList,
    summary="Прогнозы на момент at по убыванию риска",
    description=(
        "Берётся последний срез прогнозов не позже `at`. Для `at` в прошлом `outcome` показывает, "
        "что реально случилось в следующие 24 часа (null — исход ещё неизвестен)."
    ),
    dependencies=[Depends(get_current_user)],
)
def list_predictions(
    risk_level: list[schemas.RiskLevel] | None = Query(None, description="Можно несколько"),
    min_prob: float | None = Query(None, ge=0, le=1, description="Только с вероятностью не ниже"),
    object_id: int | None = Query(None, description="Объект; включает все вложенные"),
    system_type: str | None = None,
    sensor_type: str | None = None,
    q: str | None = Query(None, description="Поиск по названию, тегу или id датчика"),
    page: Page = Depends(page_param),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    snap = snapshot_at(db, at)
    if snap is None:
        return schemas.PredictionList(at=at, total=0, items=[])
    idx = ObjectIndex.load(db, access)
    base = select(Prediction.id).join(Channel, Channel.id == Prediction.channel_id).where(Prediction.at == snap)
    base = apply_channel_filters(base, idx, object_id, system_type, sensor_type, q)
    if risk_level:
        base = base.where(level_in(risk_level))
    if min_prob is not None:
        base = base.where(Prediction.prob >= min_prob)
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    ids = db.scalars(base.order_by(Prediction.prob.desc(), Prediction.id).limit(page.limit).offset(page.offset)).all()
    return schemas.PredictionList(at=at, total=total, items=prediction_items(db, load_predictions(db, ids), idx, at))


def _prediction_or_404(db: Session, prediction_id: int, idx: ObjectIndex) -> Prediction:
    """Прогноз по датчику вне области пользователя — 404, как несуществующий."""
    p = db.get(Prediction, prediction_id)
    if p is None or not idx.channel_visible(p.channel.object_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Прогноз не найден")
    return p


@router.get(
    "/{prediction_id}",
    response_model=schemas.PredictionDetail,
    summary="Прогноз с факторами, исходом и историей решений",
    responses={404: {"model": schemas.ErrorResponse}},
    dependencies=[Depends(get_current_user)],
)
def get_prediction(
    prediction_id: int, at=Depends(at_param), access: Access = Depends(get_access), db: Session = Depends(get_db)
):
    idx = ObjectIndex.load(db, access)
    return prediction_detail(db, _prediction_or_404(db, prediction_id, idx), idx, at)


@router.get(
    "/{prediction_id}/recommendation",
    response_model=schemas.MaintenanceAdvice,
    summary="Рекомендация по ТО для прогноза (прозрачные правила, не ML)",
    description=(
        "Действие, обоснование со ссылкой на факты, приоритет, срок, исполнитель и «чего не делать». Факты берутся "
        "на момент среза прогноза: причины прогноза, отказы канала за 30/90 дней, соседи по объекту, питание объекта, "
        "заявки по датчику, точность модели по типу датчика. Правила — `backend/app/recommendations/rules.yaml` "
        "(проект правил, требует согласования со специалистами эксплуатации)."
    ),
    responses={404: {"model": schemas.ErrorResponse}},
    dependencies=[Depends(get_current_user)],
)
def get_recommendation(
    prediction_id: int, _at=Depends(at_param), access: Access = Depends(get_access), db: Session = Depends(get_db)
):
    p = _prediction_or_404(db, prediction_id, ObjectIndex.load(db, access))
    return to_schema(p, advice_for(db, p))


@router.post(
    "/{prediction_id}/decision",
    response_model=schemas.PredictionDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Решение диспетчера: выезд, ложное срабатывание или наблюдение",
    description=(
        "Причина (`reason_id`) обязательна. Необязательный `at` в запросе — момент «машины времени», "
        "которым датируется решение; по умолчанию текущее время."
    ),
    responses={404: {"model": schemas.ErrorResponse}, 403: {"model": schemas.ErrorResponse}},
)
def create_decision(
    prediction_id: int,
    body: schemas.DecisionIn,
    request: Request,
    at=Depends(at_param),
    access: Access = Depends(require("decide")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    pred = _prediction_or_404(db, prediction_id, idx)
    reason = db.get(Reason, body.reason_id)
    if reason is None or not reason.is_active:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Причина не найдена в справочнике")
    if reason.decision_type and reason.decision_type != body.decision_type:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Причина не подходит к выбранному решению")
    decision = Decision(
        prediction_id=pred.id,
        decision_type=body.decision_type,
        reason_id=reason.id,
        comment=(body.comment or "").strip() or None,
        user_id=user.id,
        created_at=max(at, pred.at),  # решение не может быть раньше прогноза
    )
    db.add(decision)
    db.flush()
    audit(db, request, user, "decision_create", "prediction", pred.id,
          {"decision_id": decision.id, "decision_type": body.decision_type, "reason_id": reason.id})
    db.commit()
    return prediction_detail(db, pred, idx, None)
