"""Время. Журналы заказчика пишут московское время без пояса, поэтому в БД — naive datetime по Москве."""

from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))  # в Москве нет перехода на летнее время

# Модельное время симуляции для текущего запроса без явного at (ставит app.main.SimMomentMiddleware).
# None — симуляции нет или задан at («машина времени» важнее симуляции)
sim_moment: ContextVar[datetime | None] = ContextVar("sim_moment", default=None)


def now_msk() -> datetime:
    return datetime.now(MSK).replace(tzinfo=None, microsecond=0)


def to_msk_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        value = value.astimezone(MSK).replace(tzinfo=None)
    return value.replace(microsecond=0)


def resolve_at(at: datetime | None) -> datetime:
    """Момент «машины времени»: переданный at или текущее время."""
    return to_msk_naive(at) if at is not None else now_msk()
