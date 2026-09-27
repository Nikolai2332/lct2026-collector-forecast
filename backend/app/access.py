"""Ролевая модель заказчика и области видимости (docs/SECURITY.md, «Роли и области видимости»).

Ответ заказчика: «диспетчер» подразделения эксплуатации видит дерево своего района, «диспетчер ОДС» — всё,
«техник» — один комплекс, «администратор» ИС. Роли складываются, группы AD по комплексам задают область.

- Права — объединение прав всех ролей пользователя (PERMISSIONS).
- Область — объединение поддеревьев узлов user_scopes. Если хотя бы одна роль «видит всё» (GLOBAL_ROLES) —
  область не ограничена. Роль с областью без единого узла не видит ничего (закрыто по умолчанию).
- Фильтрация — на сервере: ObjectIndex, загруженный с областью (app.services), пропускает только видимые
  объекты, а apply_channel_filters добавляет условие по объекту датчика. Чужой объект, датчик, прогноз или
  заявка по прямому id — 404, как несуществующие (существование не раскрывается).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.labels import ROLE_LABELS
from app.models import ROLES, Object, User, UserRole, UserScope
from app.security import get_current_user

GLOBAL_ROLES = frozenset({"dispatcher_ods", "engineer", "admin"})
SCOPED_ROLES = frozenset({"dispatcher", "technician", "manager"})

# Права. view и export есть у всех ролей — в пределах области
PERMISSIONS: dict[str, frozenset[str]] = {
    "dispatcher_ods": frozenset({"view", "export", "decide", "work_orders", "work_order_status", "sim_control"}),
    "dispatcher": frozenset({"view", "export", "decide", "work_orders", "work_order_status"}),
    "technician": frozenset({"view", "export", "work_order_status"}),
    "engineer": frozenset({"view", "export", "decide", "work_orders", "work_order_status", "data_import",
                           "sim_control"}),
    "manager": frozenset({"view", "export"}),
    "admin": frozenset({"view", "export", "decide", "work_orders", "work_order_status", "data_import",
                        "sim_control", "settings", "users_admin", "delete_any_draft"}),
}
PERMISSION_LABELS = {
    "view": "Просмотр данных своей области",
    "export": "Выгрузка журнала XLSX/XML",
    "decide": "Решение по прогнозу",
    "work_orders": "Создание и изменение заявок",
    "work_order_status": "Смена статуса заявки (выезд, выполнение)",
    "data_import": "Импорт данных и поток событий",
    "sim_control": "Запуск и остановка симуляции",
    "settings": "Настраиваемые параметры",
    "users_admin": "Роли и области пользователей",
    "delete_any_draft": "Удаление чужих черновиков",
}
assert set(PERMISSIONS) == set(ROLES)


# Порядок ролей в ответах и основная роль users.role — от самой широкой к самой узкой
ROLE_PRIORITY = ("admin", "dispatcher_ods", "engineer", "dispatcher", "manager", "technician")
assert set(ROLE_PRIORITY) == set(ROLES)


def ordered_roles(roles: Iterable[str]) -> list[str]:
    s = set(roles)
    return [r for r in ROLE_PRIORITY if r in s]


def user_roles(db: Session, user: User) -> list[str]:
    roles = list(db.scalars(select(UserRole.role).where(UserRole.user_id == user.id)))
    # Нет строк (учётка создана в обход сервиса) — основная роль из users.role
    return ordered_roles(roles or [user.role])


def user_scope_ids(db: Session, user: User) -> list[int]:
    return sorted(db.scalars(select(UserScope.object_id).where(UserScope.user_id == user.id)))


@dataclass(frozen=True)
class Access:
    user_id: int
    roles: tuple[str, ...]
    scope_ids: tuple[int, ...]  # узлы области (как назначены); для глобальных ролей не используются

    @property
    def unrestricted(self) -> bool:
        return bool(GLOBAL_ROLES & set(self.roles))

    @property
    def permissions(self) -> frozenset[str]:
        result: set[str] = set()
        for r in self.roles:
            result |= PERMISSIONS.get(r, frozenset())
        return frozenset(result)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    @property
    def key(self) -> tuple | None:
        """Ключ области для кэша и группировки подписчиков SSE; None — видно всё."""
        return None if self.unrestricted else self.scope_ids


def load_access(db: Session, user: User) -> Access:
    return Access(user.id, tuple(user_roles(db, user)), tuple(user_scope_ids(db, user)))


def get_access(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Access:
    """Зависимость FastAPI: роли и область вошедшего пользователя (читаются из БД при каждом запросе —
    изменения администратора действуют сразу)."""
    return load_access(db, user)


FULL_ACCESS = Access(0, ("admin",), ())


def scope_label(db: Session, access: Access) -> str:
    if access.unrestricted:
        return "все объекты"
    if not access.scope_ids:
        return "область не назначена"
    names = dict(db.execute(select(Object.id, Object.name).where(Object.id.in_(access.scope_ids))).all())
    return ", ".join(names.get(i, f"#{i}") for i in access.scope_ids)


def roles_label(roles: Iterable[str]) -> str:
    return " + ".join(ROLE_LABELS[r] for r in ordered_roles(roles))


def require(permission: str):
    """Зависимость: 403, если ни одна роль пользователя не даёт права permission. Возвращает Access."""
    from fastapi import HTTPException, status

    def checker(access: Access = Depends(get_access)) -> Access:
        if not access.can(permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для этого действия")
        return access

    return checker


def require_user(permission: str):
    """Как require, но возвращает пользователя — для обработчиков, которым область не нужна (импорт, симуляция)."""

    def checker(user: User = Depends(get_current_user), _access: Access = Depends(require(permission))) -> User:
        return user

    return checker


def set_access(db: Session, user: User, roles: Iterable[str], scope_ids: Iterable[int]) -> None:
    """Заменяет роли и область пользователя (без commit). users.role — основная роль (первая по порядку ROLES)."""
    from sqlalchemy import delete

    ordered = ordered_roles(roles)
    if not ordered:
        raise ValueError("нужна хотя бы одна роль")
    db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    db.execute(delete(UserScope).where(UserScope.user_id == user.id))
    db.flush()
    db.add_all([UserRole(user_id=user.id, role=r) for r in ordered])
    db.add_all([UserScope(user_id=user.id, object_id=o) for o in sorted(set(scope_ids))])
    user.role = ordered[0]
    db.flush()


def user_out(db: Session, user: User):
    from app import schemas

    access = load_access(db, user)
    nodes = (
        db.execute(select(Object.id, Object.name, Object.level).where(Object.id.in_(access.scope_ids))).all()
        if access.scope_ids else []
    )
    by_id = {n.id: n for n in nodes}
    return schemas.UserOut(
        id=user.id, username=user.username, full_name=user.full_name, role=access.roles[0],
        role_label=roles_label(access.roles), roles=list(access.roles),
        scope=[schemas.ScopeNode(id=i, name=by_id[i].name, level=by_id[i].level) for i in access.scope_ids if i in by_id],
        unrestricted=access.unrestricted, scope_label=scope_label(db, access),
        permissions=sorted(access.permissions), auth_source=user.auth_source, is_active=user.is_active,
    )
