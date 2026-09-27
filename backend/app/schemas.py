"""Pydantic-схемы контракта API. Формы ответов согласованы с ТЗ фронтенда (docs/API.md)."""

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

RiskLevel = Literal["normal", "attention", "risk", "critical"]
DecisionType = Literal["dispatch", "false_alarm", "monitor"]
WorkOrderStatus = Literal["draft", "submitted", "in_progress", "done", "cancelled"]
WorkOrderPriority = Literal["low", "medium", "high", "critical"]
Role = Literal["dispatcher_ods", "dispatcher", "technician", "engineer", "manager", "admin"]
Permission = Literal[
    "view", "export", "decide", "work_orders", "work_order_status", "data_import", "sim_control", "settings",
    "users_admin", "delete_any_draft",
]


class Schema(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------- Общие ----------


class ErrorResponse(Schema):
    detail: str = Field(examples=["Прогноз не найден"])


class ChannelRef(Schema):
    id: int = Field(examples=[334609])
    name: str = Field(examples=["Дым ПК 1101+2"])
    sensor_type: str = Field(examples=["Датчик дыма"])
    system_type: str = Field(examples=["Пожарная охрана"])


class ObjectRef(Schema):
    id: int = Field(examples=[20])
    name: str = Field(examples=["объект Фита"])
    path: list[str] = Field(
        description="Путь от района до объекта включительно",
        examples=[["Район по эксплуатации", "объект Бета", "объект Фита"]],
    )


class Outcome(Schema):
    happened: bool = Field(description="Случился ли отказ в течение 24 ч после прогноза")
    fault_at: datetime | None = Field(None, examples=["2026-08-01T18:42:10"])


class ReasonRef(Schema):
    id: int
    name: str


class UserRef(Schema):
    id: int
    username: str
    full_name: str


class DecisionOut(Schema):
    id: int
    decision_type: DecisionType
    decision_label: str = Field(examples=["Выезд бригады"])
    reason: ReasonRef
    comment: str | None = None
    user: UserRef | None = None
    created_at: datetime
    source: Literal["user", "simulation"] = Field(
        "user", description="simulation — решение смоделировано (журнала ОДС нет), в интерфейсе — «смоделировано»"
    )


# ---------- Здоровье и вход ----------


class HealthOut(Schema):
    status: Literal["ok", "degraded"]
    version: str
    model_version: str | None = Field(None, examples=["lgbm-2026.09-demo"])
    database: Literal["ok", "error"]
    time: datetime
    demo: bool = Field(description="Демо-режим (APP_ENV=demo): на экране входа доступен быстрый выбор роли")


class LoginIn(Schema):
    username: str = Field(min_length=1, max_length=64, examples=["dispatcher"])
    password: str = Field(min_length=1, max_length=256, examples=["dispatcher123"])


class ScopeNode(Schema):
    id: int = Field(examples=[5])
    name: str = Field(examples=["объект Альфа"])
    level: int = Field(description="1 — район, 2 — объект-комплекс, 3 — подобъект", examples=[2])


class UserOut(Schema):
    id: int
    username: str
    full_name: str
    role: Role = Field(description="Основная роль (первая из roles) — для совместимости")
    role_label: str = Field(examples=["Техник"], description="Подписи всех ролей через «+»")
    roles: list[Role] = Field(description="Все роли пользователя — права складываются")
    scope: list[ScopeNode] = Field(description="Узлы области видимости (со всем поддеревом)")
    unrestricted: bool = Field(description="Видит все объекты (есть роль ОДС, администратора или инженера)")
    scope_label: str = Field(examples=["объект Альфа"], description="Область одной строкой для шапки")
    permissions: list[Permission] = Field(description="Права — объединение прав ролей")
    auth_source: Literal["local", "ldap"]
    is_active: bool


class UserAccessIn(Schema):
    roles: list[Role] = Field(min_length=1, max_length=6, description="Роли складываются")
    scope_object_ids: list[int] = Field(
        default_factory=list, max_length=200,
        description="Узлы области видимости (район или объект-комплекс); нужны, если есть роль с областью",
    )


class UserList(Schema):
    items: list[UserOut]


class TokenOut(Schema):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Срок жизни токена, секунды")
    user: UserOut


# ---------- Прогнозы ----------


class FactorHuman(Schema):
    """Причина прогноза языком диспетчера. Похожие признаки ML склеены в один довод."""

    text: str = Field(examples=["6 сообщений «Неисправен» за сутки (обычно 0)"])
    short: str = Field(description="Короткая версия, до 60 символов — для таблиц",
                       examples=["6 сообщений «Неисправен» за сутки (обычно 0)"])
    features: list[str] = Field(description="Коды признаков ML, из которых собрана фраза", examples=[["fault_msgs_24h"]])
    tech: list[str] = Field(description="Исходные технические формулировки ML — для аналитика",
                            examples=[["Сообщений о неисправности за сутки: 6 (обычно 0)"]])


class PredictionItem(Schema):
    """Элемент списка прогнозов — строго по примеру из ТЗ фронтенда (+ factors_human)."""

    prediction_id: int = Field(examples=[90211])
    channel: ChannelRef
    object: ObjectRef
    prob: float = Field(ge=0, le=1, examples=[0.87])
    health: int = Field(ge=0, le=100, examples=[13])
    risk_level: RiskLevel
    factors: list[str] = Field(
        description="Технические формулировки ML, как в ТЗ. Для показа диспетчеру — factors_human",
        examples=[["Сообщений о неисправности за сутки: 6 (обычно 0)"]],
    )
    factors_human: list[FactorHuman] = Field(
        default_factory=list, description="До трёх разных доводов понятным языком; первый — главная причина"
    )
    outcome: Outcome | None = Field(None, description="null — исход ещё неизвестен")
    decision: DecisionOut | None = None
    work_order_id: int | None = None
    kind_probs: dict[str, float] | None = Field(
        None, description="v3: вероятности видов отказа (link, fault, disconnected, value), сумма = prob; "
                          "null — у прогнозов v2 и уровня «норма»",
        examples=[{"link": 0.61, "fault": 0.02, "disconnected": 0.0, "value": 0.0}])
    likely_kind: str | None = Field(None, description="Самый вероятный вид отказа (код из kind_probs)")
    likely_kind_label: str | None = Field(None, examples=["Вероятнее всего: потеря связи (97 % вероятности отказа)"])


class PredictionList(Schema):
    at: datetime
    total: int
    items: list[PredictionItem]


class FactorDetail(Schema):
    feature: str = Field(examples=["fault_msgs_24h"])
    value: float | str | None = Field(None, examples=[6])
    norm: float | str | None = Field(None, examples=[0])
    phrase: str = Field(examples=["Сообщений о неисправности за сутки: 6 (обычно 0)"])


class PredictionDetail(PredictionItem):
    at: datetime = Field(description="Момент, на который сделан прогноз")
    horizon_h: int = 24
    model_version: str
    factors_detail: list[FactorDetail]
    decisions: list[DecisionOut] = Field(description="История решений, новые сверху")


class DecisionIn(Schema):
    decision_type: DecisionType = Field(description="dispatch — выезд, false_alarm — ложное, monitor — наблюдение")
    reason_id: int = Field(description="Причина из справочника /api/reasons — обязательна")
    comment: str | None = Field(None, max_length=2000)


# ---------- Объекты и датчики ----------


RiskCounts = Annotated[dict[RiskLevel, int], Field(examples=[{"normal": 40, "attention": 3, "risk": 1, "critical": 0}])]


class ObjectNode(Schema):
    id: int
    name: str
    level: int = Field(description="1 — район, 2 — объект, 3 — подобъект")
    kind: str = Field(examples=["guardObject"])
    parent_id: int | None
    max_risk_level: RiskLevel | None = Field(None, description="Максимальный риск среди датчиков внутри; null — нет прогнозов")
    risk_counts: RiskCounts
    channels_count: int
    children: list["ObjectNode"] = []


class ObjectTree(Schema):
    at: datetime
    snapshot_at: datetime | None = Field(None, description="Момент среза прогнозов, использованного для раскраски")
    items: list[ObjectNode]


class ChannelPredictionShort(Schema):
    prediction_id: int
    prob: float
    health: int
    risk_level: RiskLevel


class ChannelListItem(Schema):
    id: int
    name: str
    sensor_type: str
    system_type: str
    tag: str
    object: ObjectRef
    prediction: ChannelPredictionShort | None = None


class ChannelList(Schema):
    at: datetime
    total: int
    items: list[ChannelListItem]


class ObjectDetail(Schema):
    at: datetime
    object: ObjectNode = Field(description="Объект без вложенных детей")
    path: list[str]
    children: list[ObjectNode] = Field(description="Дочерние объекты (без внуков)")
    channels: ChannelList = Field(description="Датчики объекта и всех вложенных, по убыванию риска")


class EventOut(Schema):
    ts: datetime
    is_alarm: bool
    value: str
    value_num: float | None = None


class WorkOrderShort(Schema):
    id: int
    number: str
    status: WorkOrderStatus
    priority: WorkOrderPriority
    due_at: datetime | None


class ChannelCard(Schema):
    at: datetime
    id: int
    name: str
    sensor_type: str
    system_type: str
    tag: str
    tag_levels: list[str] = Field(examples=[["847", "1", "1", "131", "2"]])
    object: ObjectRef
    prediction: PredictionDetail | None = Field(None, description="Актуальный прогноз на момент at")
    last_event: EventOut | None = None
    on_watch: bool = Field(description="Датчик на контроле (последнее решение — «Наблюдение»)")
    open_work_orders: list[WorkOrderShort]


class HistoryPoint(Schema):
    t: datetime = Field(description="Начало интервала (сутки или час)")
    events_count: int
    alarm_count: int
    fault_count: int
    uncertain_count: int
    status_changes: int
    value_avg: float | None = None
    value_min: float | None = None
    value_max: float | None = None


class FaultMark(Schema):
    ts: datetime = Field(description="Момент отказа; для пропадания связи — когда пауза превысила личную норму канала")
    kind: str = Field(
        description="Вид отказа по разметке ML: «Неисправен», «Отключено устройство», «Пропадание связи» или (v3) «Сбой значения»",
        examples=["Пропадание связи"],
    )


class PredictionPoint(Schema):
    prediction_id: int
    at: datetime
    prob: float
    health: int
    risk_level: RiskLevel


class ChannelHistory(Schema):
    channel_id: int
    at: datetime
    date_from: datetime
    date_to: datetime
    granularity: Literal["day", "hour"]
    points: list[HistoryPoint]
    faults: list[FaultMark] = Field(description="Отметки отказов для графика")
    predictions: list[PredictionPoint] = Field(description="История прогнозов по датчику за период")


# ---------- Дашборд ----------


class Accuracy(Schema):
    precision: float | None
    recall: float | None
    true_positive: int
    false_positive: int
    false_negative: int
    threshold: float
    window_from: datetime
    window_to: datetime


class DashboardSummary(Schema):
    at: datetime
    snapshot_at: datetime | None = Field(None, description="Момент последнего среза прогнозов не позже at")
    model_version: str | None
    channels_total: int
    channels_at_risk: int = Field(description="Датчиков с уровнем «риск» или «критично»")
    predicted_failures_24h: int = Field(description="Прогнозов отказа на 24 ч (вероятность ≥ рабочего порога)")
    expected_failures_24h: float = Field(description="Ожидаемое число отказов — сумма вероятностей")
    open_work_orders: int
    accuracy_30d: Accuracy
    risk_distribution: RiskCounts


# ---------- Журнал ----------


class JournalRecommendation(Schema):
    """Рекомендация по ТО в строке журнала — коротко; полная — GET /api/predictions/{id}/recommendation."""

    rule_id: str = Field(examples=["mass_link_loss"])
    title: str = Field(examples=["Массовая потеря связи в объекте"])
    action: str
    priority: WorkOrderPriority
    fault_kind_label: str = Field(examples=["Потеря связи"])


class JournalItem(Schema):
    prediction_id: int
    at: datetime
    channel: ChannelRef
    object: ObjectRef
    prob: float
    health: int
    risk_level: RiskLevel
    factors: list[str]
    factors_human: list[FactorHuman] = Field(default_factory=list)
    decision: DecisionOut | None = None
    outcome: Outcome | None = None
    work_order_id: int | None = None
    kind_probs: dict[str, float] | None = None
    likely_kind: str | None = None
    likely_kind_label: str | None = None
    work_order_number: str | None = Field(None, examples=["ЗН-2026-000123"])
    work_order_status: WorkOrderStatus | None = None
    recommendation: JournalRecommendation | None = Field(None, description="Рекомендация по ТО по правилам (проект правил)")


class JournalList(Schema):
    at: datetime
    total: int
    items: list[JournalItem]


# ---------- Справочники ----------


class ReasonOut(Schema):
    id: int
    code: str
    name: str
    decision_type: DecisionType | None = Field(None, description="Для какого решения; null — для любого")


class RecommendationOut(Schema):
    id: int
    code: str
    sensor_type: str | None
    text: str


class CodeLabel(Schema):
    code: str
    name: str


class RiskLevelOut(CodeLabel):
    color: str


class SensorTypeOut(Schema):
    name: str
    system_type: str
    channels_count: int


class Dictionaries(Schema):
    risk_levels: list[RiskLevelOut]
    decision_types: list[CodeLabel]
    work_order_statuses: list[CodeLabel]
    work_order_priorities: list[CodeLabel]
    roles: list[CodeLabel]
    system_types: list[str]
    sensor_types: list[SensorTypeOut]


# ---------- Заявки ----------


class WorkOrderIn(Schema):
    prediction_id: int | None = Field(None, description="Из какого прогноза создаётся черновик — поля заполнятся сами")
    channel_id: int | None = Field(None, description="Нужен, если заявка не из прогноза")
    reason_id: int | None = None
    recommendation_id: int | None = None
    recommendation_text: str | None = Field(None, max_length=4000)
    priority: WorkOrderPriority | None = Field(None, description="По умолчанию — из уровня риска")
    due_at: datetime | None = Field(None, description="По умолчанию — из приоритета")
    description: str | None = Field(None, max_length=4000)
    assignee: str | None = Field(None, max_length=255)

    @model_validator(mode="after")
    def _source_required(self):
        if self.prediction_id is None and self.channel_id is None:
            raise ValueError("Укажите prediction_id или channel_id")
        return self


class WorkOrderPatch(Schema):
    status: WorkOrderStatus | None = None
    priority: WorkOrderPriority | None = None
    reason_id: int | None = None
    recommendation_id: int | None = None
    recommendation_text: str | None = Field(None, max_length=4000)
    description: str | None = Field(None, max_length=4000)
    assignee: str | None = Field(None, max_length=255)
    due_at: datetime | None = None


class RecommendationRef(Schema):
    id: int
    text: str


class WorkOrderOut(Schema):
    id: int
    number: str = Field(examples=["ЗН-2026-000042"])
    status: WorkOrderStatus
    status_label: str
    priority: WorkOrderPriority
    priority_label: str
    prediction_id: int | None
    channel: ChannelRef
    object: ObjectRef
    reason: ReasonRef | None
    recommendation: RecommendationRef | None
    recommendation_text: str | None
    description: str | None
    assignee: str | None
    due_at: datetime | None
    created_by: UserRef | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None


class WorkOrderList(Schema):
    at: datetime
    total: int
    items: list[WorkOrderOut]


# ---------- Модель ----------


class MetricValues(Schema):
    precision: float | None
    recall: float | None
    f1: float | None
    pr_auc: float | None
    support: int | None = Field(None, description="Число положительных примеров (отказов)")


class SensorTypeMetrics(MetricValues):
    sensor_type: str


class PrecisionAtK(Schema):
    k: int
    precision: float


class ThresholdRow(Schema):
    threshold: float
    precision: float
    recall: float
    alerts_per_day: float = Field(description="Тревог в сутки при этом пороге")
    tp_per_day: float = Field(description="Пойманных отказов в сутки")
    fp_per_day: float = Field(description="Лишних выездов в сутки")


class DailyFact(Schema):
    date: date
    predicted: int = Field(description="Датчиков с прогнозом отказа (вероятность ≥ порога) за сутки")
    actual: int = Field(description="Датчиков, у которых отказ реально случился")
    true_positive: int = Field(description="Из них предсказанных заранее")


class KindRecall(Schema):
    kind: str = Field(examples=["Сбой значения"])
    recall: float | None
    support: int | None


class GroupMetrics(MetricValues):
    group: str = Field(examples=["газовые"], description="Группа датчиков заказчика: газовые, пожарные, охранные, прочие")
    alerts_per_day: float | None = None


class ModelComparison(Schema):
    model: str = Field(examples=["v2"])
    label: str = Field(examples=["b"], description="На какой метке считано: b — новая (v3), v2 — старая")
    precision: float | None
    recall: float | None
    f1: float | None
    pr_auc: float | None
    alerts_per_day: float | None = None


class ModelMetrics(Schema):
    at: datetime
    model_version: str | None
    threshold: float | None
    test_period_from: date | None
    test_period_to: date | None
    overall: MetricValues | None
    baseline: MetricValues | None = Field(None, description="Правило «был отказ за 7 дней — будет снова»")
    by_sensor_type: list[SensorTypeMetrics]
    precision_at_k: list[PrecisionAtK]
    median_lead_time_h: float | None
    pr_curve: list[ThresholdRow]
    daily: list[DailyFact] = Field(description="«Прогноз против факта» по дням за выбранный период")
    date_from: date
    date_to: date
    recall_by_kind: list[KindRecall] = Field(default_factory=list, description="v3: Recall по видам отказа на тесте")
    by_group: list[GroupMetrics] = Field(default_factory=list, description="v3: метрики по группам датчиков заказчика")
    comparison: list[ModelComparison] = Field(
        default_factory=list, description="v3: v2 и v3 на тесте на одной и той же метке (новой и старой)")
    label_note: str | None = Field(None, description="Почему изменилась метка — простыми словами")


class ThresholdTable(Schema):
    at: datetime
    model_version: str | None
    selected_threshold: float | None
    items: list[ThresholdRow]


# ---------- Уведомления ----------


class NotificationItem(Schema):
    """Уведомление о новом критичном прогнозе (канал не был критичным 24 ч до этого среза).

    kind = critical_summary — сводка «ещё N датчиков перешли в «Критично»» по одному срезу; prediction —
    самый вероятный из них, count — сколько датчиков в сводке."""

    id: int = Field(description="Совпадает с prediction_id")
    kind: Literal["critical_prediction", "critical_summary"] = "critical_prediction"
    count: int = Field(1, description="Сколько датчиков за уведомлением: 1 или размер сводки")
    created_at: datetime = Field(description="Момент прогноза (срез)")
    title: str = Field(examples=["Критический риск: Дым ПК 1101+2"])
    message: str = Field(examples=["объект Фита · вероятность отказа 87%"])
    prediction: PredictionItem


class SseTicketOut(Schema):
    ticket: str = Field(description="Одноразовый тикет для /api/notifications/stream?ticket=")
    expires_in: int = Field(examples=[60], description="Сколько секунд тикет действителен")


class NotificationGroup(Schema):
    snapshot_at: datetime = Field(description="Срез прогнозов")
    count: int = Field(description="Сколько датчиков перешли в «Критично» в этом срезе")


class NotificationList(Schema):
    at: datetime
    total: int = Field(description="Всего новых переходов в «Критично» за период")
    items: list[NotificationItem]
    groups: list[NotificationGroup] = Field(default_factory=list, description="Переходы по срезам, новые сверху")


# ---------- Поток событий и импорт ----------


class EventIn(Schema):
    event_id: int | None = Field(None, description="ид_события из системы мониторинга")
    channel_id: int = Field(examples=[334609])
    ts: datetime = Field(examples=["2026-08-01T12:00:05"])
    is_alarm: bool | Annotated[str, StringConstraints(max_length=8)] = Field(False, description="true/false, t/f")
    value: Annotated[str, StringConstraints(max_length=1024)] | float = Field(examples=["Норма"])


class EventsIn(Schema):
    events: list[EventIn] = Field(max_length=10000)


class IngestResult(Schema):
    received: int
    accepted: int
    duplicates: int
    skipped_unknown_channel: int
    skipped_invalid: int
    affected_channels: list[int]


class ImportError_(Schema):
    row: int = Field(description="Номер строки в файле (с учётом заголовка)")
    message: str


class ImportResult(Schema):
    kind: str
    file_name: str
    rows_total: int
    rows_imported: int
    rows_skipped: int
    errors: list[ImportError_] = Field(description="Первые 50 ошибок")


# ---------- Симуляция потока ----------


class SimStartIn(Schema):
    day: date = Field(examples=["2026-06-15"], description="Сутки для проигрывания; в базе должны быть прогнозы за них")
    speed: int = Field(60, ge=1, le=600, examples=[60], description="Ускорение: ×60 — сутки за 24 минуты")


class SimStateOut(Schema):
    enabled: bool = Field(description="Симуляция разрешена на сервере (SIM_ENABLED)")
    active: bool
    day: date | None = Field(None, description="Проигрываемые (или последние проигранные) сутки")
    speed: int
    model_time: datetime | None = Field(
        None, description="Модельное время: при активной симуляции — «сейчас» для запросов без at"
    )
    snapshot_at: datetime | None = Field(None, description="Последний срез прогнозов, по которому разосланы уведомления")
    started_at: datetime | None = None
    started_by: str | None = None
    stopped_at: datetime | None = None
    stop_reason: str | None = Field(
        None, description="manual — остановлена вручную, finished — сутки закончились, restart — перезапуск API"
    )
    events_received: int = Field(description="Событий получено через /api/ingest/events за эту симуляцию")
    events_accepted: int = Field(description="Из них новых (остальное — дубли уже загруженных)")
    critical_sent: int = Field(description="Уведомлений о новых критических прогнозах разослано в SSE")
    available_from: date | None = Field(None, description="Первые сутки с прогнозами")
    available_to: date | None = Field(None, description="Последние сутки с прогнозами")
    default_day: date | None = Field(None, description="Сутки по умолчанию: последние полные сутки с прогнозами")
    note: str = Field(description="Что именно делает симуляция")


# ---------- Рекомендации по ТО ----------

FaultKind = Literal["link", "fault", "disconnected", "value", "group", "power", "drift", "unknown"]


class MaintenanceAdvice(Schema):
    """Рекомендация по техническому обслуживанию: прозрачные правила (backend/app/recommendations/rules.yaml),
    не ML. Проект правил — требует согласования со специалистами эксплуатации."""

    prediction_id: int
    channel_id: int
    at: datetime = Field(description="Срез прогноза: все факты взяты на этот момент")
    rule_id: str = Field(examples=["mass_link_loss"])
    title: str = Field(examples=["Массовая потеря связи в объекте"])
    action: str = Field(description="Что сделать")
    reason: str = Field(description="Обоснование простыми словами со ссылкой на факты и вывод")
    facts: list[str] = Field(description="Факты обоснования по отдельности")
    conclusion: str
    priority: WorkOrderPriority
    priority_label: str
    due_hours: float = Field(description="Рекомендуемый срок от среза прогноза, часов")
    due_at: datetime
    assignee: str = Field(description="Кому: служба или бригада")
    avoid: list[str] = Field(default_factory=list, description="Чего не делать")
    fault_kind: FaultKind = Field(description="Предполагаемый вид отказа")
    fault_kind_label: str
    fault_kind_basis: str = Field(description="На чём основан вид отказа")
    plan: bool = Field(description="Профилактическая работа — попадает во вкладку «Рекомендованные работы»")
    work_order_needed: bool = Field(description="false — заявка не нужна (норма, наблюдение)")
    recommendation_id: int | None = Field(None, description="Строка справочника recommendations (code = rule:<rule_id>)")
    source: str = Field(examples=["Проект правил, требует согласования со специалистами эксплуатации"])
    rules_version: str


class MaintenancePlanItem(Schema):
    prediction_id: int
    channel: ChannelRef
    prob: float
    risk_level: RiskLevel
    advice: MaintenanceAdvice


class MaintenancePlanGroup(Schema):
    object: ObjectRef
    top_priority: WorkOrderPriority
    items: list[MaintenancePlanItem]


class MaintenancePlan(Schema):
    at: datetime
    snapshot_at: datetime | None
    total_items: int
    total_objects: int
    groups: list[MaintenancePlanGroup]
    source: str


class MaintenanceDraftsIn(Schema):
    prediction_ids: list[int] = Field(min_length=1, max_length=100, description="Прогнозы из «Рекомендованных работ»")


class MaintenanceDraftSkipped(Schema):
    prediction_id: int
    reason: str


class MaintenanceDraftsOut(Schema):
    created: list[WorkOrderOut]
    skipped: list[MaintenanceDraftSkipped]


# ---------- Настройки ----------


class SettingsIn(Schema):
    """Настраиваемые параметры. Границы уровней риска — по вероятности отказа, должны возрастать."""

    risk_attention: float = Field(gt=0, lt=1, examples=[0.2], description="«Внимание» — вероятность не ниже")
    risk_risk: float = Field(gt=0, lt=1, examples=[0.5], description="«Риск» — вероятность не ниже")
    risk_critical: float = Field(gt=0, lt=1, examples=[0.8], description="«Критично» — вероятность не ниже")
    notify_cooldown_hours: int = Field(ge=1, le=168, examples=[24], description="Повторное уведомление по датчику — не чаще, часов")
    notify_per_slice: int = Field(ge=1, le=20, examples=[3], description="Лента и колокольчик: отдельных уведомлений на срез")
    notify_sim_per_slice: int = Field(ge=1, le=50, examples=[5], description="Симуляция: всплывающих уведомлений на срез")

    @model_validator(mode="after")
    def _ordered(self):
        if not self.risk_attention < self.risk_risk < self.risk_critical:
            raise ValueError("Границы должны возрастать: «внимание» < «риск» < «критично»")
        return self


class SettingsOut(SettingsIn):
    custom_risk_bounds: bool = Field(description="false — границы по умолчанию: уровень риска — тот, что записал ML")
    defaults: SettingsIn
    updated_at: datetime | None = None
    updated_by: str | None = None


# ---------- Схема коллекторов (синтетическая геометрия) ----------


class GeoFeature(Schema):
    type: Literal["Feature"] = "Feature"
    geometry: dict = Field(description="GeoJSON-геометрия в условных метрах: Point, LineString, MultiLineString")
    properties: dict


class GeoBase(Schema):
    type: Literal["FeatureCollection"] | None = Field(None, description="Есть при format=geojson")
    synthetic: bool = Field(True, description="Всегда true: геометрия синтетическая, координат у заказчика нет")
    crs_note: str
    at: datetime
    snapshot_at: datetime | None = None
    features: list[GeoFeature] | None = Field(None, description="format=geojson")
    items: list[dict] | None = Field(None, description="format=wkt: свойства объекта и строка `wkt`")


class GeoCollectors(GeoBase):
    stats: dict = Field(description="Покрытие разбора пикетов: channels, with_picket, on_axis, in_gallery, in_node, …")


class GeoChannels(GeoBase):
    total: int


# ---------- Журнал тревожных событий ----------


class EventTag(Schema):
    code: Literal["planned_check", "group_off", "value_failure"]
    label: str = Field(examples=["Вероятная плановая проверка"])
    reason: str = Field(description="Почему правило сработало — факты, языком диспетчера")


class EventItem(Schema):
    id: int
    ts: datetime
    channel: ChannelRef
    object: ObjectRef
    picket: str | None = Field(None, examples=["ПК447+5"], description="Пикет из названия датчика (docs/GEO_SCHEME.md)")
    value: str = Field(description="Значение как в журнале заказчика")
    value_num: float | None = None
    value_kind: Literal["status", "number", "service", "date"]
    value_kind_label: str
    is_alarm: bool = Field(description="Флаг «тревожное» из журнала заказчика")
    tags: list[EventTag] = Field(description="Контекст по прозрачным правилам (не ML), app/event_context.py")


class EventList(Schema):
    at: datetime
    date_from: datetime
    date_to: datetime
    total: int
    items: list[EventItem]
