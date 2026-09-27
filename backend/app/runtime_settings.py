"""Настраиваемые параметры: администратор меняет их в интерфейсе («Настройки»), перезапуск не нужен.

Хранятся одной строкой таблицы app_settings (ключ «runtime»); пока строки нет — значения по умолчанию.
Читаются часто (уровень риска нужен почти каждому запросу), поэтому держатся в памяти процесса и перечитываются
из БД не реже раза в RELOAD_SECONDS; сохранение через API сбрасывает их сразу.
"""

import logging
import threading
import time
from datetime import datetime

from pydantic import BaseModel, Field, model_validator
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

KEY = "runtime"


def _env():
    from app.config import get_settings

    return get_settings()
RELOAD_SECONDS = 5.0


class RuntimeSettings(BaseModel):
    # Границы уровней риска по вероятности отказа: «внимание» от, «риск» от, «критично» от.
    # По умолчанию — те же, что у ML по индексу здоровья (80 / 50 / 20 = вероятность 0,2 / 0,5 / 0,8)
    risk_attention: float = Field(0.2, gt=0, lt=1, description="«Внимание» — вероятность не ниже")
    risk_risk: float = Field(0.5, gt=0, lt=1, description="«Риск» — вероятность не ниже")
    risk_critical: float = Field(0.8, gt=0, lt=1, description="«Критично» — вероятность не ниже")
    # Уведомления о новых переходах в «Критично»
    notify_cooldown_hours: int = Field(24, ge=1, le=168, description="Повторно по тому же датчику — не чаще, часов")
    notify_per_slice: int = Field(3, ge=1, le=20, description="Лента и колокольчик: отдельных уведомлений на срез, остальные — сводкой")
    notify_sim_per_slice: int = Field(
        default_factory=lambda: _env().sim_max_notifications_per_slice, ge=1, le=50,
        description="Всплывающие в симуляции: отдельных на срез, остальные — сводкой (по умолчанию SIM_MAX_NOTIFICATIONS_PER_SLICE)",
    )

    @model_validator(mode="after")
    def _ordered(self):
        if not self.risk_attention < self.risk_risk < self.risk_critical:
            raise ValueError("Границы должны возрастать: «внимание» < «риск» < «критично»")
        return self

    @property
    def custom_risk_bounds(self) -> bool:
        """Границы менялись: уровень риска пересчитывается от вероятности; иначе — уровень, записанный ML."""
        d = DEFAULTS
        return (self.risk_attention, self.risk_risk, self.risk_critical) != (d.risk_attention, d.risk_risk, d.risk_critical)


DEFAULTS = RuntimeSettings()

_lock = threading.Lock()
_loading = threading.Lock()
_cached: tuple[float, RuntimeSettings] | None = None
_meta: dict = {}


def load(db: Session) -> tuple[RuntimeSettings, dict]:
    from app.models import AppSetting

    row = db.get(AppSetting, KEY)
    if row is None:
        return DEFAULTS, {}
    try:
        return RuntimeSettings(**{**DEFAULTS.model_dump(), **(row.value or {})}), {"updated_at": row.updated_at, "updated_by": row.updated_by}
    except ValueError:
        log.exception("Настройки в app_settings не проходят проверку — использую значения по умолчанию")
        return DEFAULTS, {}


def current() -> RuntimeSettings:
    """Текущие настройки (из памяти, не старше RELOAD_SECONDS). Ошибка БД — последние известные или по умолчанию.

    Перечитывает из БД только один поток за раз, остальные сразу получают последнее значение. Иначе под нагрузкой
    каждый запрос, уже держащий соединение, просил бы второе — и пул соединений кончался (нагрузочный тест, 40
    пользователей: ожидание 30 с и 500)."""
    global _cached
    now = time.monotonic()
    with _lock:
        if _cached is not None and now - _cached[0] < RELOAD_SECONDS:
            return _cached[1]
        stale = _cached[1] if _cached is not None else None
    if not _loading.acquire(blocking=stale is None):
        return stale  # type: ignore[return-value]  # уже перечитывает другой поток
    try:
        with _lock:
            if _cached is not None and time.monotonic() - _cached[0] < RELOAD_SECONDS:
                return _cached[1]
        from app.db import SessionLocal

        try:
            with SessionLocal() as db:
                value, meta = load(db)
        except SQLAlchemyError:
            log.warning("Не удалось прочитать настройки из БД — использую последние известные")
            value, meta = (stale or DEFAULTS), {}
        with _lock:
            _cached = (time.monotonic(), value)
            _meta.clear()
            _meta.update(meta)
        return value
    finally:
        _loading.release()


def save(db: Session, value: RuntimeSettings, username: str | None, now: datetime) -> None:
    from app.models import AppSetting

    row = db.get(AppSetting, KEY)
    if row is None:
        row = AppSetting(key=KEY, value={}, updated_at=now)
        db.add(row)
    row.value = value.model_dump()
    row.updated_at = now
    row.updated_by = username
    reset()


def reset() -> None:
    global _cached
    with _lock:
        _cached = None
