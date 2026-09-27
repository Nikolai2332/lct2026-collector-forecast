"""Роли и области видимости локальных пользователей (администратор). Учётки из LDAP/AD ведутся в каталоге:
их роли и области приходят из групп при каждом входе, здесь их менять нельзя (409)."""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.access import SCOPED_ROLES, Access, load_access, ordered_roles, require, set_access, user_out
from app.audit import audit
from app.db import get_db
from app.models import Object, User
from app.security import get_current_user

router = APIRouter(prefix="/api/users", tags=["Пользователи"])


@router.get(
    "",
    response_model=schemas.UserList,
    summary="Пользователи с ролями и областями видимости (администратор)",
    responses={403: {"model": schemas.ErrorResponse}},
)
def list_users(_access: Access = Depends(require("users_admin")), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(User.is_active.desc(), User.username)).all()
    return schemas.UserList(items=[user_out(db, u) for u in users])


@router.put(
    "/{user_id}/access",
    response_model=schemas.UserOut,
    summary="Назначить роли и область видимости локальному пользователю",
    description=(
        "Роли складываются. Область — узлы дерева объектов уровня 1 (район) или 2 (объект-комплекс) со всем "
        "поддеревом; обязательна, если среди ролей нет «видит всё» (диспетчер ОДС, инженер данных, администратор). "
        "Учётки LDAP/AD — 409 (роли и области задают группы каталога). Снять с себя права администратора "
        "пользователей нельзя (409). Изменение пишется в `audit_log` (`user_access_update`, было/стало)."
    ),
    responses={403: {"model": schemas.ErrorResponse}, 404: {"model": schemas.ErrorResponse},
               409: {"model": schemas.ErrorResponse}, 422: {"model": schemas.ErrorResponse}},
)
def update_access(
    user_id: int,
    body: schemas.UserAccessIn,
    request: Request,
    _access: Access = Depends(require("users_admin")),
    me: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Пользователь не найден")
    if user.auth_source != "local":
        raise HTTPException(status.HTTP_409_CONFLICT, "Роли и область учётки из LDAP/AD задаются группами каталога")
    roles, scope_ids = sorted(set(body.roles)), sorted(set(body.scope_object_ids))
    levels = dict(db.execute(select(Object.id, Object.level).where(Object.id.in_(scope_ids))).all()) if scope_ids else {}
    bad = [i for i in scope_ids if levels.get(i) not in (1, 2)]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Область — только район или объект-комплекс (уровень 1–2); не подходят: {bad}")
    old = load_access(db, user)
    new_preview = Access(user.id, tuple(ordered_roles(roles)), tuple(scope_ids))
    if not new_preview.unrestricted and set(roles) & SCOPED_ROLES and not scope_ids:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Для роли с областью выберите район или объект")
    if user.id == me.id and not new_preview.can("users_admin"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Нельзя снять с себя права администратора пользователей")
    set_access(db, user, roles, scope_ids)
    audit(db, request, me, "user_access_update", "user", user.id, {
        "username": user.username,
        "old": {"roles": list(old.roles), "scope": list(old.scope_ids)},
        "new": {"roles": list(new_preview.roles), "scope": scope_ids},
    })
    db.commit()
    return user_out(db, user)
