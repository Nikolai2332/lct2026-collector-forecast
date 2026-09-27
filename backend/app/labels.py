"""Подписи для интерфейса на русском и границы зон риска."""

RISK_LABELS = {
    "normal": ("Норма", "green"),
    "attention": ("Внимание", "yellow"),
    "risk": ("Риск", "orange"),
    "critical": ("Критично", "red"),
}
RISK_ORDER = {"normal": 0, "attention": 1, "risk": 2, "critical": 3}
ALERT_LEVELS = ("risk", "critical")

DECISION_LABELS = {
    "dispatch": "Выезд бригады",
    "false_alarm": "Ложное срабатывание",
    "monitor": "Наблюдение",
}
WORK_ORDER_STATUS_LABELS = {
    "draft": "Черновик",
    "submitted": "Отправлена",
    "in_progress": "В работе",
    "done": "Выполнена",
    "cancelled": "Отменена",
}
WORK_ORDER_PRIORITY_LABELS = {
    "low": "Низкий",
    "medium": "Средний",
    "high": "Высокий",
    "critical": "Аварийный",
}
ROLE_LABELS = {
    "dispatcher_ods": "Диспетчер ОДС",
    "dispatcher": "Диспетчер района",
    "technician": "Техник",
    "engineer": "Инженер данных",
    "manager": "Руководитель",
    "admin": "Администратор",
}
# Виды отказа (коды kind_probs модели v3 и движка рекомендаций) → подпись для диспетчера
FAULT_KIND_TEXT = {
    "link": "потеря связи",
    "fault": "«Неисправен» — потеря связи с устройством",
    "disconnected": "«Отключено устройство»",
    "value": "сбой значения (служебный код вместо показания)",
}


def likely_kind(kind_probs: dict | None) -> tuple[str, str] | None:
    """Самый вероятный вид отказа по kind_probs: (код, «Вероятнее всего: …»)."""
    if not kind_probs:
        return None
    items = [(k, float(v)) for k, v in kind_probs.items() if k in FAULT_KIND_TEXT and v is not None]
    if not items:
        return None
    total = sum(v for _, v in items)
    code, p = max(sorted(items), key=lambda kv: kv[1])
    share = f" ({round(100 * p / total)} % вероятности отказа)" if total > 0 else ""
    return code, f"Вероятнее всего: {FAULT_KIND_TEXT[code]}{share}"


OPEN_WORK_ORDER_STATUSES = ("draft", "submitted", "in_progress")

# Разрешённые переходы статусов заявки
WORK_ORDER_TRANSITIONS = {
    "draft": {"submitted", "cancelled"},
    "submitted": {"in_progress", "draft", "cancelled"},
    "in_progress": {"done", "cancelled"},
    "done": set(),
    "cancelled": set(),
}


def risk_level_from_health(health: int) -> str:
    """Границы из ТЗ ML: 80+ норма, 50–79 внимание, 20–49 риск, ниже 20 критично."""
    if health >= 80:
        return "normal"
    if health >= 50:
        return "attention"
    if health >= 20:
        return "risk"
    return "critical"


def health_from_prob(prob: float) -> int:
    return round(100 * (1 - prob))
