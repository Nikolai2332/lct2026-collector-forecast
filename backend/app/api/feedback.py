"""Выгрузка решений диспетчера для дообучения (общий ТЗ, сценарии п. 12: «сохранение решения для анализа и дообучения»).

Строка — решение по прогнозу вместе с тем, что предсказала модель и что случилось на самом деле. Это набор
обратной связи для конвейера ml/: какие тревоги диспетчеры считают ложными (причины из справочника), где модель
ошибается систематически. Автоматического переобучения нет (docs/SIMULATED_DECISIONS.md, «Как решения замыкают цикл»).
"""

import csv
import io
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access import Access, require
from app.api.journal import excel_safe
from app.audit import audit
from app.db import get_db
from app.deps import at_param
from app.models import Channel, Decision, Prediction, PredictionOutcome, Reason, User
from app.security import get_current_user
from app.services import ObjectIndex, apply_channel_filters

router = APIRouter(tags=["Журнал прогнозов"])

COLUMNS = ["decision_id", "prediction_id", "channel_id", "sensor_type", "system_type", "object_id", "prediction_at",
           "prob", "risk_level", "model_version", "decision_type", "reason_code", "reason", "source", "decided_at",
           "reaction_min", "outcome_happened", "fault_at", "fault_kind"]


@router.get(
    "/api/export/decisions",
    summary="Решения диспетчера с прогнозом и исходом — набор для дообучения (CSV)",
    description=(
        "Каждая строка — решение (`source`: user — принято в интерфейсе, simulation — смоделировано), вероятность и "
        "уровень риска прогноза, версия модели, реакция в минутах и исход (сбылся ли отказ за 24 ч). Период — по "
        "времени прогноза, до 92 дней. Только датчики области пользователя; выгрузка пишется в журнал действий."
    ),
    response_class=StreamingResponse,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV в UTF-8 с BOM (открывается в Excel)"}},
)
def export_decisions(
    request: Request,
    date_from: datetime = Query(..., description="Начало периода (время прогноза)"),
    date_to: datetime = Query(..., description="Конец периода"),
    source: Literal["all", "user", "simulation"] = Query("all"),
    at=Depends(at_param),
    access: Access = Depends(require("export")),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if date_from > date_to or date_to - date_from > timedelta(days=92):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Период — от 1 до 92 дней, начало раньше конца")
    idx = ObjectIndex.load(db, access)
    stmt = (
        select(Decision, Prediction, Channel, Reason.code, PredictionOutcome)
        .join(Prediction, Prediction.id == Decision.prediction_id)
        .join(Channel, Channel.id == Prediction.channel_id)
        .join(Reason, Reason.id == Decision.reason_id)
        .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Prediction.id)
        .where(Prediction.at >= date_from, Prediction.at <= date_to, Decision.created_at <= at)
        .order_by(Prediction.at, Decision.id)
    )
    stmt = apply_channel_filters(stmt, idx)
    if source != "all":
        stmt = stmt.where(Decision.source == source)
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    w.writerow(COLUMNS)
    rows = 0
    for d, p, ch, code, o in db.execute(stmt).unique():
        rows += 1
        # Строки — из импорта и справочников: то, что Excel принял бы за формулу, пишется как текст (S-07, S-30)
        w.writerow([excel_safe(v) for v in [
            d.id, p.id, ch.id, ch.sensor_type, ch.system_type, ch.object_id, p.at.isoformat(), round(p.prob, 4),
            p.risk_level, p.model_version, d.decision_type, code, d.reason.name, d.source, d.created_at.isoformat(),
            round((d.created_at - p.at).total_seconds() / 60, 1),
            "" if o is None else int(o.happened), o.fault_at.isoformat() if o and o.fault_at else "",
            o.fault_kind if o and o.fault_kind else "",
        ]])
    audit(db, request, user, "export_decisions", "decisions",
          details={"rows": rows, "from": date_from.isoformat(), "to": date_to.isoformat(), "source": source})
    db.commit()
    return StreamingResponse(io.BytesIO(buf.getvalue().encode("utf-8")), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="decisions_{date_from:%Y%m%d}_{date_to:%Y%m%d}.csv"'})
