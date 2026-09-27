"""Схема БД. Все метки времени — московское время без часового пояса (как в журналах заказчика)."""

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

# DIALECT: в PostgreSQL — JSONB (индексируемый, компактный); общий JSON остаётся только для тестов на SQLite.
JSONType = JSON().with_variant(JSONB(), "postgresql")

RISK_LEVELS = ("normal", "attention", "risk", "critical")
DECISION_TYPES = ("dispatch", "false_alarm", "monitor")
# Виды отказов разметки ML (docs/ML_DECISIONS.md)
FAULT_KINDS = ("Неисправен", "Отключено устройство", "Пропадание связи", "Сбой значения")  # «Сбой значения» — v3
WORK_ORDER_STATUSES = ("draft", "submitted", "in_progress", "done", "cancelled")
WORK_ORDER_PRIORITIES = ("low", "medium", "high", "critical")
# Роли заказчика (docs/SECURITY.md): диспетчер ОДС видит всё, диспетчер района — дерево своего района, техник —
# один комплекс, руководитель — чтение и отчёты в своей области, администратор ИС. engineer — роль прототипа
# (инженер данных: импорт, поток событий, симуляция), видит всё. Роли складываются (user_roles)
ROLES = ("dispatcher_ods", "dispatcher", "technician", "engineer", "manager", "admin")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Object(Base):
    """Дерево объектов: район → объект → подобъект."""

    __tablename__ = "objects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    level: Mapped[int] = mapped_column(SmallInteger)
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("objects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # district | controlHouse | guardObject
    name: Mapped[str] = mapped_column(String(255))

    __table_args__ = (CheckConstraint("level BETWEEN 1 AND 3", name="ck_objects_level"),)


class Channel(Base):
    """Канал данных датчика из справочника каналов."""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    object_id: Mapped[int] = mapped_column(ForeignKey("objects.id"), index=True)
    system_type: Mapped[str] = mapped_column(String(128))
    sensor_type: Mapped[str] = mapped_column(String(128))
    tag: Mapped[str] = mapped_column(String(64))
    tag_l1: Mapped[str | None] = mapped_column(String(16))
    tag_l2: Mapped[str | None] = mapped_column(String(16))
    tag_l3: Mapped[str | None] = mapped_column(String(16))
    tag_l4: Mapped[str | None] = mapped_column(String(16))
    tag_l5: Mapped[str | None] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(255))

    object: Mapped[Object] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_channels_system_sensor", "system_type", "sensor_type"),
        Index("ix_channels_sensor_type", "sensor_type"),
    )


class ChannelDaily(Base):
    """Суточные агрегаты по каналу — для графиков на карточке датчика."""

    __tablename__ = "channel_daily"

    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    events_count: Mapped[int] = mapped_column(Integer, default=0)
    alarm_count: Mapped[int] = mapped_column(Integer, default=0)
    fault_count: Mapped[int] = mapped_column(Integer, default=0)
    uncertain_count: Mapped[int] = mapped_column(Integer, default=0)
    power_off_count: Mapped[int] = mapped_column(Integer, default=0)
    status_changes: Mapped[int] = mapped_column(Integer, default=0)
    value_avg: Mapped[float | None] = mapped_column(Float)
    value_min: Mapped[float | None] = mapped_column(Float)
    value_max: Mapped[float | None] = mapped_column(Float)
    max_gap_min: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (Index("ix_channel_daily_day", "day"),)


class EventRecent(Base):
    """События последних 30 дней и входящий поток. Архив — в Parquet, не здесь."""

    __tablename__ = "events_recent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_event_id: Mapped[int | None] = mapped_column(BigInteger)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime)
    is_alarm: Mapped[bool] = mapped_column(Boolean, default=False)
    value_raw: Mapped[str] = mapped_column(String(128))
    value_num: Mapped[float | None] = mapped_column(Float)
    value_text: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        # Дубли одного события в одну секунду удаляются по связке «канал, время, значение»
        UniqueConstraint("channel_id", "ts", "value_raw", name="uq_events_recent_dedup"),
        Index("ix_events_recent_ts", "ts"),
    )


class ChannelFault(Base):
    """Отказы канала по разметке ML: статусы «Неисправен», «Отключено устройство» и пропадание связи
    (момент, когда пауза превысила личную норму канала). Загружаются из data/base/faults.parquet (ml/db/load_postgres.py)."""

    __tablename__ = "channel_faults"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    ts: Mapped[datetime] = mapped_column(DateTime)
    kind: Mapped[str] = mapped_column(String(32))

    __table_args__ = (
        UniqueConstraint("channel_id", "ts", "kind", name="uq_channel_faults"),
        CheckConstraint(_in("kind", FAULT_KINDS), name="ck_channel_faults_kind"),
    )


class Prediction(Base):
    """Прогноз модели в формате ML: channel_id, at, horizon_h, prob, health, risk_level, top_factors, model_version."""

    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"))
    at: Mapped[datetime] = mapped_column(DateTime)
    horizon_h: Mapped[int] = mapped_column(SmallInteger, default=24)
    prob: Mapped[float] = mapped_column(Float)
    health: Mapped[int] = mapped_column(SmallInteger)
    risk_level: Mapped[str] = mapped_column(String(16))
    top_factors: Mapped[list] = mapped_column(JSONType, default=list)
    # v3: вероятности видов отказа {link, fault, disconnected, value}, сумма = prob; NULL — у v2 и у «нормы»
    kind_probs: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    model_version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    channel: Mapped[Channel] = relationship(lazy="joined")
    outcome: Mapped["PredictionOutcome | None"] = relationship(lazy="joined", uselist=False)

    __table_args__ = (
        UniqueConstraint("channel_id", "at", "horizon_h", name="uq_predictions_channel_at"),
        # Главный запрос: срез на момент at по убыванию риска
        Index("ix_predictions_at_prob", "at", "prob"),
        # Журнал: диапазон времени + уровень риска
        Index("ix_predictions_at_risk", "at", "risk_level"),
        # История прогнозов на карточке датчика
        Index("ix_predictions_channel_at", "channel_id", "at"),
        CheckConstraint("prob >= 0 AND prob <= 1", name="ck_predictions_prob"),
        CheckConstraint("health >= 0 AND health <= 100", name="ck_predictions_health"),
        CheckConstraint("horizon_h > 0", name="ck_predictions_horizon"),
        CheckConstraint(_in("risk_level", RISK_LEVELS), name="ck_predictions_risk_level"),
    )


class PredictionOutcome(Base):
    """Сбылся ли прогноз: был ли отказ в интервале (at, at + horizon_h]."""

    __tablename__ = "prediction_outcomes"

    prediction_id: Mapped[int] = mapped_column(
        ForeignKey("predictions.id", ondelete="CASCADE"), primary_key=True
    )
    happened: Mapped[bool] = mapped_column(Boolean)
    fault_at: Mapped[datetime | None] = mapped_column(DateTime)
    fault_kind: Mapped[str | None] = mapped_column(String(64))
    labeled_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Reason(Base):
    __tablename__ = "reasons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    # Для какого решения подходит причина; NULL — для любого
    decision_type: Mapped[str | None] = mapped_column(String(16))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    # Для какого типа датчика; NULL — общая рекомендация
    sensor_type: Mapped[str | None] = mapped_column(String(128), index=True)
    text: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    auth_source: Mapped[str] = mapped_column(String(16), default="local")  # local | ldap
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (CheckConstraint(_in("role", ROLES), name="ck_users_role"),)


class UserRole(Base):
    """Роли пользователя — складываются. users.role — основная роль (первая), для совместимости."""

    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), primary_key=True)

    __table_args__ = (CheckConstraint(_in("role", ROLES), name="ck_user_roles_role"),)


class UserScope(Base):
    """Область видимости: узлы дерева объектов (район или объект-комплекс) — со всем поддеревом.
    Области нескольких строк объединяются. Для ролей «всё видно» (ОДС, администратор, инженер) не нужна."""

    __tablename__ = "user_scopes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("objects.id", ondelete="CASCADE"), primary_key=True)


class Decision(Base):
    """Решение диспетчера по прогнозу. Хранится история, актуально последнее."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prediction_id: Mapped[int] = mapped_column(ForeignKey("predictions.id", ondelete="CASCADE"))
    decision_type: Mapped[str] = mapped_column(String(16))
    reason_id: Mapped[int] = mapped_column(ForeignKey("reasons.id"))
    comment: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # user — принято в интерфейсе; simulation — смоделировано scripts/simulate_decisions (журнала ОДС у заказчика нет)
    source: Mapped[str] = mapped_column(String(16), default="user", server_default="user")

    reason: Mapped[Reason] = relationship(lazy="joined")
    user: Mapped[User | None] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_decisions_prediction_created", "prediction_id", "created_at"),
        CheckConstraint(_in("decision_type", DECISION_TYPES), name="ck_decisions_type"),
        CheckConstraint("source IN ('user', 'simulation')", name="ck_decisions_source"),
    )


class WorkOrder(Base):
    """Заявка на обслуживание (сначала черновик из прогноза)."""

    __tablename__ = "work_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    prediction_id: Mapped[int | None] = mapped_column(
        ForeignKey("predictions.id", ondelete="SET NULL"), index=True
    )
    channel_id: Mapped[int] = mapped_column(ForeignKey("channels.id"), index=True)
    object_id: Mapped[int] = mapped_column(ForeignKey("objects.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    reason_id: Mapped[int | None] = mapped_column(ForeignKey("reasons.id"))
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey("recommendations.id"))
    recommendation_text: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    assignee: Mapped[str | None] = mapped_column(String(255))
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)

    channel: Mapped[Channel] = relationship(lazy="joined")
    reason: Mapped[Reason | None] = relationship(lazy="joined")
    recommendation: Mapped[Recommendation | None] = relationship(lazy="joined")
    author: Mapped[User | None] = relationship(lazy="joined")

    __table_args__ = (
        Index("ix_work_orders_status_created", "status", "created_at"),
        CheckConstraint(_in("status", WORK_ORDER_STATUSES), name="ck_work_orders_status"),
        CheckConstraint(_in("priority", WORK_ORDER_PRIORITIES), name="ck_work_orders_priority"),
    )


class ModelMetric(Base):
    """Метрики модели на тесте: общие, базовая линия, по типам датчиков, Precision@K, упреждение."""

    __tablename__ = "model_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(32))  # overall | baseline | sensor_type | precision_at_k | lead_time
    key: Mapped[str] = mapped_column(String(128), default="")
    precision: Mapped[float | None] = mapped_column(Float)
    recall: Mapped[float | None] = mapped_column(Float)
    f1: Mapped[float | None] = mapped_column(Float)
    pr_auc: Mapped[float | None] = mapped_column(Float)
    support: Mapped[int | None] = mapped_column(Integer)
    value: Mapped[float | None] = mapped_column(Float)
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("model_version", "scope", "key", name="uq_model_metrics"),)


class ModelThreshold(Base):
    """Таблица для ползунка порога: порог → Precision, Recall, тревог в сутки."""

    __tablename__ = "model_thresholds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(64))
    threshold: Mapped[float] = mapped_column(Float)
    precision: Mapped[float] = mapped_column(Float)
    recall: Mapped[float] = mapped_column(Float)
    alerts_per_day: Mapped[float] = mapped_column(Float)
    tp_per_day: Mapped[float] = mapped_column(Float)
    fp_per_day: Mapped[float] = mapped_column(Float)
    is_selected: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("model_version", "threshold", name="uq_model_thresholds"),)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONType)
    ip: Mapped[str | None] = mapped_column(String(64))


class SimState(Base):
    """Симуляция потока событий: одна строка (id = 1). Модельное время не хранится как «истина», а считается:
    model_start + (сейчас − started_at) × speed — так часы идут без записи в БД каждую секунду.
    model_time — последнее сохранённое значение, для наблюдения и журнала."""

    __tablename__ = "sim_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    day: Mapped[date | None] = mapped_column(Date)
    speed: Mapped[int] = mapped_column(SmallInteger, default=60)
    model_start: Mapped[datetime | None] = mapped_column(DateTime)
    model_time: Mapped[datetime | None] = mapped_column(DateTime)
    # Реальное время запуска (МСК, с микросекундами) — от него считается ход модельных часов
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    started_by: Mapped[str | None] = mapped_column(String(64))
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime)
    stop_reason: Mapped[str | None] = mapped_column(String(32))
    # Последний срез прогнозов, по которому уже разосланы уведомления
    last_snapshot: Mapped[datetime | None] = mapped_column(DateTime)
    events_received: Mapped[int] = mapped_column(Integer, default=0)
    events_accepted: Mapped[int] = mapped_column(Integer, default=0)
    critical_sent: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_sim_state_single_row"),
        CheckConstraint("speed BETWEEN 1 AND 600", name="ck_sim_state_speed"),
    )


class AppSetting(Base):
    """Настраиваемые параметры (app.runtime_settings): ключ → JSON. Нет строки — значение по умолчанию."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    updated_by: Mapped[str | None] = mapped_column(String(64))
