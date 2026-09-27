"""Расчёт по расписанию: APScheduler в процессе API.

Задания:
- sim_tick — такт симуляции потока (раз в секунду): новые срезы → уведомления SSE, конец суток → остановка;
- label_outcomes — разметка исходов прогнозов с закрытым горизонтом (app.scheduler.outcomes);
- warm_cache — один раз при старте: считает в кэш тяжёлые агрегаты для экрана «Последние данные» (app.cache),
  в том числе «Рекомендованные работы».

Онлайн-пересчёта прогнозов моделью здесь нет: прогнозы рассчитывает ML-конвейер заранее (ml/, docs/ARCHITECTURE.md).
Резервную копию БД делает отдельный контейнер backup (docker-compose.prod.yml), здесь не дублируется.

Ограничение: планировщик живёт в процессе uvicorn. API запускается одним процессом; при нескольких воркерах
задания выполнялись бы в каждом (для этого понадобится отдельный процесс-планировщик или блокировка в БД).
"""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from app import db as app_db
from app import sim
from app.config import Settings
from app.scheduler.outcomes import label_outcomes

log = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def run_job(name: str, job: Callable[[Session], object]) -> None:
    """Своя сессия БД на запуск; ошибка задания пишется в лог и не роняет API."""
    try:
        with app_db.SessionLocal() as db:
            job(db)
    except Exception:
        log.exception("Задание %s завершилось с ошибкой", name)


def _sim_tick() -> None:
    if sim.current().active:
        run_job("sim_tick", sim.tick)


def _label_outcomes() -> None:
    run_job("label_outcomes", label_outcomes)


def warm_cache(db: Session) -> None:
    """Дашборд и «Качество модели» для момента по умолчанию — как его выбирает фронтенд (DataMomentProvider):
    если данные старше суток, «Сейчас» = последний срез − 24 ч. Иначе прогревать нечего: момент всё время новый."""
    from sqlalchemy import func, select

    from app.api.dashboard import accuracy
    from app.api.model import _daily_facts
    from app.cache import cached
    from app.models import Prediction
    from app.access import FULL_ACCESS
    from app.services import ObjectIndex, working_threshold
    from app.timeutil import now_msk

    snap = db.scalar(select(func.max(Prediction.at)))
    if snap is None or now_msk() - snap <= timedelta(hours=24):
        return
    at = (snap - timedelta(hours=24)).replace(minute=0, second=0, microsecond=0)
    threshold = working_threshold(db)
    # Прогрев — для полной области (ОДС, администратор); у ролей с областью свои ключи, считаются по запросу
    idx = ObjectIndex.load(db, FULL_ACCESS)
    cached(db, ("accuracy_30d", at, threshold, None), lambda: accuracy(db, at, threshold, idx))
    day = at.date()
    cached(db, ("daily_facts", day - timedelta(days=30), day, threshold, None),
           lambda: _daily_facts(db, day - timedelta(days=30), day, threshold, idx))
    # «Рекомендованные работы» — на последний срез не позже этого момента
    from app.api.maintenance import plan_groups
    from app.services import snapshot_at

    plan_snap = snapshot_at(db, at)
    if plan_snap is not None:
        plan_groups(db, plan_snap, None, idx)
    log.info("Кэш прогрет для момента %s", at)


def start(settings: Settings) -> BackgroundScheduler | None:
    global _scheduler
    if not settings.scheduler_enabled:
        log.info("Планировщик выключен (SCHEDULER_ENABLED=false): такт симуляции — только при опросе /api/sim/state")
        return None
    if _scheduler is not None:
        return _scheduler
    scheduler = BackgroundScheduler(
        timezone=timezone.utc, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 30}
    )
    if settings.sim_on:
        scheduler.add_job(_sim_tick, "interval", seconds=settings.sim_tick_seconds, id="sim_tick")
    scheduler.add_job(
        _label_outcomes, "interval", minutes=settings.outcomes_interval_minutes, id="label_outcomes",
        next_run_time=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    scheduler.add_job(lambda: run_job("warm_cache", warm_cache), "date", id="warm_cache",
                      run_date=datetime.now(timezone.utc) + timedelta(seconds=2))
    scheduler.start()
    _scheduler = scheduler
    log.info("Планировщик запущен: %s", ", ".join(j.id for j in scheduler.get_jobs()))
    return scheduler


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
