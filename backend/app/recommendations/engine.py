"""Рекомендации по ТО на прозрачных правилах (не ML).

Вход — прогноз и то, что о датчике известно на момент прогноза: тип и система, уровень риска и вероятность,
причины прогноза (`top_factors`), история отказов канала за 30/90 дней, состояние соседей по объекту
(потери связи, «Обесточен» у фаз питания), заявки по датчику и точность модели по типу датчика.
Выход — действие, обоснование со ссылкой на факты, приоритет, срок, исполнитель и «чего не делать».

Правила — в rules.yaml рядом (проект правил, требует согласования со специалистами эксплуатации).
Все факты берутся строго на момент прогноза `p.at` — «машина времени» и симуляция не видят будущего.
Одинаковые входы → одинаковый выход: в движке нет случайности и зависимости от текущего времени.
"""

from __future__ import annotations

import bisect
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.dbcompat import reset_sequences
from app.factors_human import HumanFactor, _f, group_of, humanize, num, plural
from app.labels import OPEN_WORK_ORDER_STATUSES, RISK_LABELS, RISK_ORDER, WORK_ORDER_PRIORITY_LABELS, WORK_ORDER_STATUS_LABELS
from app.models import Channel, ChannelDaily, ChannelFault, EventRecent, ModelMetric, Prediction, Recommendation, WorkOrder
from app.risk import level_in, level_of

RULES_PATH = Path(__file__).with_name("rules.yaml")
PRIORITIES = ("low", "medium", "high", "critical")
PRIORITY_BY_RISK = {"critical": "critical", "risk": "high", "attention": "medium", "normal": "low"}

FAULT_KIND_LABELS = {
    "link": "Потеря связи",
    "fault": "«Неисправен» (связь с устройством)",
    "disconnected": "«Отключено устройство»",
    "value": "Сбой значения",
    "group": "Групповое отключение (плановые работы)",
    "power": "Питание",
    "drift": "Недостоверные показания",
    "unknown": "Не определён",
}
# Виды отказов разметки ML (channel_faults.kind) → вид для правил
KIND_OF_FAULT = {"Пропадание связи": "link", "Неисправен": "fault", "Отключено устройство": "fault",
                 "Сбой значения": "value"}
STATUS_FAULT = ("Неисправен", "Отключено устройство")
STATUS_POWER_OFF = "Обесточен"

CONDITIONS = {
    "kind", "group", "not_group", "min_risk", "max_risk", "mass_link", "faults_30d_min", "faults_90d_min",
    "same_kind_90d_min", "faults_since_repair_min", "low_precision_type", "flapping", "stuck_share_min",
    "object_power_off_min",
}
VALUE_FAIL_GROUPS = {"sentinel", "date1970", "date_current", "gas5", "value_failures"}
FACTS = {"current", "silence", "neighbors", "power", "fault_msgs", "flap", "repeats", "repair", "precision", "stuck", "values",
         "group_silence", "value_fail"}
CHUNK = 5000  # IN-списки режем: у SQLite ограничение на число параметров


# ---------- Правила ----------


@dataclass(frozen=True)
class Rule:
    id: str
    title: str
    when: dict
    action: str
    facts: tuple[str, ...]
    conclusion: str
    priority: str
    priority_min: str | None
    due_hours: float | None
    assignee: str
    avoid: str | None
    plan: bool
    work_order: bool
    per_object: bool
    due_limit: str | None = None  # набор регламентных сроков (due_limits)

    @property
    def code(self) -> str:
        return f"rule:{self.id}"


@dataclass(frozen=True)
class Norm:
    """Регламентный срок устранения неисправности («не более»)."""

    id: str
    hours: float
    label: str
    clause: str
    title: str
    note: str | None = None


@dataclass(frozen=True)
class LimitCase:
    """Случай набора регламентных сроков: условия (как в `when`) → норма. direct=False — «по аналогии»."""

    when: dict
    norm: str
    direct: bool


@dataclass(frozen=True)
class RuleSet:
    version: str
    source: str
    params: dict
    groups: dict[str, tuple[str, ...]]
    rules: tuple[Rule, ...]
    modifiers: dict[str, str]
    norms: dict[str, Norm] = field(default_factory=dict)
    due_limits: dict[str, tuple[LimitCase, ...]] = field(default_factory=dict)
    regulation: str = ""  # краткое название регламента для основания срока
    # Группы по типу датчика считаются один раз: типов меньше двух десятков, а вызовов — тысячи
    _groups_memo: dict = field(default_factory=dict, compare=False, repr=False)

    def groups_of(self, sensor_type: str) -> frozenset[str]:
        memo = self._groups_memo.get(sensor_type)
        if memo is None:
            low = (sensor_type or "").lower() + " "
            memo = frozenset(g for g, words in self.groups.items() if any(w in low for w in words))
            self._groups_memo[sensor_type] = memo
        return memo

    def rule(self, rule_id: str) -> Rule:
        return next(r for r in self.rules if r.id == rule_id)


def parse_rules(data: dict) -> RuleSet:
    """Проверяет файл правил: неизвестные условия, факты и приоритеты — ошибка при загрузке, а не молчаливый пропуск."""
    params = dict(data.get("params") or {})
    params.setdefault("mass_link_neighbors", 3)
    params.setdefault("mass_link_share", 0.08)
    params.setdefault("power_quiet_days", 7)
    params.setdefault("low_precision", 0.5)
    params.setdefault("flap_changes", 10)
    params.setdefault("repair_days", 14)
    params.setdefault("due_hours", {"critical": 4, "high": 24, "medium": 72, "low": 168})
    params.setdefault("min_priority_by_group", {})
    params.setdefault("group_silence_share", 0.5)
    groups = {g: tuple(str(w).lower() for w in words) for g, words in (data.get("sensor_groups") or {}).items()}
    reg = data.get("regulation") or {}
    norms = {
        str(nid): Norm(id=str(nid), hours=float(n["hours"]), label=str(n.get("label") or f"{n['hours']} ч"),
                       clause=str(n["clause"]), title=str(n.get("title") or ""), note=n.get("note"))
        for nid, n in (reg.get("norms") or {}).items()
    }
    due_limits: dict[str, tuple[LimitCase, ...]] = {}
    for name, cases in (data.get("due_limits") or {}).items():
        parsed = []
        for case in cases or []:
            case = dict(case)
            nid, direct = str(case.pop("norm", "")), bool(case.pop("direct", False))
            if nid not in norms:
                raise ValueError(f"Набор сроков {name}: неизвестная норма регламента {nid!r}")
            if set(case) - CONDITIONS:
                raise ValueError(f"Набор сроков {name}: неизвестные условия {sorted(set(case) - CONDITIONS)}")
            if set(case.get("group", [])) - set(groups):
                raise ValueError(f"Набор сроков {name}: неизвестная группа датчиков")
            parsed.append(LimitCase(when=case, norm=nid, direct=direct))
        due_limits[str(name)] = tuple(parsed)
    rules, seen = [], set()
    for raw in data.get("rules") or []:
        rid = str(raw["id"])
        when = dict(raw.get("when") or {})
        unknown = set(when) - CONDITIONS
        if unknown:
            raise ValueError(f"Правило {rid}: неизвестные условия {sorted(unknown)}")
        facts = tuple(raw.get("facts") or ())
        if set(facts) - FACTS:
            raise ValueError(f"Правило {rid}: неизвестные факты {sorted(set(facts) - FACTS)}")
        priority = str(raw.get("priority", "from_risk"))
        if priority not in ("from_risk", "raise", *PRIORITIES):
            raise ValueError(f"Правило {rid}: приоритет {priority}")
        pmin = raw.get("priority_min")
        if pmin is not None and pmin not in PRIORITIES:
            raise ValueError(f"Правило {rid}: priority_min {pmin}")
        for key in ("group", "not_group"):
            if set(when.get(key, [])) - set(groups):
                raise ValueError(f"Правило {rid}: неизвестная группа датчиков в {key}")
        limit = raw.get("due_limit")
        if limit is not None and limit not in due_limits:
            raise ValueError(f"Правило {rid}: неизвестный набор регламентных сроков {limit!r}")
        if rid in seen:
            raise ValueError(f"Правило {rid} повторяется")
        seen.add(rid)
        rules.append(Rule(
            id=rid, title=str(raw["title"]), when=when, action=str(raw["action"]).strip(), facts=facts,
            conclusion=str(raw.get("conclusion") or "").strip(), priority=priority, priority_min=pmin,
            due_hours=float(raw["due_hours"]) if raw.get("due_hours") is not None else None,
            assignee=str(raw.get("assignee") or "—"), avoid=raw.get("avoid"), plan=bool(raw.get("plan", False)),
            work_order=bool(raw.get("work_order", True)), per_object=bool(raw.get("per_object", False)),
            due_limit=str(limit) if limit is not None else None,
        ))
    if not rules or rules[-1].when:
        raise ValueError("Последнее правило должно быть без условий (when: {}) — чтобы рекомендация была всегда")
    modifiers = {str(m["id"]): str(m["avoid"]) for m in data.get("modifiers") or []}
    return RuleSet(str(data.get("version", "")), str(data.get("source", "")), params, groups, tuple(rules), modifiers,
                   norms=norms, due_limits=due_limits, regulation=str(reg.get("short") or ""))


_lock = threading.Lock()
_cache: tuple[float, RuleSet] | None = None


def load_rules(path: Path = RULES_PATH) -> RuleSet:
    """Правила перечитываются, если файл изменился (по времени изменения) — без перезапуска API."""
    global _cache
    mtime = path.stat().st_mtime
    with _lock:
        if _cache is not None and _cache[0] == mtime and path == RULES_PATH:
            return _cache[1]
    rules = parse_rules(yaml.safe_load(path.read_text(encoding="utf-8")))
    if path == RULES_PATH:
        with _lock:
            _cache = (mtime, rules)
    return rules


# ---------- Вход движка ----------


@dataclass
class Signals:
    """Факты о датчике и его соседях на момент прогноза (из БД)."""

    faults_30d: Counter = field(default_factory=Counter)  # вид отказа разметки ML → число
    faults_90d: Counter = field(default_factory=Counter)
    fault_msgs_24h: int = 0  # сообщений «Неисправен» / «Отключено устройство» за сутки
    power_off_24h: int = 0  # своих сообщений «Обесточен» за сутки, если до этого неделю их не было
    neighbors_silent: int = 0  # других датчиков объекта, у которых тоже пропадает связь
    object_channels: int = 1  # датчиков в объекте, включая этот
    same_type_channels: int = 1  # датчиков того же типа в объекте, включая этот
    same_type_silent: int = 0  # из них (кроме этого) без связи — для «группового отключения»
    object_power_off: int = 0  # датчиков фаз объекта, у которых «Обесточен» появилось за сутки впервые за неделю
    open_order: str | None = None  # «ЗН-2026-000012 (Черновик)»
    last_repair: tuple[str, datetime, int] | None = None  # номер, когда выполнена, отказов после
    recent_visit: str | None = None  # выполнена меньше суток назад и отказов после не было
    type_precision: float | None = None
    history_source: str = "channel_faults"  # или channel_daily (демо: дни с сообщениями о неисправности)


@dataclass
class Context:
    prediction_id: int | None
    at: datetime
    sensor_type: str
    system_type: str
    risk_level: str
    prob: float
    top_factors: list
    signals: Signals
    # Вероятности видов отказа модели v3 ({link, fault, disconnected, value}); None — вид выводится из причин
    kind_probs: dict[str, float] | None = None


@dataclass
class Advice:
    rule_id: str
    title: str
    action: str
    reason: str
    facts: list[str]
    conclusion: str
    priority: str
    priority_label: str
    due_hours: float
    due_at: datetime
    assignee: str
    avoid: list[str]
    fault_kind: str
    fault_kind_label: str
    fault_kind_basis: str
    plan: bool
    work_order: bool
    per_object: bool
    has_open_order: bool
    source: str
    rules_version: str
    recommendation_code: str
    recommendation_id: int | None = None
    due_limit_hours: float | None = None  # регламентный срок («не более»), если он есть для правила
    due_basis: str | None = None  # «по регламенту — не более 48 ч (ОПС, кат. III), РТЭКК п. …; по аналогии»


# ---------- Вид отказа: единственная точка ----------


def _factors(top_factors) -> dict[str, tuple[float | None, float | None]]:
    out = {}
    for item in top_factors or []:
        if isinstance(item, dict) and item.get("feature"):
            out[str(item["feature"])] = (_f(item.get("value")), _f(item.get("norm")))
    return out


def _short_window(feature: str) -> bool:
    return feature.endswith(("_1h", "_6h", "_24h"))


def is_flapping(factors: dict, params: dict) -> bool:
    for feat, (value, norm) in factors.items():
        if feat.startswith("status_changes_") and _short_window(feat) and value is not None:
            if value >= params["flap_changes"] and value >= 3 * max(norm or 0, 1):
                return True
    return False


def stuck_share(factors: dict) -> float | None:
    for feat, (value, _norm) in factors.items():
        if feat.startswith("stuck_share_") and value is not None:
            return value / 100 if value > 1 else value  # демо-сид отдаёт проценты, ML — доли
    return None


def group_silence_share(top_factors) -> float | None:
    for feat, (value, _norm) in _factors(top_factors).items():
        if feat == "nb_silent_share" and value is not None:
            return value
    return None


def infer_fault_kind(ctx: Context, rules: RuleSet) -> tuple[str, str]:
    """Предполагаемый вид отказа и на чём он основан.

    ЕДИНСТВЕННАЯ точка, где определяется вид отказа. Порядок:
    1) групповая тишина — среди причин прогноза «молчат ≥ group_silence_share однотипных датчиков объекта»
       или связь пропадает у такой же доли датчиков объекта (по данным на момент прогноза):
       это плановые работы или отключение объекта, а не отказ отдельного датчика (правило разметки v3);
    2) вероятности видов модели v3 (ctx.kind_probs) — самый вероятный вид;
    3) без них — вывод из причин прогноза и данных (прогнозы v2 и уровня «норма»).
    """
    share = group_silence_share(ctx.top_factors)
    if share is not None and share >= rules.params["group_silence_share"]:
        return "group", f"молчат {num(share * 100)} % однотипных датчиков объекта"
    others = ctx.signals.same_type_channels - 1
    if others >= 5 and ctx.signals.same_type_silent / others >= rules.params["group_silence_share"]:
        return "group", (f"связь пропадает у {ctx.signals.same_type_silent} из {others} других датчиков "
                         f"того же типа в объекте ({num(100 * ctx.signals.same_type_silent / others)} %)")
    if ctx.kind_probs:
        known = {k: float(v) for k, v in ctx.kind_probs.items() if k in FAULT_KIND_LABELS and v is not None}
        if known:
            kind, p = max(sorted(known.items()), key=lambda kv: kv[1])
            total = sum(known.values())
            return kind, f"по оценке модели ({num(100 * p / total if total else 0)} % вероятности отказа)"

    f, s = _factors(ctx.top_factors), ctx.signals
    groups = {group_of(name) for name, (v, _) in f.items() if v is None or v != 0}
    power_group = "power" in rules.groups_of(ctx.sensor_type)
    fault_now = s.fault_msgs_24h > 0 or any(
        g in ("fault_msgs", "disc_msgs") and _short_window(n) and (v or 0) > 0
        for n, (v, _) in f.items() for g in [group_of(n)]
    )
    power_now = (
        any(group_of(n) in ("power_off", "off_share", "phase_off") and (v or 0) > 0 for n, (v, _) in f.items())
        or (power_group and s.power_off_24h > 0)
    )
    link_now = bool(groups & {"silence", "link_losses", "past_gaps"})

    if power_now:
        return "power", "сообщения «Обесточен»"
    if link_now and s.object_power_off > 0:
        return "power", "датчик молчит, а у фаз питания объекта было «Обесточен»"
    if fault_now:
        return "fault", "сообщения «Неисправен» / «Отключено устройство»"
    if link_now:
        return "link", "датчик дольше обычного не выходит на связь"
    if is_flapping(f, rules.params):
        return "fault", "дребезг статуса"
    if groups & {"stuck", "values", "neighbors"} and (stuck_share(f) or "values" in groups or "neighbors" in groups):
        return "drift", "показания «застыли» или расходятся с соседями"
    if s.faults_90d:
        top = max(sorted(s.faults_90d.items()), key=lambda kv: kv[1])[0]
        if top in KIND_OF_FAULT:
            return KIND_OF_FAULT[top], f"чаще всего за 90 дней: «{top}»"
    return "unknown", "явного признака нет"


# ---------- Факты для обоснования ----------


def _human(ctx: Context) -> list[HumanFactor]:
    return humanize(ctx.top_factors, limit=10)


def _first_text(humans: list[HumanFactor], groups: set[str]) -> str | None:
    for h in humans:
        if {group_of(x) for x in h.features} & groups:
            return h.text
    return None


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def _fault_breakdown(c: Counter) -> str:
    parts = [f"{KIND_SHORT.get(k, k)} — {n}" for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ", ".join(parts)


KIND_SHORT = {"Пропадание связи": "потерь связи", "Неисправен": "«Неисправен»", "Отключено устройство": "«Отключено»",
              "Сбой значения": "сбоев значения"}


def build_facts(ctx: Context, kind: str, rules: RuleSet) -> dict[str, str]:
    s, humans = ctx.signals, _human(ctx)
    facts: dict[str, str] = {}
    facts["current"] = (
        f"вероятность отказа в ближайшие 24 ч — {num(ctx.prob * 100)} % ({RISK_LABELS[ctx.risk_level][0].lower()})"
    )
    silence = _first_text(humans, {"silence"}) or _first_text(humans, {"link_losses", "past_gaps"})
    if silence:
        facts["silence"] = silence
    others = s.object_channels - 1
    if s.neighbors_silent > 0 and is_mass_link(s, rules.params):
        facts["neighbors"] = (
            f"у {s.neighbors_silent} {plural(s.neighbors_silent, 'соседнего датчика', 'соседних датчиков', 'соседних датчиков')} "
            f"объекта (из {others}) тоже пропадает связь"
        )
    elif s.neighbors_silent > 0:
        facts["neighbors"] = (
            f"у {s.neighbors_silent} из {others} соседних датчиков объекта ({num(100 * s.neighbors_silent / max(others, 1))} %) "
            f"тоже пропадает связь — это обычный фон, массовой потери связи нет"
        )
    elif others > 0 and kind in ("link", "power", "unknown"):
        facts["neighbors"] = f"у остальных {others} {plural(others, 'датчика', 'датчиков', 'датчиков')} объекта признаков потери связи нет"
    if s.object_power_off > 0:
        facts["power"] = (
            f"у {s.object_power_off} {plural(s.object_power_off, 'датчика фазы', 'датчиков фаз', 'датчиков фаз')} "
            f"питания объекта за сутки появилось «Обесточен» (неделю до этого не было)"
        )
    elif s.power_off_24h > 0:
        facts["power"] = (
            f"датчик {s.power_off_24h} {plural(s.power_off_24h, 'раз', 'раза', 'раз')} сообщил «Обесточен» за сутки "
            f"(неделю до этого не было)"
        )
    elif text := _first_text(humans, {"power_off", "off_share", "phase_off"}):
        facts["power"] = text
    if s.fault_msgs_24h > 0:
        facts["fault_msgs"] = (
            f"за сутки {s.fault_msgs_24h} {plural(s.fault_msgs_24h, 'сообщение', 'сообщения', 'сообщений')} "
            f"«Неисправен» / «Отключено устройство»"
        )
    elif text := _first_text(humans, {"fault_msgs", "disc_msgs"}):
        facts["fault_msgs"] = text
    if is_flapping(_factors(ctx.top_factors), rules.params):
        facts["flap"] = _first_text(humans, {"status_changes"}) or "дребезг статуса"
    n30, n90 = sum(s.faults_30d.values()), sum(s.faults_90d.values())
    if s.history_source == "channel_daily":
        if n90:
            facts["repeats"] = f"дней с сообщениями о неисправности: {n30} за 30 дней, {n90} за 90 дней"
    elif n90:
        facts["repeats"] = f"отказов за 30 дней — {n30}, за 90 дней — {n90} ({_fault_breakdown(s.faults_90d)})"
    else:
        facts["repeats"] = "за 90 дней отказов не было"
    if s.last_repair:
        number, closed, after = s.last_repair
        facts["repair"] = f"по заявке {number} работы выполнены {closed:%d.%m.%Y}, после этого отказов — {after}"
    if s.type_precision is not None:
        facts["precision"] = (
            f"точность модели (Precision) для типа «{ctx.sensor_type}» — {num(s.type_precision, 2)}: "
            f"заметная часть тревог по таким датчикам ложные"
        )
    if text := _first_text(humans, {"stuck"}):
        facts["stuck"] = text
    if text := _first_text(humans, {"values", "neighbors"}):
        facts["values"] = text
    if text := _first_text(humans, {"group_silence"}):
        facts["group_silence"] = text
    if text := _first_text(humans, VALUE_FAIL_GROUPS):
        facts["value_fail"] = text
    elif kind == "value":
        facts["value_fail"] = "модель ожидает сбой значения: служебный код или «01.01.1970» вместо показания"
    return facts


# ---------- Сопоставление ----------


def matches(rule: Rule, ctx: Context, kind: str, rules: RuleSet) -> bool:
    return check_when(rule.when, ctx, kind, rules)


def check_when(w: dict, ctx: Context, kind: str, rules: RuleSet) -> bool:
    """Условия `when` правила или случая набора регламентных сроков."""
    s, p = ctx.signals, rules.params
    groups = rules.groups_of(ctx.sensor_type)
    base_kind = "fault" if kind == "disconnected" else kind  # «Отключено устройство» в истории — вид fault
    same_kind = sum(n for k, n in s.faults_90d.items() if KIND_OF_FAULT.get(k) == base_kind)
    if s.history_source == "channel_daily" and kind == "fault":
        same_kind = sum(s.faults_90d.values())
    checks = {
        "kind": lambda v: kind in v,
        "group": lambda v: bool(groups & set(v)),
        "not_group": lambda v: not groups & set(v),
        "min_risk": lambda v: RISK_ORDER[ctx.risk_level] >= RISK_ORDER[v],
        "max_risk": lambda v: RISK_ORDER[ctx.risk_level] <= RISK_ORDER[v],
        "mass_link": lambda v: is_mass_link(s, p) == bool(v),
        "faults_30d_min": lambda v: sum(s.faults_30d.values()) >= v,
        "faults_90d_min": lambda v: sum(s.faults_90d.values()) >= v,
        "same_kind_90d_min": lambda v: same_kind >= v,
        "faults_since_repair_min": lambda v: s.last_repair is not None and s.last_repair[2] >= v,
        "low_precision_type": lambda v: (s.type_precision is not None and s.type_precision < p["low_precision"]) == bool(v),
        "flapping": lambda v: is_flapping(_factors(ctx.top_factors), p) == bool(v),
        "stuck_share_min": lambda v: (stuck_share(_factors(ctx.top_factors)) or 0) >= v,
        "object_power_off_min": lambda v: s.object_power_off >= v,
    }
    return all(checks[k](v) for k, v in w.items())


def is_mass_link(s: Signals, params: dict) -> bool:
    """Массовая потеря связи: не меньше N соседей и заметная доля объекта. В реальных объектах по 100–700 датчиков,
    и 3–5 % из них молчат в любой срез — поэтому одного абсолютного числа мало."""
    others = max(s.object_channels - 1, 1)
    return s.neighbors_silent >= params["mass_link_neighbors"] and s.neighbors_silent / others >= params["mass_link_share"]


def _priority(rule: Rule, risk_level: str) -> str:
    base = PRIORITY_BY_RISK[risk_level]
    if rule.priority == "from_risk":
        pr = base
    elif rule.priority == "raise":
        pr = PRIORITIES[min(PRIORITIES.index(base) + 1, len(PRIORITIES) - 1)]
    else:
        pr = rule.priority
    if rule.priority_min and PRIORITIES.index(pr) < PRIORITIES.index(rule.priority_min):
        pr = rule.priority_min
    return pr


def _group_floor(priority: str, groups: set[str], params: dict) -> str:
    """Приоритет не ниже заданного для группы датчика (params.min_priority_by_group)."""
    for g in sorted(groups):
        floor = params["min_priority_by_group"].get(g)
        if floor in PRIORITIES and PRIORITIES.index(priority) < PRIORITIES.index(floor):
            priority = floor
    return priority


def due_text(hours: float) -> str:
    """Как на фронтенде (utils/time.ts, dueText): меньше 48 ч — в часах, дальше — в сутках."""
    return f"{round(hours)} ч" if hours < 48 else f"{round(hours / 24)} сут"


def regulation_limit(rule: Rule, ctx: Context, kind: str, rules: RuleSet) -> tuple[Norm, bool] | None:
    """Регламентный срок для правила и датчика: (норма, прямая ли она) или None — аналога в регламенте нет."""
    if rule.due_limit is None:
        return None
    for case in rules.due_limits.get(rule.due_limit, ()):
        if check_when(case.when, ctx, kind, rules):
            return rules.norms[case.norm], case.direct
    return None


def due_basis_text(norm: Norm, direct: bool, regulation: str) -> str:
    text = f"по регламенту — не более {norm.label}"
    if norm.title:
        text += f" ({norm.title})"
    text += f", {regulation} п. {norm.clause}" if regulation else f", п. {norm.clause}"
    if norm.note:
        text += f"; {norm.note}"
    return text if direct else text + "; по аналогии"


def advise(ctx: Context, rules: RuleSet | None = None) -> Advice:
    rules = rules or load_rules()
    kind, basis = infer_fault_kind(ctx, rules)
    rule = next(r for r in rules.rules if matches(r, ctx, kind, rules))
    facts_all = build_facts(ctx, kind, rules)
    facts = [facts_all[k] for k in rule.facts if facts_all.get(k)]
    text = "; ".join(_lower_first(x) for x in facts)
    text = text[:1].upper() + text[1:] if text else ""
    reason = f"{text} → {rule.conclusion}." if text and rule.conclusion else (text or rule.conclusion.capitalize())
    priority = _priority(rule, ctx.risk_level)
    groups = rules.groups_of(ctx.sensor_type)
    if rule.work_order:
        priority = _group_floor(priority, groups, rules.params)
    due_hours = rule.due_hours if rule.due_hours is not None else float(rules.params["due_hours"][priority])
    # Регламент ограничивает срок сверху: срочные случаи не удлиняются, медленные сокращаются до нормы
    limit = regulation_limit(rule, ctx, kind, rules)
    if limit is not None:
        due_hours = min(due_hours, limit[0].hours)
    avoid = [rule.avoid] if rule.avoid else []
    s = ctx.signals
    if s.open_order and "open_order" in rules.modifiers and rule.work_order:
        avoid.append(rules.modifiers["open_order"].format(open_order=s.open_order))
    if rule.id == "mass_link_loss" and "gas" in groups and "gas_planned_works" in rules.modifiers:
        avoid.append(rules.modifiers["gas_planned_works"])
    if s.recent_visit and "recent_visit" in rules.modifiers:
        avoid.append(rules.modifiers["recent_visit"].format(recent_visit=s.recent_visit))
    return Advice(
        rule_id=rule.id, title=rule.title, action=rule.action, reason=reason, facts=facts, conclusion=rule.conclusion,
        priority=priority, priority_label=WORK_ORDER_PRIORITY_LABELS[priority], due_hours=due_hours,
        due_at=ctx.at + timedelta(hours=due_hours), assignee=rule.assignee, avoid=avoid, fault_kind=kind,
        fault_kind_label=FAULT_KIND_LABELS[kind], fault_kind_basis=basis, plan=rule.plan, work_order=rule.work_order, per_object=rule.per_object,
        has_open_order=s.open_order is not None,
        source=rules.source, rules_version=rules.version, recommendation_code=rule.code,
        due_limit_hours=limit[0].hours if limit else None,
        due_basis=due_basis_text(*limit, rules.regulation) if limit else None,
    )


# ---------- Сбор фактов из БД (пакетом) ----------


def _chunks(values: Sequence, size: int = CHUNK) -> Iterable[list]:
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i : i + size]


def _count_in(times: list[datetime], lo: datetime, hi: datetime) -> int:
    """Сколько моментов в (lo, hi]; times отсортирован."""
    return bisect.bisect_right(times, hi) - bisect.bisect_right(times, lo)


def new_power_off(times: list[datetime], at: datetime, day: timedelta, quiet: timedelta) -> int:
    """Сообщений «Обесточен» за сутки до at — если за quiet до этих суток их не было; иначе 0."""
    recent = _count_in(times, at - day, at)
    return recent if recent and not _count_in(times, at - day - quiet, at - day) else 0


def _is_silence(top_factors) -> bool:
    return any(isinstance(f, dict) and group_of(str(f.get("feature") or "")) in ("silence", "link_losses", "past_gaps")
               for f in top_factors or [])


def type_precision(db: Session) -> dict[str, float]:
    from app.services import current_model_version

    version = current_model_version(db)
    rows = db.execute(
        select(ModelMetric.key, ModelMetric.precision).where(ModelMetric.scope == "sensor_type", ModelMetric.model_version == version)
    )
    return {k: float(p) for k, p in rows if p is not None}


def load_signals(db: Session, preds: Sequence[Prediction], rules: RuleSet) -> dict[int, Signals]:
    """Факты для пачки прогнозов. Запросов — константа на пачку, не на прогноз."""
    if not preds:
        return {}
    ch_ids = sorted({p.channel_id for p in preds})
    channels = {c.id: c for part in _chunks(ch_ids) for c in db.scalars(select(Channel).where(Channel.id.in_(part)))}
    obj_ids = sorted({channels[c].object_id for c in ch_ids})
    lo = min(p.at for p in preds)
    hi = max(p.at for p in preds)
    day = timedelta(hours=24)
    quiet = timedelta(days=rules.params["power_quiet_days"])

    # Отказы канала за 90 дней (разметка ML). В демо channel_faults пуст — берём дни с сообщениями о неисправности
    use_daily = db.scalar(select(ChannelFault.id).limit(1)) is None
    fault_times: dict[int, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))
    for part in _chunks(ch_ids):
        if use_daily:
            rows = db.execute(
                select(ChannelDaily.channel_id, ChannelDaily.day).where(
                    ChannelDaily.channel_id.in_(part), ChannelDaily.fault_count > 0,
                    ChannelDaily.day >= (lo - timedelta(days=91)).date(), ChannelDaily.day <= hi.date(),
                )
            )
            for cid, d in rows:
                # день засчитывается, когда закончился: сутки прогноза не видят своих же сообщений
                fault_times[cid]["Неисправен"].append(datetime.combine(d, datetime.min.time()) + timedelta(days=1) - timedelta(seconds=1))
        else:
            rows = db.execute(
                select(ChannelFault.channel_id, ChannelFault.kind, ChannelFault.ts).where(
                    ChannelFault.channel_id.in_(part), ChannelFault.ts > lo - timedelta(days=90), ChannelFault.ts <= hi
                )
            )
            for cid, kind, ts in rows:
                fault_times[cid][kind].append(ts)
    for by_kind in fault_times.values():
        for times in by_kind.values():
            times.sort()

    # Свои статусы за сутки: «Неисправен» / «Отключено устройство» / «Обесточен»
    status_times: dict[int, dict[str, list[datetime]]] = defaultdict(lambda: defaultdict(list))
    for part in _chunks(ch_ids):
        for cid, text, ts in db.execute(
            select(EventRecent.channel_id, EventRecent.value_text, EventRecent.ts).where(
                EventRecent.channel_id.in_(part), EventRecent.value_text.in_([*STATUS_FAULT, STATUS_POWER_OFF]),
                EventRecent.ts > lo - day - quiet, EventRecent.ts <= hi,
            )
        ):
            status_times[cid][text].append(ts)
    for by_text in status_times.values():
        for times in by_text.values():
            times.sort()

    # Объекты: сколько в них датчиков и какие из них — фазы питания (в SQL: в объектах бывает по 700 датчиков)
    obj_count: dict[int, int] = {}
    type_count: dict[tuple[int, str], int] = {}
    power_channels: dict[int, int] = {}
    power_types = [t for t in db.scalars(select(Channel.sensor_type).distinct()) if "power" in rules.groups_of(t)]
    for part in _chunks(obj_ids):
        obj_count.update(db.execute(
            select(Channel.object_id, func.count()).where(Channel.object_id.in_(part)).group_by(Channel.object_id)
        ).all())
        type_count.update({(oid, st): n for oid, st, n in db.execute(
            select(Channel.object_id, Channel.sensor_type, func.count()).where(Channel.object_id.in_(part))
            .group_by(Channel.object_id, Channel.sensor_type)
        )})
        if power_types:
            power_channels.update({cid: oid for cid, oid in db.execute(
                select(Channel.id, Channel.object_id).where(Channel.object_id.in_(part), Channel.sensor_type.in_(power_types))
            )})
    # «Обесточен» у фаз объекта. В реальных данных у части фаз оно бывает каждый день (похоже на штатное состояние
    # резервных фаз), поэтому считаем только новое: за сутки есть, а за неделю до этого не было
    power_off: dict[int, dict[int, list[datetime]]] = defaultdict(lambda: defaultdict(list))  # объект → фаза → моменты
    for part in _chunks(sorted(power_channels)):
        for cid, ts in db.execute(
            select(EventRecent.channel_id, EventRecent.ts).where(
                EventRecent.channel_id.in_(part), EventRecent.value_text == STATUS_POWER_OFF,
                EventRecent.ts > lo - day - quiet, EventRecent.ts <= hi,
            )
        ):
            power_off[power_channels[cid]][cid].append(ts)
    for by_phase in power_off.values():
        for times in by_phase.values():
            times.sort()

    # Соседи без связи: (а) прогноз «риск» и выше из-за тишины в том же срезе; (б) потеря связи по разметке за сутки
    snaps = sorted({p.at for p in preds})
    silent_in_snap: dict[tuple[datetime, int], set[int]] = defaultdict(set)
    type_of: dict[int, str] = {}  # датчик-сосед → тип (для доли молчащих однотипных)
    for part in _chunks(obj_ids):
        for snap_part in _chunks(snaps, 500):
            for at, cid, oid, tf, st in db.execute(
                select(Prediction.at, Prediction.channel_id, Channel.object_id, Prediction.top_factors, Channel.sensor_type)
                .join(Channel, Channel.id == Prediction.channel_id)
                .where(Prediction.at.in_(snap_part), Channel.object_id.in_(part), level_in(("risk", "critical")))
            ):
                if _is_silence(tf):
                    silent_in_snap[(at, oid)].add(cid)
                    type_of[cid] = st
    link_losses: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    if not use_daily:
        for part in _chunks(obj_ids):
            for cid, oid, ts, st in db.execute(
                select(ChannelFault.channel_id, Channel.object_id, ChannelFault.ts, Channel.sensor_type)
                .join(Channel, Channel.id == ChannelFault.channel_id)
                .where(Channel.object_id.in_(part), ChannelFault.kind == "Пропадание связи",
                       ChannelFault.ts > lo - day, ChannelFault.ts <= hi)
            ):
                link_losses[oid].append((ts, cid))
                type_of[cid] = st

    # Заявки по датчикам
    orders: dict[int, list[WorkOrder]] = defaultdict(list)
    for part in _chunks(ch_ids):
        for wo in db.scalars(select(WorkOrder).where(WorkOrder.channel_id.in_(part), WorkOrder.created_at <= hi)):
            orders[wo.channel_id].append(wo)

    precision = type_precision(db)
    repair_days = timedelta(days=rules.params["repair_days"])
    result: dict[int, Signals] = {}
    for p in preds:
        ch = channels[p.channel_id]
        oid, at = ch.object_id, p.at
        s = Signals(history_source="channel_daily" if use_daily else "channel_faults")
        for kind, times in fault_times.get(p.channel_id, {}).items():
            if n := _count_in(times, at - timedelta(days=30), at):
                s.faults_30d[kind] = n
            if n := _count_in(times, at - timedelta(days=90), at):
                s.faults_90d[kind] = n
        st = status_times.get(p.channel_id, {})
        s.fault_msgs_24h = sum(_count_in(st.get(t, []), at - day, at) for t in STATUS_FAULT)
        s.power_off_24h = new_power_off(st.get(STATUS_POWER_OFF, []), at, day, quiet)
        s.object_channels = obj_count.get(oid, 0) or 1
        silent = set(silent_in_snap.get((at, oid), set()))
        silent |= {cid for ts, cid in link_losses.get(oid, []) if at - day < ts <= at}
        silent.discard(p.channel_id)
        s.neighbors_silent = len(silent)
        s.same_type_channels = type_count.get((oid, ch.sensor_type), 0) or 1
        s.same_type_silent = sum(1 for cid in silent if type_of.get(cid) == ch.sensor_type)
        s.object_power_off = sum(
            1 for cid, times in power_off.get(oid, {}).items() if cid != p.channel_id and new_power_off(times, at, day, quiet)
        )

        all_faults = sorted(t for times in fault_times.get(p.channel_id, {}).values() for t in times)
        open_orders = [w for w in orders.get(p.channel_id, []) if w.created_at <= at
                       and (w.status in OPEN_WORK_ORDER_STATUSES or (w.closed_at and w.closed_at > at))]
        if open_orders:
            w = max(open_orders, key=lambda w: (w.created_at, w.id))
            label = WORK_ORDER_STATUS_LABELS["submitted" if w.status in ("done", "cancelled") else w.status]
            s.open_order = f"{w.number} ({label.lower()})"
        done = [w for w in orders.get(p.channel_id, []) if w.status == "done" and w.closed_at and at - repair_days <= w.closed_at <= at]
        if done:
            w = max(done, key=lambda w: (w.closed_at, w.id))
            after = _count_in(all_faults, w.closed_at, at)
            s.last_repair = (w.number, w.closed_at, after)
            if after == 0 and at - w.closed_at <= day:
                s.recent_visit = w.number
        s.type_precision = precision.get(ch.sensor_type)
        result[p.id] = s
    return result


def context_of(p: Prediction, signals: Signals) -> Context:
    return Context(
        prediction_id=p.id, at=p.at, sensor_type=p.channel.sensor_type, system_type=p.channel.system_type,
        risk_level=level_of(p), prob=float(p.prob), top_factors=list(p.top_factors or []), signals=signals,
        kind_probs=dict(p.kind_probs) if p.kind_probs else None,
    )


def advise_predictions(db: Session, preds: Sequence[Prediction]) -> dict[int, Advice]:
    rules = load_rules()
    signals = load_signals(db, preds, rules)
    ids = recommendation_ids(db)
    result = {}
    for p in preds:
        a = advise(context_of(p, signals[p.id]), rules)
        a.recommendation_id = ids.get(a.recommendation_code)
        result[p.id] = a
    return result


def advice_for(db: Session, p: Prediction) -> Advice:
    return advise_predictions(db, [p])[p.id]


# ---------- Справочник рекомендаций ----------


def recommendation_ids(db: Session) -> dict[str, int]:
    return dict(db.execute(select(Recommendation.code, Recommendation.id).where(Recommendation.code.like("rule:%"))).all())


def sync_dictionary(db: Session, rules: RuleSet | None = None) -> int:
    """Строки справочника recommendations для правил (code = rule:<id>): добавляет недостающие и обновляет текст.
    Идемпотентно; строки прежнего справочника по типам датчиков не трогает. Возвращает число изменённых строк."""
    rules = rules or load_rules()
    existing = {r.code: r for r in db.scalars(select(Recommendation).where(Recommendation.code.like("rule:%")))}
    changed = 0
    next_id = (db.scalar(select(func.max(Recommendation.id))) or 0) + 1
    for rule in rules.rules:
        row = existing.get(rule.code)
        if row is None:
            db.add(Recommendation(id=next_id, code=rule.code, sensor_type=None, text=rule.action, is_active=rule.work_order))
            next_id += 1
            changed += 1
        elif row.text != rule.action or row.is_active != rule.work_order:
            row.text, row.is_active = rule.action, rule.work_order
            changed += 1
    for code, row in existing.items():
        if code not in {r.code for r in rules.rules} and row.is_active:
            row.is_active = False  # правило удалили из файла — строку не удаляем (на неё могут ссылаться заявки)
            changed += 1
    if changed:
        db.flush()
        reset_sequences(db, [Recommendation.__table__])
        db.commit()
    return changed


def to_schema(p: Prediction, a: Advice):
    from app import schemas

    return schemas.MaintenanceAdvice(
        prediction_id=p.id, channel_id=p.channel_id, at=p.at, rule_id=a.rule_id, title=a.title, action=a.action,
        reason=a.reason, facts=a.facts, conclusion=a.conclusion, priority=a.priority, priority_label=a.priority_label,
        due_hours=a.due_hours, due_at=a.due_at, assignee=a.assignee, avoid=a.avoid, fault_kind=a.fault_kind,
        fault_kind_label=a.fault_kind_label, fault_kind_basis=a.fault_kind_basis, plan=a.plan,
        work_order_needed=a.work_order, recommendation_id=a.recommendation_id, source=a.source, rules_version=a.rules_version,
        due_limit_hours=a.due_limit_hours, due_basis=a.due_basis,
    )
