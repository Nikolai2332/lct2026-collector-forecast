from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app import schemas
from app.config import get_settings
from app.db import get_db
from app.deps import at_param
from app.labels import (
    DECISION_LABELS,
    RISK_LABELS,
    ROLE_LABELS,
    WORK_ORDER_PRIORITY_LABELS,
    WORK_ORDER_STATUS_LABELS,
)
from app.models import Channel, Reason, Recommendation
from app.security import get_current_user
from app.access import Access, get_access
from app.services import ObjectIndex, apply_channel_filters, current_model_version
from app.timeutil import now_msk

router = APIRouter(prefix="/api", tags=["Сервис и справочники"])


def _code_labels(labels: dict[str, str]) -> list[schemas.CodeLabel]:
    return [schemas.CodeLabel(code=k, name=v) for k, v in labels.items()]


@router.get("/health", response_model=schemas.HealthOut, summary="Статус сервиса и версия модели")
def health(_at=Depends(at_param), db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        model_version, db_state = current_model_version(db), "ok"
    except Exception:
        model_version, db_state = None, "error"
    return schemas.HealthOut(
        status="ok" if db_state == "ok" else "degraded",
        version=get_settings().app_version,
        model_version=model_version,
        database=db_state,
        time=now_msk(),
        demo=not get_settings().is_production,
    )


@router.get(
    "/reasons",
    response_model=list[schemas.ReasonOut],
    summary="Справочник причин для решений и заявок",
    dependencies=[Depends(get_current_user)],
)
def reasons(
    decision_type: schemas.DecisionType | None = Query(None, description="Только причины для этого решения и общие"),
    _at=Depends(at_param),
    db: Session = Depends(get_db),
):
    stmt = select(Reason).where(Reason.is_active)
    if decision_type:
        stmt = stmt.where((Reason.decision_type == decision_type) | Reason.decision_type.is_(None))
    return db.scalars(stmt.order_by(Reason.sort_order, Reason.id)).all()


@router.get(
    "/recommendations",
    response_model=list[schemas.RecommendationOut],
    summary="Справочник рекомендаций для заявок",
    dependencies=[Depends(get_current_user)],
)
def recommendations(
    sensor_type: str | None = Query(None, description="Рекомендации для типа датчика и общие"),
    _at=Depends(at_param),
    db: Session = Depends(get_db),
):
    stmt = select(Recommendation).where(Recommendation.is_active)
    if sensor_type:
        stmt = stmt.where((Recommendation.sensor_type == sensor_type) | Recommendation.sensor_type.is_(None))
    return db.scalars(stmt.order_by(Recommendation.id)).all()


@router.get(
    "/dictionaries",
    response_model=schemas.Dictionaries,
    summary="Подписи и значения для фильтров интерфейса",
    dependencies=[Depends(get_current_user)],
)
def dictionaries(_at=Depends(at_param), access: Access = Depends(get_access), db: Session = Depends(get_db)):
    # Типы датчиков и их число — по области пользователя (фильтры показывают только то, что он видит)
    stmt = apply_channel_filters(
        select(Channel.sensor_type, Channel.system_type, func.count()), ObjectIndex.load(db, access)
    )
    sensor_rows = db.execute(
        stmt.group_by(Channel.sensor_type, Channel.system_type).order_by(Channel.system_type, Channel.sensor_type)
    ).all()
    return schemas.Dictionaries(
        risk_levels=[schemas.RiskLevelOut(code=k, name=n, color=c) for k, (n, c) in RISK_LABELS.items()],
        decision_types=_code_labels(DECISION_LABELS),
        work_order_statuses=_code_labels(WORK_ORDER_STATUS_LABELS),
        work_order_priorities=_code_labels(WORK_ORDER_PRIORITY_LABELS),
        roles=_code_labels(ROLE_LABELS),
        system_types=sorted({r[1] for r in sensor_rows}),
        sensor_types=[schemas.SensorTypeOut(name=s, system_type=sys, channels_count=n) for s, sys, n in sensor_rows],
    )
