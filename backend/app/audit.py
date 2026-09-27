from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User
from app.timeutil import now_msk


def audit(
    db: Session,
    request: Request | None,
    user: User | None,
    action: str,
    entity: str | None = None,
    entity_id: object = None,
    details: dict | None = None,
    username: str | None = None,
) -> None:
    """Добавляет запись в audit_log; фиксируется вместе с основной транзакцией.

    Пароли и токены сюда не передаются; IP — реальный клиент (uvicorn --proxy-headers за Nginx)."""
    db.add(
        AuditLog(
            ts=now_msk(),
            user_id=user.id if user else None,
            username=(user.username if user else username or "")[:64] or None,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            details=details,
            ip=request.client.host[:64] if request and request.client else None,
        )
    )
