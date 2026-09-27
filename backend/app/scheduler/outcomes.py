"""Разметка исходов прогнозов по расписанию.

Исход прогноза (at, горизонт 24 ч) — был ли отказ канала в (at, at + horizon_h]. Отказы — разметка ML
(channel_faults: «Неисправен», «Отключено устройство», пропадание связи) и статусы-отказы во входящем потоке
(events_recent), то же правило, что у отметок на графике карточки датчика.

Размечаются только прогнозы, у которых горизонт закрылся и по реальному времени, и внутри данных:
at + horizon_h ≤ min(сейчас, последнее событие в events_recent). Иначе «отказа не было» было бы выдумкой.
Уже размеченные не трогаются (NOT EXISTS + ON CONFLICT DO NOTHING), работа идёт батчами по id.
На реальных данных исходы загружены из ML, поэтому задание ничего не делает.
"""

import bisect
import logging
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app import cache
from app.config import get_settings
from app.dbcompat import upsert
from app.importer import FAULT_STATUSES
from app.models import ChannelFault, EventRecent, Prediction, PredictionOutcome
from app.timeutil import now_msk

log = logging.getLogger(__name__)

MAX_HORIZON_H = 24


def _faults(db: Session, channel_ids: set[int], t_from: datetime, t_to: datetime) -> dict[int, list[tuple]]:
    """Отказы каналов в (t_from, t_to]: {канал: [(время, вид), …] по времени}."""
    by_channel: dict[int, set[tuple]] = defaultdict(set)
    ids = list(channel_ids)
    for i in range(0, len(ids), 1000):
        chunk = ids[i : i + 1000]
        for ch, ts, kind in db.execute(
            select(ChannelFault.channel_id, ChannelFault.ts, ChannelFault.kind)
            .where(ChannelFault.channel_id.in_(chunk), ChannelFault.ts > t_from, ChannelFault.ts <= t_to)
        ):
            by_channel[ch].add((ts, kind))
        for ch, ts, kind in db.execute(
            select(EventRecent.channel_id, EventRecent.ts, EventRecent.value_text)
            .where(EventRecent.channel_id.in_(chunk), EventRecent.ts > t_from, EventRecent.ts <= t_to,
                   EventRecent.value_text.in_(FAULT_STATUSES))
        ):
            by_channel[ch].add((ts, kind))
    return {ch: sorted(v) for ch, v in by_channel.items()}


def label_outcomes(db: Session, now: datetime | None = None) -> int:
    """Размечает исходы прогнозов с закрытым горизонтом. Возвращает число новых строк prediction_outcomes."""
    settings = get_settings()
    data_end = db.scalar(select(func.max(EventRecent.ts)))
    if data_end is None:
        return 0
    known_until = min(now or now_msk(), data_end)
    cutoff = known_until - timedelta(hours=MAX_HORIZON_H)
    window_from = cutoff - timedelta(days=settings.outcomes_lookback_days)
    total, last_id = 0, 0
    while True:
        rows = db.execute(
            select(Prediction.id, Prediction.channel_id, Prediction.at, Prediction.horizon_h)
            .where(
                Prediction.at > window_from,
                Prediction.at <= cutoff,
                Prediction.id > last_id,
                ~exists().where(PredictionOutcome.prediction_id == Prediction.id),
            )
            .order_by(Prediction.id)
            .limit(settings.outcomes_batch_size)
        ).all()
        if not rows:
            break
        last_id = rows[-1].id
        faults = _faults(db, {r.channel_id for r in rows}, min(r.at for r in rows),
                         max(r.at + timedelta(hours=r.horizon_h) for r in rows))
        labeled_at = now_msk()
        outcomes = []
        for r in rows:
            end = r.at + timedelta(hours=r.horizon_h)
            if end > known_until:
                continue
            marks = faults.get(r.channel_id, [])
            i = bisect.bisect_right(marks, (r.at, "￿"))  # первый отказ строго после at
            hit = marks[i] if i < len(marks) and marks[i][0] <= end else None
            outcomes.append({
                "prediction_id": r.id, "happened": hit is not None,
                "fault_at": hit[0] if hit else None, "fault_kind": hit[1] if hit else None, "labeled_at": labeled_at,
            })
        total += upsert(db, PredictionOutcome.__table__, outcomes, ["prediction_id"])
        db.commit()
    if total:
        cache.bump()
        log.info("Разметка исходов: %d новых (горизонт закрыт до %s)", total, known_until)
    return total
