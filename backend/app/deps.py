from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import HTTPException, Query, status

from app.timeutil import resolve_at, sim_moment

# Границы «машины времени»: запросы считают периоды от at (минус 30 дней и т. п.), у края календаря это OverflowError.
# Интерфейс сам спрашивает at=2100-01-01 (DataMomentProvider: «конец данных»), поэтому верхняя граница с запасом
AT_MIN, AT_MAX = datetime(2000, 1, 1), datetime(2200, 1, 1)

AT_DESCRIPTION = (
    "Момент «машины времени» (ISO 8601). Без пояса — московское время. "
    "Не задан — текущее время, а во время симуляции — модельное время."
)


def at_param(
    at: Annotated[datetime | None, Query(description=AT_DESCRIPTION, examples=["2026-08-01T12:00:00"])] = None,
) -> datetime:
    if at is None:
        moment = sim_moment.get()
        if moment is not None:
            return moment
    value = resolve_at(at)
    if not AT_MIN <= value <= AT_MAX:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Момент «машины времени» — от 2000 до 2200 года")
    return value


@dataclass
class Page:
    limit: int
    offset: int


def page_param(
    limit: Annotated[int, Query(ge=1, le=500, description="Размер страницы")] = 50,
    offset: Annotated[int, Query(ge=0, description="Сдвиг от начала")] = 0,
) -> Page:
    return Page(limit, offset)
