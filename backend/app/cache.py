"""Кэш тяжёлых агрегатов по истории прогнозов (точность за 30 дней, «прогноз против факта» по дням).

Эти числа считаются по миллионам строк predictions × prediction_outcomes (на реальных данных 2,5–5 с), а меняются
только когда меняются сами прогнозы или исходы. Поэтому ключ кэша — параметры запроса плюс «версия данных»:
- max(id) прогнозов и max(prediction_id) исходов — дешёвые запросы по первичным ключам; ловят и загрузку
  данных в обход API (ETL `db.load_postgres`);
- счётчик изменений процесса — `bump()` после импорта, разметки исходов и сида.
TTL — страховка на случай правки строк без новых id. Процесс API один (docs/ARCHITECTURE.md), кэш — в его памяти.
Решения и заявки в эти агрегаты не входят и кэш не сбрасывают.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import TypeVar

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Prediction, PredictionOutcome

T = TypeVar("T")

TTL_SECONDS = 15 * 60
MAX_ENTRIES = 512

_lock = threading.Lock()
_entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()
_generation = 0


def bump() -> None:
    """Данные изменились — всё посчитанное раньше больше не годится."""
    global _generation
    with _lock:
        _generation += 1
        _entries.clear()


def data_version(db: Session) -> tuple:
    return (
        _generation,
        db.scalar(select(func.max(Prediction.id))),
        db.scalar(select(func.max(PredictionOutcome.prediction_id))),
    )


def cached(db: Session, key: Hashable, compute: Callable[[], T]) -> T:
    full_key = (key, data_version(db))
    now = time.monotonic()
    with _lock:
        hit = _entries.get(full_key)
        if hit is not None and now - hit[0] < TTL_SECONDS:
            _entries.move_to_end(full_key)
            return hit[1]  # type: ignore[return-value]
    value = compute()  # считаем без блокировки: два одинаковых запроса разом просто посчитают дважды
    with _lock:
        _entries[full_key] = (now, value)
        _entries.move_to_end(full_key)
        while len(_entries) > MAX_ENTRIES:
            _entries.popitem(last=False)
    return value


def clear() -> None:
    with _lock:
        _entries.clear()
