"""Тестовое окружение: SQLite в памяти вместо PostgreSQL.

Вся SQLite-специфика живёт здесь и в основной код не попадает (см. docs/DECISIONS.md).
Чтобы прогнать тесты на настоящем PostgreSQL, задайте TEST_DATABASE_URL=postgresql+psycopg://…
(база будет очищена).
"""

import os

os.environ.setdefault("JWT_SECRET", "test-secret-that-is-at-least-32-bytes-long")
# Фоновые задания в тестах не запускаем: такт симуляции и разметку исходов тесты вызывают сами
os.environ.setdefault("SCHEDULER_ENABLED", "false")
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import BigInteger, create_engine, event  # noqa: E402
from sqlalchemy.ext.compiler import compiles  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import db as app_db  # noqa: E402
from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed  # noqa: E402

IS_SQLITE = TEST_DATABASE_URL.startswith("sqlite")


@compiles(BigInteger, "sqlite")
def _bigint_as_integer(type_, compiler, **kw):
    # Только для SQLite: автоинкремент работает лишь у INTEGER PRIMARY KEY
    return "INTEGER"


def _make_engine():
    if not IS_SQLITE:
        return create_engine(TEST_DATABASE_URL)
    engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@pytest.fixture(scope="session")
def engine():
    engine = _make_engine()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_factory() as s:
        seed(s, "small")
    # SSE и сид открывают сессии напрямую через SessionLocal — подменяем и его
    app_db.SessionLocal.configure(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture(scope="session")
def client(session_factory):
    def override_get_db():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _token(client, username: str) -> str:
    r = client.post("/api/auth/login", json={"username": username, "password": f"{username}123"})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def tokens(client) -> dict[str, str]:
    return {role: _token(client, role) for role in ("dispatcher", "engineer", "manager", "admin")}


@pytest.fixture(scope="session")
def auth(tokens):
    def headers(role: str = "dispatcher") -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens[role]}"}

    return headers


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    # Лимиты входа и загрузок живут в памяти процесса — между тестами обнуляем
    from app.ratelimit import reset_all

    reset_all()
    yield
    reset_all()


@pytest.fixture(autouse=True)
def _reset_cache():
    # Кэш агрегатов тоже в памяти процесса
    from app import cache

    cache.clear()
    yield
    cache.clear()
