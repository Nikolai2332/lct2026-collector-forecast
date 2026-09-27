"""Симуляция потока событий (вариант Б): проигрывание суток по заранее рассчитанным прогнозам.

Это НЕ онлайн-пересчёт моделью. Прогнозы и исходы уже лежат в БД; симуляция их только читает и двигает
«модельные часы»: model_time = model_start + (сейчас − started_at) × speed. Пока симуляция активна,
запросы без явного `at` работают на модельный момент (app.main.SimMomentMiddleware → app.deps.at_param),
исходы с незакрытым на этот момент горизонтом скрываются (app.services.outcome_out).

Состояние — строка sim_state в БД (id = 1) и её копия в памяти процесса для быстрых проверок в каждом запросе.
Процесс API один (uvicorn без --workers), поэтому копия всегда совпадает с БД: меняет её только этот процесс.
Защита от двух одновременных запусков — условный UPDATE … WHERE active = false в БД.

Такт (tick) выполняет планировщик раз в секунду и GET /api/sim/state: когда модельное время переходит
в новый срез прогнозов, новые критические прогнозы уходят в SSE, и рассылается служебное событие sim_clock.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app import runtime_settings, schemas
from app.config import get_settings
from app.models import Prediction, SimState
from app.notifications import broker, critical_transitions, slice_notifications
from app.services import snapshot_at
from app.timeutil import MSK

log = logging.getLogger(__name__)

NOTE = "Воспроизведение заранее рассчитанных прогнозов; онлайн-пересчёт моделью не выполняется."
MIN_SPEED, MAX_SPEED = 1, 600


class SimError(Exception):
    """Ошибка управления симуляцией: status — HTTP-код для ответа API."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def real_now() -> datetime:
    """Реальное московское время с микросекундами — от него идут модельные часы."""
    return datetime.now(MSK).replace(tzinfo=None)


def day_end(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59))


@dataclass(frozen=True)
class SimView:
    """Копия строки sim_state, отвязанная от сессии БД."""

    active: bool
    day: date | None
    speed: int
    model_start: datetime | None
    model_time_saved: datetime | None
    started_at: datetime | None
    started_by: str | None
    stopped_at: datetime | None
    stop_reason: str | None
    last_snapshot: datetime | None
    events_received: int
    events_accepted: int
    critical_sent: int

    @classmethod
    def of(cls, row: SimState) -> "SimView":
        return cls(
            active=bool(row.active), day=row.day, speed=int(row.speed or 60), model_start=row.model_start,
            model_time_saved=row.model_time, started_at=row.started_at, started_by=row.started_by,
            stopped_at=row.stopped_at, stop_reason=row.stop_reason, last_snapshot=row.last_snapshot,
            events_received=row.events_received or 0, events_accepted=row.events_accepted or 0,
            critical_sent=row.critical_sent or 0,
        )

    def model_time(self, now: datetime | None = None) -> datetime | None:
        """Текущее модельное время; после конца суток стоит на 23:59:59."""
        if not self.active or self.model_start is None or self.started_at is None or self.day is None:
            return self.model_time_saved
        elapsed = max(timedelta(0), (now or real_now()) - self.started_at)
        return min(self.model_start + elapsed * self.speed, day_end(self.day)).replace(microsecond=0)


_INACTIVE = SimView(False, None, 60, None, None, None, None, None, None, None, 0, 0, 0)
_view: SimView = _INACTIVE
_view_lock = threading.Lock()
_tick_lock = threading.Lock()


def _set_view(view: SimView) -> None:
    global _view
    with _view_lock:
        _view = view


def current() -> SimView:
    return _view


def model_now() -> datetime | None:
    """Модельное время, если симуляция активна и включена; иначе None. Без обращения к БД."""
    view = _view
    if not view.active or not get_settings().sim_on:
        return None
    return view.model_time()


def _row(db: Session) -> SimState:
    row = db.get(SimState, 1)
    if row is None:  # в миграции строка создаётся; здесь — для схемы из metadata.create_all (тесты)
        row = SimState(id=1, active=False, speed=60, events_received=0, events_accepted=0, critical_sent=0)
        db.add(row)
        db.flush()
    return row


def load(db: Session) -> SimView:
    """Перечитать состояние из БД в память."""
    db.expire_all()
    view = SimView.of(_row(db))
    _set_view(view)
    return view


def reset_on_startup(db: Session) -> None:
    """После перезапуска API «активной» симуляции быть не может: часы и подписчики SSE жили в старом процессе."""
    row = _row(db)
    if row.active:
        log.warning("Симуляция за %s была активна до перезапуска — сбрасываю", row.day)
        row.active = False
        row.stopped_at = real_now().replace(microsecond=0)
        row.stop_reason = "restart"
    db.commit()
    load(db)


# ---------- Доступные сутки ----------


def available_range(db: Session) -> tuple[date | None, date | None]:
    lo, hi = db.execute(select(func.min(Prediction.at), func.max(Prediction.at))).one()
    return (lo.date() if lo else None, hi.date() if hi else None)


def default_day(db: Session) -> date | None:
    """Последние полные сутки с прогнозами: последний срез дня — не раньше 18:00 (подходит и почасовым
    реальным прогнозам, и демо-срезам раз в 6 ч)."""
    hi = db.scalar(select(func.max(Prediction.at)))
    return (hi - timedelta(hours=18)).date() if hi else None


def day_has_predictions(db: Session, day: date) -> bool:
    start = datetime.combine(day, time())
    return db.scalar(
        select(Prediction.id).where(Prediction.at >= start, Prediction.at < start + timedelta(days=1)).limit(1)
    ) is not None


# ---------- Запуск и остановка (коммитит вызывающий код вместе с записью в audit_log) ----------


def start(db: Session, day: date, speed: int, username: str) -> None:
    if not get_settings().sim_on:
        raise SimError(403, "Симуляция выключена на этом сервере (SIM_ENABLED=false)")
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise SimError(422, f"Скорость — от ×{MIN_SPEED} до ×{MAX_SPEED}")
    if not day_has_predictions(db, day):
        lo, hi = available_range(db)
        hint = f" Доступны сутки с {lo:%d.%m.%Y} по {hi:%d.%m.%Y}." if lo and hi else " В базе нет прогнозов."
        raise SimError(422, f"За {day:%d.%m.%Y} в базе нет прогнозов.{hint}")
    _row(db)
    model_start = datetime.combine(day, time())
    result = db.execute(
        update(SimState)
        .where(SimState.id == 1, SimState.active.is_(False))
        .values(
            active=True, day=day, speed=speed, model_start=model_start, model_time=model_start,
            started_at=real_now(), started_by=username[:64], stopped_at=None, stop_reason=None,
            # Срез на старте не рассылаем: клиенты получат его событием snapshot при переподключении
            last_snapshot=snapshot_at(db, model_start),
            events_received=0, events_accepted=0, critical_sent=0,
        )
    )
    if result.rowcount == 0:
        raise SimError(409, "Симуляция уже идёт. Остановите её, прежде чем запускать новую.")


def stop(db: Session, reason: str = "manual") -> bool:
    """True — симуляция была активна и остановлена."""
    view = _view
    values = {"active": False, "stopped_at": real_now().replace(microsecond=0), "stop_reason": reason[:32]}
    if view.active:
        values["model_time"] = view.model_time()
    result = db.execute(update(SimState).where(SimState.id == 1, SimState.active.is_(True)).values(**values))
    return result.rowcount > 0


def record_ingest(db: Session, received: int, accepted: int) -> None:
    """Счётчики событий, принятых во время симуляции (в той же транзакции, что и вставка)."""
    if not _view.active:
        return
    db.execute(
        update(SimState)
        .where(SimState.id == 1, SimState.active.is_(True))
        .values(
            events_received=SimState.events_received + received,
            events_accepted=SimState.events_accepted + accepted,
        )
    )


def after_change(db: Session) -> SimView:
    """После commit запуска или остановки: обновить копию в памяти и сообщить клиентам."""
    view = load(db)
    publish_clock(view)
    return view


# ---------- Такт: уведомления по новым срезам ----------


def publish_clock(view: SimView, snapshot: datetime | None = None) -> None:
    broker.publish_event("sim_clock", {
        "active": view.active,
        "day": view.day.isoformat() if view.day else None,
        "speed": view.speed,
        "model_time": view.model_time().isoformat() if view.model_time() else None,
        "snapshot_at": (snapshot or view.last_snapshot).isoformat() if (snapshot or view.last_snapshot) else None,
    })


def new_critical_ids(db: Session, snap: datetime) -> list[int]:
    """Новые критичные прогнозы среза (канал не был критичным 24 ч до него); по убыванию вероятности.

    Та же логика, что у ленты и колокольчика (app.notifications.critical_transitions)."""
    return [t.prediction_id for t in critical_transitions(db, snap, snap)]


def publish_slice(db: Session, snap: datetime, moment: datetime) -> int:
    """Рассылает новые критичные прогнозы среза: до N по отдельности, остальные — одним сводным уведомлением.

    Возвращает число разосланных уведомлений."""
    limit = max(1, runtime_settings.current().notify_sim_per_slice)
    return broker.publish_scoped(
        lambda scope: slice_notifications(db, critical_transitions(db, snap, snap, scope), moment, per_slice=limit)
    )


def tick(db: Session) -> SimView:
    """Один такт: новый срез → уведомления и sim_clock; конец суток → остановка. Безопасно звать параллельно."""
    if not _tick_lock.acquire(blocking=False):
        return _view
    try:
        view = _view
        if not view.active or view.day is None:
            return view
        moment = view.model_time()
        snap = snapshot_at(db, moment)
        changed = snap is not None and snap != view.last_snapshot
        sent = publish_slice(db, snap, moment) if changed else 0
        finished = moment >= day_end(view.day)
        values: dict = {"model_time": moment, "last_snapshot": snap if snap is not None else view.last_snapshot}
        if sent:
            values["critical_sent"] = SimState.critical_sent + sent
        if finished:
            values.update(active=False, stopped_at=real_now().replace(microsecond=0), stop_reason="finished")
        # started_at в условии: такт не должен задеть симуляцию, запущенную заново, пока он шёл
        db.execute(
            update(SimState)
            .where(SimState.id == 1, SimState.active.is_(True), SimState.started_at == view.started_at)
            .values(**values)
        )
        db.commit()
        new_view = load(db)
        if changed or finished:
            publish_clock(new_view, snap)
        if finished:
            log.info("Симуляция за %s завершена: модельные сутки прошли", view.day)
        return new_view
    finally:
        _tick_lock.release()


def state_out(db: Session) -> schemas.SimStateOut:
    view = _view
    lo, hi = available_range(db)
    moment = view.model_time() if view.active else view.model_time_saved
    return schemas.SimStateOut(
        enabled=get_settings().sim_on,
        active=view.active,
        day=view.day,
        speed=view.speed,
        model_time=moment,
        snapshot_at=view.last_snapshot,
        started_at=view.started_at.replace(microsecond=0) if view.started_at else None,
        started_by=view.started_by,
        stopped_at=view.stopped_at,
        stop_reason=view.stop_reason,
        events_received=view.events_received,
        events_accepted=view.events_accepted,
        critical_sent=view.critical_sent,
        available_from=lo,
        available_to=hi,
        default_day=default_day(db),
        note=NOTE,
    )
