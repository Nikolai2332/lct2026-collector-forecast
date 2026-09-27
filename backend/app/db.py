from collections.abc import Iterator

from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_s = get_settings()
# Пул — под пул потоков FastAPI (40 синхронных обработчиков одновременно): при 5 + 10 соединениях 40 пользователей
# ждали соединения до 30 с (нагрузочный тест, docs/LOADTEST.md). PostgreSQL по умолчанию держит 100 соединений
engine = create_engine(
    _s.database_url, pool_pre_ping=True,
    **({"pool_size": _s.db_pool_size, "max_overflow": _s.db_max_overflow}
       if make_url(_s.database_url).get_backend_name() == "postgresql" else {}),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
