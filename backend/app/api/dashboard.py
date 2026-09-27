from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access
from app.cache import cached
from app.db import get_db
from app.deps import at_param
from app.labels import ALERT_LEVELS, OPEN_WORK_ORDER_STATUSES
from app.models import Channel, Prediction, PredictionOutcome, WorkOrder
from app.risk import level_expr
from app.security import get_current_user
from app.services import ObjectIndex, current_model_version, restrict_by_channel, snapshot_at, working_threshold

router = APIRouter(prefix="/api/dashboard", tags=["Дашборд"], dependencies=[Depends(get_current_user)])


def accuracy(db: Session, at, threshold: float, idx: ObjectIndex) -> schemas.Accuracy:
    """Точность за 30 дней по прогнозам, чей 24-часовой горизонт к моменту at уже закрылся."""
    window_to = at - timedelta(hours=24)
    window_from = at - timedelta(days=30)
    flagged = Prediction.prob >= threshold
    happened = PredictionOutcome.happened.is_(True)
    stmt = (
        select(
            func.coalesce(func.sum(case((and_(flagged, happened), 1), else_=0)), 0),
            func.coalesce(func.sum(case((and_(flagged, ~happened), 1), else_=0)), 0),
            func.coalesce(func.sum(case((and_(~flagged, happened), 1), else_=0)), 0),
        )
        .select_from(Prediction)
        .join(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(Prediction.at > window_from, Prediction.at <= window_to)
    )
    tp, fp, fn = db.execute(restrict_by_channel(stmt, Prediction.channel_id, idx)).one()
    return schemas.Accuracy(
        precision=round(tp / (tp + fp), 3) if tp + fp else None,
        recall=round(tp / (tp + fn), 3) if tp + fn else None,
        true_positive=tp,
        false_positive=fp,
        false_negative=fn,
        threshold=threshold,
        window_from=window_from,
        window_to=window_to,
    )


@router.get("/summary", response_model=schemas.DashboardSummary, summary="Плашки дашборда на момент at")
def summary(at=Depends(at_param), access: Access = Depends(get_access), db: Session = Depends(get_db)):
    idx = ObjectIndex.load(db, access)
    snap = snapshot_at(db, at)
    threshold = working_threshold(db)
    distribution = {level: 0 for level in ("normal", "attention", "risk", "critical")}
    predicted, expected = 0, 0.0
    if snap is not None:
        level_col = level_expr()
        by_level = select(level_col, func.count()).where(Prediction.at == snap).group_by(level_col)
        for level, n in db.execute(restrict_by_channel(by_level, Prediction.channel_id, idx)):
            distribution[level] = n
        predicted, expected = db.execute(restrict_by_channel(
            select(
                func.coalesce(func.sum(case((Prediction.prob >= threshold, 1), else_=0)), 0),
                func.coalesce(func.sum(Prediction.prob), 0.0),
            ).where(Prediction.at == snap),
            Prediction.channel_id, idx,
        )).one()
    # Открыта на момент at: создана до at и либо ещё не закрыта, либо закрыта позже at
    open_orders = db.scalar(restrict_by_channel(
        select(func.count())
        .select_from(WorkOrder)
        .where(
            WorkOrder.created_at <= at,
            or_(WorkOrder.status.in_(OPEN_WORK_ORDER_STATUSES), WorkOrder.closed_at > at),
        ),
        WorkOrder.channel_id, idx,
    ))
    channels_total = select(func.count()).select_from(Channel)
    if idx.channel_objects is not None:
        channels_total = channels_total.where(Channel.object_id.in_(sorted(idx.channel_objects)))
    return schemas.DashboardSummary(
        at=at,
        snapshot_at=snap,
        model_version=current_model_version(db),
        channels_total=db.scalar(channels_total),
        channels_at_risk=sum(distribution[level] for level in ALERT_LEVELS),
        predicted_failures_24h=int(predicted),
        expected_failures_24h=round(float(expected), 1),
        open_work_orders=open_orders,
        # Кэш — с ключом области: у диспетчера района и техника свои числа
        accuracy_30d=cached(db, ("accuracy_30d", at, threshold, idx.scope_key),
                            lambda: accuracy(db, at, threshold, idx)),
        risk_distribution=distribution,
    )
