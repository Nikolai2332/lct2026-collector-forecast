from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access
from app.cache import cached
from app.db import get_db
from app.dbcompat import as_date, day_of
from app.deps import at_param
from app.models import ModelMetric, ModelThreshold, Prediction, PredictionOutcome
from app.security import get_current_user
from app.services import ObjectIndex, current_model_version, restrict_by_channel, snapshot_at, working_threshold

router = APIRouter(prefix="/api/model", tags=["Качество модели"], dependencies=[Depends(get_current_user)])

MAX_PERIOD_DAYS = 92
GROUP_ORDER = ["газовые", "пожарные", "охранные", "прочие"]
KIND_ORDER = ["Пропадание связи", "Неисправен", "Отключено устройство", "Сбой значения"]
LABEL_NOTE = (
    "С версии v3 отказом не считаются плановые работы и отключения целых объектов (когда одновременно молчит "
    "большинство однотипных датчиков объекта), а также дни, когда в журнале нет данных. Отдельным видом отказа "
    "считается «Сбой значения» — дата «01.01.1970» или аномальное значение газа вместо показания. Так метку "
    "определили ответы заказчика 17–18.09 и 23.09.2026. Поэтому метрики v3 сравниваются с v2 на одной и той же "
    "метке: строки «v2» и «v3» ниже."
)


def _threshold_rows(db: Session, version: str | None) -> list[ModelThreshold]:
    if version is None:
        return []
    return db.scalars(
        select(ModelThreshold).where(ModelThreshold.model_version == version).order_by(ModelThreshold.threshold)
    ).all()


def _metric_values(m: ModelMetric | None) -> schemas.MetricValues | None:
    if m is None:
        return None
    return schemas.MetricValues(precision=m.precision, recall=m.recall, f1=m.f1, pr_auc=m.pr_auc, support=m.support)


def _daily_facts(db: Session, date_from: date, date_to: date, threshold: float, idx: ObjectIndex) -> list[schemas.DailyFact]:
    """«Прогноз против факта» по дням, в разрезе «датчик × сутки», — по датчикам области пользователя."""
    day = day_of(Prediction.at)
    flagged = Prediction.prob >= threshold
    happened = PredictionOutcome.happened.is_(True)
    # Внутренний запрос сводит срезы внутри суток до одной строки на датчик
    per_channel = restrict_by_channel(
        select(
            day.label("day"),
            Prediction.channel_id,
            func.max(case((flagged, 1), else_=0)).label("pred"),
            func.max(case((happened, 1), else_=0)).label("fact"),
            func.max(case((and_(flagged, happened), 1), else_=0)).label("tp"),
        )
        .select_from(Prediction)
        .join(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(
            Prediction.at >= datetime.combine(date_from, time.min),
            Prediction.at < datetime.combine(date_to + timedelta(days=1), time.min),
        )
        .group_by(day, Prediction.channel_id),
        Prediction.channel_id, idx,
    ).subquery()
    rows = db.execute(
        select(per_channel.c.day, func.sum(per_channel.c.pred), func.sum(per_channel.c.fact), func.sum(per_channel.c.tp))
        .group_by(per_channel.c.day)
        .order_by(per_channel.c.day)
    ).all()
    return [
        schemas.DailyFact(date=as_date(d), predicted=int(p or 0), actual=int(f or 0), true_positive=int(t or 0))
        for d, p, f, t in rows
    ]


@router.get("/metrics", response_model=schemas.ModelMetrics, summary="Метрики модели и «прогноз против факта»")
def model_metrics(
    date_from: date | None = Query(None, description="Начало периода для графика по дням; по умолчанию конец − 30 дней"),
    date_to: date | None = Query(None, description="Конец периода; по умолчанию дата последнего среза не позже at"),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    date_to = date_to or (snapshot_at(db, at) or at).date()
    date_from = date_from or date_to - timedelta(days=30)
    if date_from > date_to:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Начало периода позже конца")
    if (date_to - date_from).days > MAX_PERIOD_DAYS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Период не может быть длиннее {MAX_PERIOD_DAYS} дней")

    version = current_model_version(db)
    metrics = db.scalars(select(ModelMetric).where(ModelMetric.model_version == version)).all() if version else []
    by_scope: dict[str, list[ModelMetric]] = {}
    for m in metrics:
        by_scope.setdefault(m.scope, []).append(m)
    overall = (by_scope.get("overall") or [None])[0]
    lead = (by_scope.get("lead_time") or [None])[0]
    threshold = working_threshold(db)

    return schemas.ModelMetrics(
        at=at,
        model_version=version,
        threshold=threshold if version else None,
        test_period_from=overall.period_from if overall else None,
        test_period_to=overall.period_to if overall else None,
        overall=_metric_values(overall),
        baseline=_metric_values((by_scope.get("baseline") or [None])[0]),
        by_sensor_type=[
            schemas.SensorTypeMetrics(sensor_type=m.key, **_metric_values(m).model_dump())
            for m in sorted(by_scope.get("sensor_type", []), key=lambda m: -(m.support or 0))
        ],
        precision_at_k=[
            schemas.PrecisionAtK(k=int(m.key), precision=m.value)
            for m in sorted(by_scope.get("precision_at_k", []), key=lambda m: int(m.key))
        ],
        median_lead_time_h=lead.value if lead else None,
        pr_curve=[schemas.ThresholdRow.model_validate(r) for r in _threshold_rows(db, version)],
        daily=cached(db, ("daily_facts", date_from, date_to, threshold, idx.scope_key),
                     lambda: _daily_facts(db, date_from, date_to, threshold, idx)),
        date_from=date_from,
        date_to=date_to,
        recall_by_kind=[
            schemas.KindRecall(kind=m.key, recall=m.recall, support=m.support)
            for m in sorted(by_scope.get("fault_kind", []),
                            key=lambda m: KIND_ORDER.index(m.key) if m.key in KIND_ORDER else 99)
        ],
        by_group=[
            schemas.GroupMetrics(group=m.key, alerts_per_day=m.value, **_metric_values(m).model_dump())
            for m in sorted(by_scope.get("sensor_group", []),
                            key=lambda m: GROUP_ORDER.index(m.key) if m.key in GROUP_ORDER else 99)
        ],
        comparison=[
            schemas.ModelComparison(model=m.key.split("@")[0], label=m.key.split("@")[1], precision=m.precision,
                                    recall=m.recall, f1=m.f1, pr_auc=m.pr_auc, alerts_per_day=m.value)
            for m in sorted(by_scope.get("compare", []), key=lambda m: (m.key.split("@")[1] != "b", m.key))
        ],
        label_note=LABEL_NOTE if by_scope.get("compare") else None,
    )


@router.get(
    "/thresholds",
    response_model=schemas.ThresholdTable,
    summary="Порог → Precision, Recall, тревог в сутки (для ползунка)",
)
def thresholds(at=Depends(at_param), db: Session = Depends(get_db)):
    version = current_model_version(db)
    rows = _threshold_rows(db, version)
    selected = next((r.threshold for r in rows if r.is_selected), None)
    return schemas.ThresholdTable(
        at=at,
        model_version=version,
        selected_threshold=selected,
        items=[schemas.ThresholdRow.model_validate(r) for r in rows],
    )
