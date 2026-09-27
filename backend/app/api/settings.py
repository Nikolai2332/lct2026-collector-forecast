"""Настраиваемые параметры: границы уровней риска и лимиты уведомлений (без перезапуска API)."""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app import cache, runtime_settings, schemas
from app.audit import audit
from app.access import require_user
from app.db import get_db
from app.models import User
from app.runtime_settings import DEFAULTS, RuntimeSettings
from app.security import get_current_user
from app.timeutil import now_msk

router = APIRouter(prefix="/api/settings", tags=["Настройки"])


def settings_out(db: Session) -> schemas.SettingsOut:
    value, meta = runtime_settings.load(db)
    return schemas.SettingsOut(
        **value.model_dump(), custom_risk_bounds=value.custom_risk_bounds, defaults=schemas.SettingsIn(**DEFAULTS.model_dump()),
        updated_at=meta.get("updated_at"), updated_by=meta.get("updated_by"),
    )


@router.get(
    "",
    response_model=schemas.SettingsOut,
    summary="Настраиваемые параметры (любая роль — чтение)",
    dependencies=[Depends(get_current_user)],
)
def get_settings_(db: Session = Depends(get_db)):
    return settings_out(db)


@router.put(
    "",
    response_model=schemas.SettingsOut,
    summary="Изменить параметры (только администратор)",
    description=(
        "Границы уровней риска по вероятности отказа (должны возрастать) и лимиты уведомлений. Действуют сразу, "
        "перезапуск не нужен: уровень риска во всех ответах, фильтрах и счётчиках пересчитывается от `prob`, кэш "
        "агрегатов сбрасывается. Прогнозы в БД не меняются. Изменение пишется в `audit_log` (`settings_update`)."
    ),
    responses={403: {"model": schemas.ErrorResponse}},
)
def put_settings(
    body: schemas.SettingsIn,
    request: Request,
    user: User = Depends(require_user("settings")),
    db: Session = Depends(get_db),
):
    old, _ = runtime_settings.load(db)
    new = RuntimeSettings(**body.model_dump())
    changes = {k: [getattr(old, k), v] for k, v in new.model_dump().items() if getattr(old, k) != v}
    runtime_settings.save(db, new, user.username, now_msk())
    audit(db, request, user, "settings_update", "settings", runtime_settings.KEY, {"changes": changes})
    db.commit()
    runtime_settings.reset()
    cache.bump()  # счётчики и агрегаты, посчитанные по старым границам, больше не годятся
    return settings_out(db)
