from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

# Значения из примеров и старых версий docker-compose: с ними production не стартует
KNOWN_WEAK_SECRETS = {
    "dev-secret-change-me-in-env-min-32-bytes",
    "change-me-to-a-long-random-string",
    "test-secret-that-is-at-least-32-bytes-long",
}
WEAK_MARKERS = ("change-me", "changeme", "dev-secret", "example", "secret123")
WEAK_DB_PASSWORDS = {"", "change-me", "changeme", "collector", "postgres", "password", "admin", "123456"}
MIN_JWT_SECRET_LEN = 32
MIN_DB_PASSWORD_LEN = 12


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Прогноз отказов оборудования коллекторов"
    app_version: str = "0.1.0"

    # demo — локальный показ: демо-пользователи, быстрый вход, Swagger. production — VPS: всё это выключено,
    # слабые секреты не принимаются (см. docs/SECURITY.md)
    app_env: Literal["demo", "production"] = "demo"

    database_url: str = "postgresql+psycopg://collector:change-me@localhost:5432/collector"
    # Пул соединений с PostgreSQL: постоянных + временных сверх них
    db_pool_size: int = 10
    db_max_overflow: int = 30

    jwt_secret: str = "dev-secret-change-me-in-env-min-32-bytes"
    jwt_algorithm: Literal["HS256"] = "HS256"
    jwt_expire_minutes: int = 720

    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    log_level: str = "info"

    # Swagger (/docs, /openapi.json). Не задано — включён в demo и выключен в production
    docs_enabled: bool | None = None

    # Начальный администратор для production (создаётся, если активного администратора нет)
    admin_username: str = ""
    admin_password: str = ""
    admin_full_name: str = "Администратор системы"

    # Защита от перебора: неудачных входов за окно с одного IP для одного логина и всего с одного IP
    login_max_failures_per_user: int = 5
    login_max_failures_per_ip: int = 30
    # Неудачных входов на логин со всех адресов вместе: перебор одного пароля с многих IP (ботнет) тоже упирается в 429.
    # Цена — законный пользователь может подождать до конца окна, если его логин перебирают
    login_max_failures_per_account: int = 20
    login_window_seconds: int = 300
    # Импорт файлов: не больше N загрузок за окно на пользователя
    import_max_per_window: int = 20
    import_window_seconds: int = 600

    # Одноразовый тикет для SSE, секунды
    sse_ticket_ttl_seconds: int = 60

    ldap_enabled: bool = False
    ldap_url: str = ""
    ldap_base_dn: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_filter: str = "(sAMAccountName={username})"
    ldap_role_groups: str = ""
    # Область видимости по группам AD: «id объекта:группа» через запятую (или «;» с полными DN). Роли с областью
    # (диспетчер района, техник, руководитель) без подходящей группы не входят
    ldap_scope_groups: str = ""
    # StartTLS поверх ldap:// (для ldaps:// не нужен); свой корневой сертификат каталога — путь к PEM
    ldap_start_tls: bool = False
    ldap_ca_file: str = ""
    # Группы пользователя: memberOf из записи и поиск групп по этому фильтру ({user_dn}, {username}); пусто — только memberOf
    ldap_group_filter: str = "(|(member={user_dn})(uniqueMember={user_dn}))"
    ldap_group_base_dn: str = ""

    # Интервал heartbeat-комментариев в SSE, секунды
    sse_ping_seconds: int = 15

    # Симуляция потока (проигрывание рассчитанных прогнозов). Не задано — включена в demo, выключена в production
    sim_enabled: bool | None = None
    # Сколько новых критических прогнозов среза разослать по отдельности; остальные — одним сводным уведомлением
    sim_max_notifications_per_slice: int = 5

    # Расписание (APScheduler, в процессе API): такт симуляции и разметка исходов
    scheduler_enabled: bool = True
    sim_tick_seconds: float = 1.0
    outcomes_interval_minutes: int = 30
    # Разметка исходов смотрит прогнозы за столько дней до последнего закрытого горизонта
    outcomes_lookback_days: int = 7
    outcomes_batch_size: int = 5000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def docs_on(self) -> bool:
        return self.docs_enabled if self.docs_enabled is not None else not self.is_production

    @property
    def sim_on(self) -> bool:
        return self.sim_enabled if self.sim_enabled is not None else not self.is_production


def _weak_secret(value: str) -> bool:
    low = value.lower()
    return len(value) < MIN_JWT_SECRET_LEN or value in KNOWN_WEAK_SECRETS or any(m in low for m in WEAK_MARKERS)


def production_problems(s: Settings) -> list[str]:
    """Почему эти настройки нельзя запускать в production. Пустой список — можно."""
    if not s.is_production:
        return []
    problems = []
    if _weak_secret(s.jwt_secret):
        problems.append(
            f"JWT_SECRET слабый или из примера: нужно не меньше {MIN_JWT_SECRET_LEN} случайных символов "
            "(openssl rand -hex 32)"
        )
    try:
        db_password = make_url(s.database_url).password or ""
    except Exception:
        db_password = ""
    if db_password.lower() in WEAK_DB_PASSWORDS or len(db_password) < MIN_DB_PASSWORD_LEN or any(
        m in db_password.lower() for m in WEAK_MARKERS
    ):
        problems.append(
            f"Пароль БД (POSTGRES_PASSWORD) слабый или из примера: нужно не меньше {MIN_DB_PASSWORD_LEN} "
            "случайных символов (openssl rand -hex 24)"
        )
    origins = s.cors_origin_list
    if not origins or any(o == "*" or not o.startswith("https://") for o in origins):
        problems.append("CORS_ORIGINS в production — явный список адресов https://<домен>, без * и http://")
    if s.ldap_enabled and s.ldap_url.lower().startswith("ldap://") and not s.ldap_start_tls:
        problems.append("LDAP в production — только ldaps:// или LDAP_START_TLS=true: пароли не должны идти открытым текстом")
    if s.jwt_expire_minutes > 24 * 60:
        problems.append("JWT_EXPIRE_MINUTES в production — не больше 1440 (сутки)")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
