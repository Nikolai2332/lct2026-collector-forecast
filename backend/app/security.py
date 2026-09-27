import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

# OWASP (2023+) для PBKDF2-HMAC-SHA256 — от 600 000 итераций. Старые хеши с 200 000 проверяются как есть
_PBKDF2_ITERATIONS = 600_000

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt}${digest}"


# Хеш для «пустой» проверки: вход с несуществующим логином тратит столько же времени, сколько с неверным паролем
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def verify_password(password: str, stored: str | None) -> bool:
    """None вместо хеша (нет пользователя) — та же работа по времени и всегда False."""
    if not stored:
        stored, found = _DUMMY_HASH, False
    else:
        found = True
    try:
        _, iterations, salt, digest = stored.split("$")
        rounds = int(iterations)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds).hex()
    return hmac.compare_digest(candidate, digest) and found


def create_access_token(user: User) -> tuple[str, int]:
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    payload = {
        "sub": user.username,
        "uid": user.id,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), expires_in


def ldap_authenticate(db: Session, username: str, password: str) -> User | None:
    """Вход через LDAP/AD (app.ldap_auth): User — только после bind паролем пользователя и роли по группе, иначе None.
    Недоступный или неверно настроенный каталог — app.ldap_auth.LdapUnavailable (вызывающий код отвечает 503)."""
    from app.ldap_auth import ldap_login

    return ldap_login(db, username, password)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail, headers={"WWW-Authenticate": "Bearer"})


def user_from_token(raw: str, db: Session) -> User:
    settings = get_settings()
    try:
        payload = jwt.decode(
            raw, settings.jwt_secret, algorithms=[settings.jwt_algorithm], options={"require": ["exp", "sub"]}
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Сессия истекла, войдите заново")
    except jwt.PyJWTError:
        raise _unauthorized("Недействительный токен")
    user = db.scalar(select(User).where(User.username == payload.get("sub")))
    if user is None or not user.is_active:
        raise _unauthorized("Пользователь не найден или заблокирован")
    return user


def get_current_user(header_token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    # Токен — только в заголовке Authorization. Для EventSource есть одноразовый тикет (SseTickets)
    if not header_token:
        raise _unauthorized("Требуется вход в систему")
    return user_from_token(header_token, db)


def require_roles(*roles: str):
    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Недостаточно прав для этого действия")
        return user

    return checker


# ---------- Одноразовые тикеты для SSE ----------


@dataclass
class _Ticket:
    user_id: int
    expires: float


class SseTickets:
    """EventSource не умеет заголовки, а JWT в URL оседает в логах и истории.

    Вместо него — случайный тикет на 30–60 секунд: выдаётся по обычному JWT, принимается
    только потоком /api/notifications/stream и сгорает при первом использовании.
    Хранится в памяти процесса (API работает одним процессом uvicorn).
    """

    MAX_TICKETS = 10_000

    def __init__(self) -> None:
        self._items: dict[str, _Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, user: User, ttl: int) -> str:
        now = time.monotonic()
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            self._items = {k: v for k, v in self._items.items() if v.expires > now}
            if len(self._items) >= self.MAX_TICKETS:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много подключений, повторите позже")
            self._items[ticket] = _Ticket(user.id, now + ttl)
        return ticket

    def redeem(self, ticket: str) -> int | None:
        with self._lock:
            item = self._items.pop(ticket, None)
        if item is None or item.expires <= time.monotonic():
            return None
        return item.user_id

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


sse_tickets = SseTickets()


# Кто что может
WRITERS = ("dispatcher", "engineer", "admin")  # решения и заявки
DATA_ADMINS = ("engineer", "admin")  # импорт данных и поток событий
