from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import schemas, sim
from app.audit import audit
from app.access import require_user
from app.db import get_db
from app.models import User
from app.security import get_current_user

router = APIRouter(prefix="/api/sim", tags=["Симуляция потока"])

SIM_NOTE = (
    "Симуляция проигрывает выбранные сутки по **заранее рассчитанным** прогнозам: модельные часы идут с ускорением, "
    "на каждом новом часовом срезе новые критические прогнозы уходят в SSE. Онлайн-пересчёт моделью не выполняется. "
)


def _raise(e: sim.SimError):
    raise HTTPException(e.status, e.message)


@router.get(
    "/state",
    response_model=schemas.SimStateOut,
    summary="Состояние симуляции: модельное время, скорость, доступные сутки",
    description=SIM_NOTE + "Читать может любая роль — нужно для метки «Симуляция» в интерфейсе. "
    "Вызов заодно выполняет такт симуляции (на случай, если планировщик выключен).",
)
def state(_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if sim.current().active:
        sim.tick(db)
    return sim.state_out(db)


@router.post(
    "/start",
    response_model=schemas.SimStateOut,
    summary="Запустить симуляцию суток",
    description=SIM_NOTE + "Пока симуляция идёт, запросы без `at` работают на модельное время, исходы с незакрытым "
    "горизонтом скрыты. Одна симуляция за раз (409), сутки — только с прогнозами (422), скорость ×1…×600. "
    "Право sim_control: диспетчер ОДС, инженер данных, администратор. Выключено при SIM_ENABLED=false (403).",
    responses={403: {"model": schemas.ErrorResponse}, 409: {"model": schemas.ErrorResponse},
               422: {"model": schemas.ErrorResponse}},
)
def start(
    body: schemas.SimStartIn,
    request: Request,
    user: User = Depends(require_user("sim_control")),
    db: Session = Depends(get_db),
):
    try:
        sim.start(db, body.day, body.speed, user.username)
    except sim.SimError as e:
        db.rollback()
        _raise(e)
    audit(db, request, user, "sim_start", "sim_state", 1, {"day": body.day.isoformat(), "speed": body.speed})
    db.commit()
    sim.after_change(db)
    return sim.state_out(db)


@router.post(
    "/stop",
    response_model=schemas.SimStateOut,
    summary="Остановить симуляцию",
    description="Идемпотентно: если симуляция не идёт, возвращает состояние без изменений. Право sim_control.",
)
def stop(
    request: Request,
    user: User = Depends(require_user("sim_control")),
    db: Session = Depends(get_db),
):
    view = sim.current()
    if sim.stop(db, "manual"):
        audit(db, request, user, "sim_stop", "sim_state", 1, {
            "day": view.day.isoformat() if view.day else None,
            "model_time": view.model_time().isoformat() if view.model_time() else None,
        })
        db.commit()
        sim.after_change(db)
    else:
        db.rollback()
    return sim.state_out(db)
