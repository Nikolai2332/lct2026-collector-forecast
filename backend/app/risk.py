"""Уровень риска прогноза при выдаче.

Пока администратор не менял границы («Настройки»), уровень — тот, что записал ML (по индексу здоровья
80 / 50 / 20, ТЗ бэкенда): так контракт с ML и индекс ix_predictions_at_risk работают как раньше.
После изменения границ уровень пересчитывается от вероятности `prob` при каждом запросе — и в SQL (фильтры,
группировки), и в ответах; таблица прогнозов не меняется. Фильтр по уровню становится диапазоном по prob —
его обслуживает индекс ix_predictions_at_prob.
"""

from sqlalchemy import ColumnElement, and_, case, false, or_

from app.models import Prediction
from app.runtime_settings import RuntimeSettings, current

LEVELS = ("normal", "attention", "risk", "critical")


def _bounds(s: RuntimeSettings) -> dict[str, tuple[float | None, float | None]]:
    """Уровень → [нижняя граница, верхняя граница) по вероятности."""
    return {
        "normal": (None, s.risk_attention),
        "attention": (s.risk_attention, s.risk_risk),
        "risk": (s.risk_risk, s.risk_critical),
        "critical": (s.risk_critical, None),
    }


def level_from_prob(prob: float, s: RuntimeSettings | None = None) -> str:
    s = s or current()
    if prob >= s.risk_critical:
        return "critical"
    if prob >= s.risk_risk:
        return "risk"
    if prob >= s.risk_attention:
        return "attention"
    return "normal"


def level_of(p: Prediction, s: RuntimeSettings | None = None) -> str:
    s = s or current()
    return level_from_prob(p.prob, s) if s.custom_risk_bounds else p.risk_level


def level_expr(s: RuntimeSettings | None = None) -> ColumnElement:
    """Уровень риска как выражение SQL (для GROUP BY). Для строк без прогноза (outer join) — NULL."""
    s = s or current()
    if not s.custom_risk_bounds:
        return Prediction.risk_level
    return case(
        (Prediction.prob.is_(None), None),
        (Prediction.prob >= s.risk_critical, "critical"),
        (Prediction.prob >= s.risk_risk, "risk"),
        (Prediction.prob >= s.risk_attention, "attention"),
        else_="normal",
    )


def level_in(levels, s: RuntimeSettings | None = None) -> ColumnElement:
    """Условие «уровень риска из списка» для WHERE."""
    s = s or current()
    levels = [lvl for lvl in levels if lvl in LEVELS]
    if not s.custom_risk_bounds:
        return Prediction.risk_level.in_(levels)
    if not levels:
        return false()
    bounds = _bounds(s)
    parts = []
    for lvl in levels:
        lo, hi = bounds[lvl]
        cond = []
        if lo is not None:
            cond.append(Prediction.prob >= lo)
        if hi is not None:
            cond.append(Prediction.prob < hi)
        parts.append(and_(*cond))
    return or_(*parts)
