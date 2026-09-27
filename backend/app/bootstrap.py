"""Подготовка к запуску и учётные записи.

    python -m app.bootstrap check      — отказ (код 1), если настройки небезопасны для APP_ENV=production
    python -m app.bootstrap prepare    — production: блокирует демо-учётки с известными паролями
                                         и создаёт администратора из ADMIN_USERNAME / ADMIN_PASSWORD
    python -m app.bootstrap user LOGIN --role dispatcher --scope 5773 --full-name "Иванов И. И."
                                       — создать пользователя или сменить ему пароль, роли и область;
                                         --role и --scope можно повторять (роли складываются, область —
                                         id района или объекта-комплекса); пароль спрашивается с клавиатуры
                                         или берётся из USER_PASSWORD

entrypoint.sh вызывает check до миграций и prepare перед запуском API.
"""

import argparse
import getpass
import logging
import os
import secrets
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings, production_problems
from app.models import ROLES, User
from app.security import hash_password, verify_password
from app.seed import DEMO_USERS

log = logging.getLogger("bootstrap")

MIN_PASSWORD_LEN = 12
DEMO_PASSWORDS = {p for _, p, _, _ in DEMO_USERS}


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD_LEN:
        return f"пароль короче {MIN_PASSWORD_LEN} символов"
    if password in DEMO_PASSWORDS or password.lower() in {"password1234", "admin1234567", "qwerty123456"}:
        return "пароль из демо-набора или слишком простой"
    return None


def lock_demo_users(db: Session) -> list[str]:
    """Блокирует активных демо-пользователей, у которых остался пароль из README. Возвращает их логины."""
    from app.audit import audit

    locked = []
    for username, password, _, _ in DEMO_USERS:
        user = db.scalar(select(User).where(User.username == username))
        if user is None or not user.is_active or not verify_password(password, user.password_hash):
            continue
        user.is_active = False
        user.password_hash = hash_password(secrets.token_urlsafe(32))
        audit(db, None, None, "demo_user_locked", "user", user.id, username="system")
        locked.append(username)
    db.commit()
    return locked


def upsert_user(db: Session, username: str, password: str, role: str | list[str], full_name: str | None,
                scope_ids: list[int] | None = None) -> User:
    from app.access import set_access

    roles = [role] if isinstance(role, str) else list(role)
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, full_name=full_name or username, role=roles[0], auth_source="local")
        db.add(user)
    if full_name:
        user.full_name = full_name
    user.password_hash = hash_password(password)
    user.is_active = True
    db.flush()
    set_access(db, user, roles, scope_ids or [])
    return user


def ensure_admin(db: Session, s: Settings) -> str:
    """Создаёт администратора из переменных окружения, если активного администратора нет."""
    from app.audit import audit

    from sqlalchemy import or_

    from app.models import UserRole

    has_admin = db.scalar(
        select(User.id).outerjoin(UserRole, UserRole.user_id == User.id)
        .where(or_(User.role == "admin", UserRole.role == "admin"), User.is_active.is_(True)).limit(1)
    )
    if has_admin:
        return "exists"
    if not (s.admin_username and s.admin_password):
        return "missing"
    problem = password_problem(s.admin_password)
    if problem:
        raise SystemExit(f"ADMIN_PASSWORD не подходит: {problem}")
    user = upsert_user(db, s.admin_username.strip()[:64], s.admin_password, "admin", s.admin_full_name)
    audit(db, None, None, "admin_bootstrap", "user", user.id, username="system")
    db.commit()
    return "created"


def cmd_check() -> int:
    problems = production_problems(get_settings())
    if problems:
        print("Небезопасные настройки для APP_ENV=production — API не запущен:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print(f"Настройки проверены (APP_ENV={get_settings().app_env})")
    return 0


def cmd_prepare() -> int:
    from app.db import SessionLocal

    s = get_settings()
    if not s.is_production:
        # demo: на базе, заполненной до ролевой модели заказчика (реальные данные), добавить демо-учётки
        # новых ролей (ОДС, техник) с их областями. Существующих пользователей не меняет
        from app.seed import ensure_demo_users

        with SessionLocal() as db:
            added = ensure_demo_users(db)
        if added:
            log.info("Добавлены демо-пользователи: %s", ", ".join(added))
        return 0
    with SessionLocal() as db:
        locked = lock_demo_users(db)
        if locked:
            log.warning("Заблокированы демо-учётки с известными паролями: %s", ", ".join(locked))
        state = ensure_admin(db, s)
    if state == "created":
        log.info("Создан администратор %s. Уберите ADMIN_PASSWORD из .env после первого входа", s.admin_username)
    elif state == "missing":
        log.warning("Активного администратора нет: задайте ADMIN_USERNAME и ADMIN_PASSWORD или выполните "
                    "docker compose exec api python -m app.bootstrap user <логин> --role admin")
    return 0


def cmd_user(args) -> int:
    from app.db import SessionLocal

    password = os.environ.get("USER_PASSWORD") or getpass.getpass("Пароль: ")
    if not os.environ.get("USER_PASSWORD") and getpass.getpass("Ещё раз: ") != password:
        print("Пароли не совпадают", file=sys.stderr)
        return 1
    problem = password_problem(password)
    if problem:
        print(f"Не подходит: {problem}", file=sys.stderr)
        return 1
    from app.audit import audit

    with SessionLocal() as db:
        user = upsert_user(db, args.username.strip()[:64], password, args.role, args.full_name, args.scope)
        audit(db, None, None, "user_set", "user", user.id, {"roles": args.role, "scope": args.scope or []},
              username="system")
        db.commit()
    print(f"Пользователь {args.username} ({', '.join(args.role)}; область: {args.scope or 'нет'}) сохранён и активен")
    return 0


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Проверка настроек и учётные записи")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    sub.add_parser("prepare")
    u = sub.add_parser("user")
    u.add_argument("username")
    u.add_argument("--role", choices=ROLES, required=True, action="append", help="можно несколько раз")
    u.add_argument("--scope", type=int, action="append", help="id района или объекта-комплекса; можно несколько")
    u.add_argument("--full-name")
    args = parser.parse_args()
    code = {"check": cmd_check, "prepare": cmd_prepare}.get(args.cmd)
    sys.exit(code() if code else cmd_user(args))


if __name__ == "__main__":
    main()
