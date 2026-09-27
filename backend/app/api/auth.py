import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import schemas
from app.audit import audit
from app.config import get_settings
from app.db import get_db
from app.deps import at_param
from app.access import user_out
from app.ldap_auth import LdapUnavailable
from app.models import User
from app.ratelimit import login_failures
from app.security import create_access_token, get_current_user, ldap_authenticate, verify_password
from app.timeutil import now_msk

router = APIRouter(prefix="/api/auth", tags=["Вход"])
log = logging.getLogger(__name__)




def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_rate_limit(request: Request, username: str) -> None:
    """Слишком много неудачных входов с этого IP (для логина или всего) или на этот логин со всех адресов — 429
    до конца окна."""
    s = get_settings()
    ip = _client_ip(request)
    window = s.login_window_seconds
    for key, limit in ((f"ip:{ip}", s.login_max_failures_per_ip), (f"user:{ip}:{username}", s.login_max_failures_per_user),
                       (f"account:{username}", s.login_max_failures_per_account)):
        if login_failures.count(key, window) >= limit:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Слишком много неудачных попыток входа. Повторите через несколько минут.",
                headers={"Retry-After": str(login_failures.retry_after(key, window))},
            )


def _register_failure(request: Request, username: str) -> None:
    ip = _client_ip(request)
    login_failures.hit(f"ip:{ip}")
    login_failures.hit(f"user:{ip}:{username}")
    login_failures.hit(f"account:{username}")


def _fail(db: Session, request: Request, username: str, reason: str) -> HTTPException:
    _register_failure(request, username)
    audit(db, request, None, "login_failed", "user", details={"username": username, "reason": reason},
          username=username)
    db.commit()
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный логин или пароль")


def _authenticate(db: Session, request: Request, username: str, password: str) -> schemas.TokenOut:
    username = username.strip()[:64]
    _check_rate_limit(request, username.lower())
    if get_settings().ldap_enabled:
        # Закрыто по умолчанию: локальные пароли при включённом LDAP не проверяются,
        # а всё, кроме явно найденного активного пользователя, — отказ
        try:
            user = ldap_authenticate(db, username, password)
        except LdapUnavailable as e:
            db.rollback()
            log.error("LDAP недоступен: %s", e)
            _fail(db, request, username.lower(), "ldap_unavailable")
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Служба каталогов (LDAP) недоступна — вход временно невозможен. Обратитесь к администратору.")
        if user is None or not user.is_active:
            db.rollback()
            raise _fail(db, request, username.lower(), "ldap")
    else:
        user = db.scalar(select(User).where(User.username == username))
        # Пароль проверяем и для несуществующего логина: время ответа не выдаёт, есть ли такой пользователь
        ok = verify_password(password, user.password_hash if user else None)
        if user is None or not user.is_active or not ok:
            raise _fail(db, request, username.lower(), "local")
    user.last_login_at = now_msk()
    audit(db, request, user, "login", "user", user.id)
    db.commit()
    token, expires_in = create_access_token(user)
    return schemas.TokenOut(access_token=token, expires_in=expires_in, user=user_out(db, user))


@router.post(
    "/login",
    response_model=schemas.TokenOut,
    summary="Вход по логину и паролю (JSON)",
    responses={401: {"model": schemas.ErrorResponse}, 429: {"model": schemas.ErrorResponse}},
)
def login(body: schemas.LoginIn, request: Request, db: Session = Depends(get_db)):
    return _authenticate(db, request, body.username, body.password)


@router.post(
    "/token",
    response_model=schemas.TokenOut,
    summary="Вход через форму OAuth2 (для кнопки Authorize в Swagger)",
    responses={401: {"model": schemas.ErrorResponse}, 429: {"model": schemas.ErrorResponse}},
)
def token(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _authenticate(db, request, form.username, form.password)


@router.get("/me", response_model=schemas.UserOut, summary="Текущий пользователь")
def me(_at=Depends(at_param), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return user_out(db, user)
