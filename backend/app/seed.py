"""Детерминированный демо-сид: обезличенные справочники, 1–2 месяца событий, прогнозы, исходы, решения, заявки.

Запуск: python -m app.seed [--scale demo|small] [--force]
Все данные выводятся из одного процесса отказов, поэтому прогнозы, исходы, события и метрики модели
согласованы между собой: метрики и таблица порогов считаются по сгенерированным прогнозам, а не выдуманы.
"""

import argparse
import bisect
import logging
import math
import random
import secrets
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.dbcompat import reset_sequences
from app.labels import health_from_prob, risk_level_from_health
from app.models import (
    AuditLog,
    Channel,
    ChannelDaily,
    Decision,
    EventRecent,
    ModelMetric,
    ModelThreshold,
    Object,
    Prediction,
    PredictionOutcome,
    Reason,
    Recommendation,
    User,
    WorkOrder,
)
from app.security import hash_password

log = logging.getLogger("seed")

SEED = 20260922
MODEL_VERSION = "lgbm-2026.09-demo"
SNAPSHOT_STEP = timedelta(hours=6)
FAULT_EXCLUSION = timedelta(hours=72)  # как в ТЗ ML: срезы с отказом в последние 72 ч не оцениваются
TARGET_PRECISION = 0.72

SCALES = {
    # Демо для жюри и фронтенда: ≈400 датчиков, два месяца
    "demo": {"channels": 400, "start": date(2026, 7, 1), "end": date(2026, 8, 31)},
    # Для тестов и быстрых проверок
    "small": {"channels": 60, "start": date(2026, 7, 27), "end": date(2026, 8, 4)},
}

# Пример из ТЗ фронтенда — воспроизводим его буквально
EXAMPLE_CHANNEL_ID = 334609
EXAMPLE_AT = datetime(2026, 8, 1, 12, 0)
EXAMPLE_FAULT_AT = datetime(2026, 8, 1, 18, 42, 10)

DEMO_USERS = [
    ("dispatcher", "dispatcher123", "Диспетчер района", "dispatcher"),
    ("engineer", "engineer123", "Инженер данных", "engineer"),
    ("manager", "manager123", "Начальник участка", "manager"),
    ("admin", "admin123", "Администратор системы", "admin"),
    ("ods", "ods123", "Диспетчер ОДС", "dispatcher_ods"),
    ("technician", "technician123", "Техник комплекса", "technician"),
]
# Область демо-пользователей (роли с областью): district — все районы (в данных заказчика район один, и
# диспетчер района видит всё его дерево), complex — один объект-комплекс: «объект Альфа», если у него есть датчики,
# иначе комплекс с наибольшим числом датчиков (в маленьком тестовом сиде у «Альфы» их нет)
DEMO_SCOPES = {"dispatcher": "district", "manager": "district", "technician": "complex"}
DEMO_COMPLEX_NAME = "объект Альфа"


def demo_scope_ids(db: Session, kind: str) -> list[int]:
    if kind == "district":
        return list(db.scalars(select(Object.id).where(Object.level == 1).order_by(Object.id)))
    parent = {o.id: o.parent_id for o in db.execute(select(Object.id, Object.parent_id))}
    complexes = dict(db.execute(select(Object.id, Object.name).where(Object.level == 2)).all())
    counts: dict[int, int] = {}
    for object_id, n in db.execute(select(Channel.object_id, func.count()).group_by(Channel.object_id)):
        cur = object_id
        while cur is not None and cur not in complexes:
            cur = parent.get(cur)
        if cur is not None:
            counts[cur] = counts.get(cur, 0) + n
    named = next((i for i, name in complexes.items() if name == DEMO_COMPLEX_NAME and counts.get(i)), None)
    if named is not None:
        return [named]
    best = max(sorted(complexes), key=lambda i: counts.get(i, 0), default=None)
    return [best] if best is not None else []


def apply_demo_access(db: Session, only_missing: bool = False) -> None:
    """Роли и области демо-пользователей. only_missing — не трогать тех, кому роли уже назначены
    (администратор мог их поменять)."""
    from app.access import set_access
    from app.models import UserRole

    for username, _, _, role in DEMO_USERS:
        user = db.scalar(select(User).where(User.username == username))
        if user is None:
            continue
        if only_missing and db.scalar(select(UserRole.role).where(UserRole.user_id == user.id).limit(1)):
            continue
        kind = DEMO_SCOPES.get(username)
        set_access(db, user, [role], demo_scope_ids(db, kind) if kind else [])


def ensure_demo_users(db: Session) -> list[str]:
    """demo-режим на уже заполненной базе (например, реальные данные): добавить недостающих демо-пользователей
    (новые роли ОДС и техник) и назначить им роли и области. Существующих не меняет."""
    added = []
    for username, password, full_name, role in DEMO_USERS:
        if db.scalar(select(User.id).where(User.username == username)) is None:
            db.add(User(username=username, full_name=full_name, role=role, password_hash=hash_password(password),
                        auth_source="local", is_active=True))
            added.append(username)
    db.flush()
    apply_demo_access(db, only_missing=True)
    db.commit()
    return added

OBJECT_NAMES_L2 = [
    "Альфа", "Бета", "Гамма", "Дельта", "Эпсилон", "Дзета", "Эта", "Тета",
    "Йота", "Каппа", "Лямбда", "Мю", "Ню", "Кси", "Омикрон", "Пи",
]
SUBOBJECTS_PER_OBJECT = [2, 6, 5, 5, 4, 5, 6, 4, 5, 5, 4, 6, 5, 5, 6, 5]  # всего 78
OBJECT_NAMES_L3 = [
    "Ро", "Сигма", "Фита", "Тау", "Ипсилон", "Фи", "Хи", "Пси", "Омега", "Ижица", "Коппа", "Сампи", "Дигамма",
    "Стигма", "Хета", "Шо", "Санн", "Йот", "Аз", "Буки", "Веди", "Глаголь", "Добро", "Есть", "Живете", "Зело",
]


@dataclass(frozen=True)
class SensorKind:
    system: str
    sensor: str
    name: str  # шаблон названия
    weight: float  # доля в парке датчиков
    fault_rate: float  # отказов в сутки на датчик
    numeric: bool = False
    unit_base: float = 0.0
    unit_spread: float = 0.0
    group: str = "status"  # status | numeric | unit (насосы и вентиляторы) | power


# 6 систем, 19 типов. Пропорции — по разбору справочника (дым > фаза > двери > движение).
SENSOR_KINDS = [
    SensorKind("Пожарная охрана", "Датчик дыма", "Дым ПК {pk}+{n}", 0.30, 0.006),
    SensorKind("Пожарная охрана", "Тепловой извещатель", "ИП тепловой ПК {pk}+{n}", 0.03, 0.004),
    SensorKind("Пожарная охрана", "Ручной пожарный извещатель", "ИПР ПК {pk}", 0.02, 0.002),
    SensorKind("Газовый контроль", "Датчик метана", "Газ CH4 ПК {pk}+{n}", 0.05, 0.006, True, 0.01, 0.004, "numeric"),
    SensorKind("Газовый контроль", "Датчик угарного газа", "Газ CO ПК {pk}+{n}", 0.03, 0.006, True, 2.0, 0.6, "numeric"),
    SensorKind("Газовый контроль", "Датчик кислорода", "O2 ПК {pk}+{n}", 0.02, 0.005, True, 20.8, 0.15, "numeric"),
    SensorKind("Газовый контроль", "Датчик сероводорода", "H2S ПК {pk}+{n}", 0.02, 0.005, True, 0.3, 0.1, "numeric"),
    SensorKind("Охранная сигнализация", "Датчик движения", "Движение ПК {pk}+{n}", 0.07, 0.004),
    SensorKind("Охранная сигнализация", "Дверной контакт", "Дверь ВШ-{k}", 0.08, 0.004),
    SensorKind("Охранная сигнализация", "Датчик люка", "Люк ВШ-{k}", 0.03, 0.004),
    SensorKind("Охранная сигнализация", "Датчик вскрытия щита", "Вскрытие щита ЩР-{k}", 0.02, 0.003),
    SensorKind("Водоотведение", "Насос", "Насос АНС-{k} №{n}", 0.04, 0.035, group="unit"),
    SensorKind("Водоотведение", "Датчик уровня воды", "Уровень АНС-{k}", 0.02, 0.008, True, 0.35, 0.1, "numeric"),
    SensorKind("Водоотведение", "Датчик затопления", "Затопление ПК {pk}", 0.03, 0.004),
    SensorKind("Вентиляция", "Вентилятор", "Вентилятор ВШ-{k} №{n}", 0.03, 0.025, group="unit"),
    SensorKind("Вентиляция", "Датчик температуры", "Т° ПК {pk}+{n}", 0.04, 0.005, True, 24.0, 2.5, "numeric"),
    SensorKind("Электроснабжение", "Контроль фазы", "Фаза {phase} щит ЩР-{k}", 0.08, 0.03, group="power"),
    SensorKind("Электроснабжение", "Ввод питания", "Ввод питания ЩР-{k}", 0.02, 0.01, group="power"),
    SensorKind("Электроснабжение", "Датчик освещения", "Освещение ПК {pk}", 0.02, 0.004),
]
SYSTEM_CODES = {s: i + 1 for i, s in enumerate(dict.fromkeys(k.system for k in SENSOR_KINDS))}

REASONS = [
    ("model_risk_confirmed", "Подтверждён риск отказа по данным модели", "dispatch"),
    ("repeated_fault_msgs", "Повторные сообщения о неисправности", "dispatch"),
    ("link_loss", "Пропадание связи с датчиком", "dispatch"),
    ("power_loss", "Обесточивание фазы или щита", "dispatch"),
    ("flood_risk", "Риск затопления", "dispatch"),
    ("planned_works", "Проводятся плановые работы", "false_alarm"),
    ("known_interference", "Известная помеха или наводка", "false_alarm"),
    ("already_replaced", "Датчик уже заменён", "false_alarm"),
    ("comm_glitch", "Сбой канала связи, оборудование исправно", "false_alarm"),
    ("staff_pass", "Срабатывание при проходе персонала", "false_alarm"),
    ("need_more_data", "Недостаточно данных, наблюдаем", "monitor"),
    ("single_spike", "Единичный всплеск", "monitor"),
    ("wait_related_service", "Ожидаем подтверждения от смежной службы", "monitor"),
    ("other", "Другое (см. комментарий)", None),
]

RECOMMENDATIONS = [
    ("smoke_check", "Датчик дыма", "Проверить и очистить дымовую камеру извещателя, проверить шлейф и адресный модуль."),
    ("heat_check", "Тепловой извещатель", "Проверить тепловой извещатель тестером, осмотреть шлейф."),
    ("pump_check", "Насос", "Проверить насос АНС: питание, пускатель, поплавковые датчики, отсутствие засора всаса."),
    ("fan_check", "Вентилятор", "Проверить пускатель и привод вентилятора, подшипники и крепление; замерить ток двигателя."),
    ("phase_check", "Контроль фазы", "Проверить автоматы и контакторы в щите, замерить напряжение по фазам."),
    ("feed_check", "Ввод питания", "Проверить вводной автомат и АВР, замерить напряжение на вводе."),
    ("gas_calibration", "Датчик метана", "Проверить калибровку газоанализатора поверочной смесью; при дрейфе заменить сенсор."),
    ("co_calibration", "Датчик угарного газа", "Проверить калибровку датчика CO поверочной смесью; при дрейфе заменить сенсор."),
    ("temp_check", "Датчик температуры", "Сравнить показания с переносным термометром, проверить крепление и кабель датчика."),
    ("motion_check", "Датчик движения", "Проверить извещатель движения: загрязнение линзы, крепление, питание."),
    ("door_check", "Дверной контакт", "Проверить геркон и магнит дверного контакта, регулировку двери."),
    ("hatch_check", "Датчик люка", "Проверить датчик люка и уплотнение крышки."),
    ("water_check", "Датчик уровня воды", "Очистить датчик уровня от ила, проверить поплавок и кабель."),
    ("flood_check", "Датчик затопления", "Проверить датчик затопления, очистить контакты."),
    ("general_inspection", None, "Провести осмотр датчика и линии связи, проверить питание контроллера."),
    ("controller_link", None, "Проверить канал связи контроллера и коммутационное оборудование."),
]


@dataclass
class Fault:
    at: datetime
    kind: str
    detected: bool
    repair_h: float


@dataclass
class ChannelSim:
    id: int
    kind: SensorKind
    object_id: int
    name: str
    tag: str
    faults: list[Fault] = field(default_factory=list)
    false_alarms: list[tuple[datetime, datetime]] = field(default_factory=list)
    base: float = 0.0


# ---------- Справочники ----------


def build_objects() -> tuple[list[dict], dict[int, int]]:
    """95 объектов: район → 16 объектов → 78 подобъектов. Возвращает строки и «подобъект → объект»."""
    rows = [{"id": 1, "level": 1, "parent_id": None, "kind": "district", "name": "Район по эксплуатации"}]
    for i, name in enumerate(OBJECT_NAMES_L2):
        rows.append({"id": 2 + i, "level": 2, "parent_id": 1, "kind": "controlHouse", "name": f"объект {name}"})
    names = [f"объект {n}" for n in OBJECT_NAMES_L3]
    names += [f"объект {n}-2" for n in OBJECT_NAMES_L3] + [f"объект {n}-3" for n in OBJECT_NAMES_L3]
    next_id, parent_of = 18, {}
    for i, count in enumerate(SUBOBJECTS_PER_OBJECT):
        for _ in range(count):
            rows.append({"id": next_id, "level": 3, "parent_id": 2 + i, "kind": "guardObject", "name": names[next_id - 18]})
            parent_of[next_id] = 2 + i
            next_id += 1
    return rows, parent_of


def build_channels(rng: random.Random, n: int, subobjects: list[int]) -> list[ChannelSim]:
    ids = rng.sample(range(300_000, 350_000), n + 1)
    ids = [i for i in ids if i != EXAMPLE_CHANNEL_ID][: n - 1]
    kinds = rng.choices(SENSOR_KINDS, weights=[k.weight for k in SENSOR_KINDS], k=n - 1)
    smoke = next(k for k in SENSOR_KINDS if k.sensor == "Датчик дыма")
    channels = [ChannelSim(EXAMPLE_CHANNEL_ID, smoke, 20, "Дым ПК 1101+2", "847-1.1.131.2")]
    used_names = {channels[0].name}
    for cid, kind in zip(ids, kinds):
        obj = rng.choice(subobjects)
        for _ in range(20):
            name = kind.name.format(
                pk=rng.randint(700, 1500), n=rng.randint(1, 9), k=rng.randint(1, 30), phase=rng.choice("ABC")
            )
            if name not in used_names:
                break
        used_names.add(name)
        tag = f"847-{SYSTEM_CODES[kind.system]}.{rng.randint(1, 4)}.{obj + 100}.{rng.randint(1, 20)}"
        if rng.random() < 0.3:
            tag += f".{rng.randint(1, 9)}"
        channels.append(ChannelSim(cid, kind, obj, name, tag))
    for ch in channels:
        ch.base = ch.kind.unit_base + rng.uniform(-1, 1) * ch.kind.unit_spread
    return channels


def split_tag_levels(tag: str) -> dict:
    head, _, rest = tag.partition("-")
    parts = [head, *[p for p in rest.split(".") if p]][:5]
    parts += [None] * (5 - len(parts))
    return {f"tag_l{i + 1}": parts[i] for i in range(5)}


# ---------- Процесс отказов ----------


def simulate_faults(rng: random.Random, channels: list[ChannelSim], start: datetime, end: datetime) -> None:
    for ch in channels:
        # У части датчиков хроническая проблема — они отказывают заметно чаще
        rate = ch.kind.fault_rate * rng.lognormvariate(0, 0.8)
        t = start - timedelta(days=3)
        while True:
            t += timedelta(days=rng.expovariate(rate)) if rate > 0 else timedelta(days=10_000)
            if t > end + timedelta(days=1):
                break
            ch.faults.append(
                Fault(
                    at=t.replace(microsecond=0),
                    kind="Неисправен" if rng.random() < 0.85 else "Отключено устройство",
                    detected=rng.random() < 0.66,
                    repair_h=rng.uniform(2, 30),
                )
            )
        fa_rate = 0.005 * rng.lognormvariate(0, 0.5)
        t = start
        while True:
            t += timedelta(days=rng.expovariate(fa_rate))
            if t > end:
                break
            ch.false_alarms.append((t, t + timedelta(hours=rng.uniform(6, 18))))
    example = next(c for c in channels if c.id == EXAMPLE_CHANNEL_ID)
    example.faults = [f for f in example.faults if abs((f.at - EXAMPLE_FAULT_AT).days) > 4]
    example.faults.append(Fault(EXAMPLE_FAULT_AT, "Неисправен", True, 5.0))
    example.faults.sort(key=lambda f: f.at)


def next_fault(ch: ChannelSim, t: datetime, horizon=timedelta(hours=24)) -> Fault | None:
    times = [f.at for f in ch.faults]
    i = bisect.bisect_right(times, t)
    if i < len(ch.faults) and ch.faults[i].at <= t + horizon:
        return ch.faults[i]
    return None


def recent_fault(ch: ChannelSim, t: datetime, window: timedelta) -> Fault | None:
    times = [f.at for f in ch.faults]
    i = bisect.bisect_right(times, t) - 1
    if i >= 0 and ch.faults[i].at > t - window:
        return ch.faults[i]
    return None


def predict_prob(rng: random.Random, ch: ChannelSim, t: datetime) -> float:
    nf = next_fault(ch, t)
    if ch.id == EXAMPLE_CHANNEL_ID and t == EXAMPLE_AT:
        return 0.87
    if nf is not None:
        lead_h = (nf.at - t).total_seconds() / 3600
        closeness = 1 - lead_h / 24
        if nf.detected:
            return min(0.98, rng.uniform(0.52, 0.8) + 0.2 * closeness)
        return rng.uniform(0.12, 0.46)
    if any(a <= t <= b for a, b in ch.false_alarms):
        return rng.uniform(0.5, 0.86)
    if recent_fault(ch, t, FAULT_EXCLUSION):
        return rng.uniform(0.15, 0.48)
    # Обычное состояние: в основном низкий риск, изредка «внимание»
    return min(0.49, rng.random() ** 3 * 0.42 + 0.01)


# ---------- Факторы ----------


def factors_for(rng: random.Random, ch: ChannelSim, t: datetime, prob: float, object_faults_7d: int) -> list[dict]:
    if ch.id == EXAMPLE_CHANNEL_ID and t == EXAMPLE_AT:
        return [
            {"feature": "fault_msgs_24h", "value": 6, "norm": 0, "phrase": "Сообщений о неисправности за сутки: 6 (обычно 0)"},
            {"feature": "status_changes_24h", "value": 40, "norm": 2, "phrase": "Смен статуса за сутки: 40"},
            {"feature": "object_faults_7d", "value": 3, "norm": 0, "phrase": "Отказов в этом объекте за 7 дней: 3"},
        ]
    s = prob  # сила аномалии растёт с вероятностью
    g = ch.kind.group
    pool: list[tuple[float, dict]] = []

    def add(weight, feature, value, norm, phrase):
        pool.append((weight + rng.random() * 0.3, {"feature": feature, "value": value, "norm": norm, "phrase": phrase}))

    if g == "numeric":
        stuck = round(20 + 75 * s)
        add(s, "stuck_share_24h", stuck, 15, f"Одинаковых показаний подряд за сутки: {stuck}% (обычно до 15%)")
        dev = round(0.5 + 4 * s, 1)
        add(s * 0.9, "neighbor_deviation", dev, 1.0, f"Отклонение от соседних датчиков того же типа: {dev}σ (обычно до 1σ)")
        trend = round(3 + 40 * s)
        add(s * 0.7, "trend_24h", trend, 5, f"Изменение показаний за сутки: +{trend}% (обычно до 5%)")
    if g == "unit":
        starts = round(4 + 30 * s)
        add(s, "starts_24h", starts, 6, f"Пусков за сутки: {starts} (обычно 6)")
    if g == "power":
        off = round(8 * s)
        add(s, "power_off_24h", off, 0, f"Сообщений «Обесточен» за сутки: {off} (обычно 0)")
    faults = round(7 * s * s)
    add(s * 1.1, "fault_msgs_24h", faults, 0, f"Сообщений о неисправности за сутки: {faults} (обычно 0)")
    changes = round(2 + 45 * s * s)
    add(s, "status_changes_24h", changes, 2, f"Смен статуса за сутки: {changes} (обычно 2)")
    unc = round(60 * s * s)
    add(s * 0.8, "uncertain_share_24h", unc, 0, f"Доля статуса «Неопределен» за сутки: {unc}% (обычно 0%)")
    silence = round(1 + 14 * s * s, 1)
    add(s * 0.6, "max_silence_h", silence, 2, f"Самая долгая пауза связи: {silence} ч (обычно до 2 ч)")
    if object_faults_7d:
        add(0.4 + s * 0.4, "object_faults_7d", object_faults_7d, 0, f"Отказов в этом объекте за 7 дней: {object_faults_7d}")
    last = recent_fault(ch, t, timedelta(days=90))
    if last is not None:
        days = max(1, (t - last.at).days)
        add(0.3 + s * 0.3, "days_since_fault", days, None, f"Дней с последнего отказа: {days}")
    pool.sort(key=lambda x: -x[0])
    return [f for _, f in pool[:3]]


# ---------- События ----------


def gen_events(rng: random.Random, ch: ChannelSim, start: datetime, end: datetime) -> list[dict]:
    events: dict[tuple, dict] = {}

    def ev(ts: datetime, value, alarm=False):
        ts = ts.replace(microsecond=0)
        if not (start <= ts <= end):
            return
        raw = f"{value:.3f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
        num = value if isinstance(value, float) else None
        key = (ts, raw)
        events.setdefault(key, {
            "channel_id": ch.id, "ts": ts, "is_alarm": alarm, "value_raw": raw,
            "value_num": num, "value_text": None if num is not None else raw,
        })

    kind = ch.kind
    days = (end.date() - start.date()).days + 1
    for d in range(days):
        day0 = datetime.combine(start.date() + timedelta(days=d), datetime.min.time())
        if kind.numeric:
            for h in range(0, 24, 2):
                ts = day0 + timedelta(hours=h, minutes=rng.randint(0, 59), seconds=rng.randint(0, 59))
                nf = next_fault(ch, ts)
                drift = 0.0
                if nf is not None and nf.detected:  # перед отказом показания «плывут»
                    drift = kind.unit_spread * 3 * (1 - (nf.at - ts).total_seconds() / 86400)
                value = max(0.0, ch.base + rng.gauss(0, kind.unit_spread * 0.3) + drift)
                ev(ts, round(value, 3))
        else:
            for _ in range(2):
                ev(day0 + timedelta(seconds=rng.randint(0, 86399)), "Норма")
            if kind.sensor == "Датчик движения":
                for _ in range(rng.randint(0, 3)):
                    ev(day0 + timedelta(seconds=rng.randint(0, 86399)), "Обнаружено движение", True)
            if kind.group == "unit":
                for _ in range(rng.randint(2, 5)):
                    on = day0 + timedelta(seconds=rng.randint(0, 80000))
                    ev(on, "Включен")
                    ev(on + timedelta(minutes=rng.randint(5, 90)), "Выключен")
    for f in ch.faults:
        if f.detected:
            # Предвестники, которые видит модель: дребезг «Неопределен» / «Норма», у питания — «Обесточен»
            for i in range(rng.randint(4, 20)):
                ts = f.at - timedelta(minutes=rng.randint(10, 12 * 60))
                if kind.group == "power" and i % 3 == 0:
                    ev(ts, "Обесточен", True)
                else:
                    ev(ts, "Неопределен" if i % 2 == 0 else "Норма")
        ev(f.at, f.kind, True)
        ev(f.at + timedelta(hours=f.repair_h), "Норма")
    return sorted(events.values(), key=lambda e: e["ts"])


def daily_rows(channel_id: int, events: list[dict]) -> list[dict]:
    by_day: dict[date, list[dict]] = defaultdict(list)
    for e in events:
        by_day[e["ts"].date()].append(e)
    rows = []
    for day, evs in sorted(by_day.items()):
        texts = [e["value_text"] for e in evs if e["value_text"]]
        nums = [e["value_num"] for e in evs if e["value_num"] is not None]
        gaps = [(b["ts"] - a["ts"]).total_seconds() / 60 for a, b in zip(evs, evs[1:])]
        rows.append({
            "channel_id": channel_id,
            "day": day,
            "events_count": len(evs),
            "alarm_count": sum(e["is_alarm"] for e in evs),
            "fault_count": sum(t in ("Неисправен", "Отключено устройство") for t in texts),
            "uncertain_count": texts.count("Неопределен"),
            "power_off_count": texts.count("Обесточен"),
            "status_changes": sum(a != b for a, b in zip(texts, texts[1:])),
            "value_avg": round(sum(nums) / len(nums), 4) if nums else None,
            "value_min": min(nums) if nums else None,
            "value_max": max(nums) if nums else None,
            "max_gap_min": round(max(gaps)) if gaps else None,
        })
    return rows


# ---------- Метрики модели ----------


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p and r else None
    return p, r, f1


def average_precision(pairs: list[tuple[float, bool]]) -> float | None:
    positives = sum(y for _, y in pairs)
    if not positives:
        return None
    tp, ap = 0, 0.0
    for i, (_, y) in enumerate(sorted(pairs, key=lambda x: -x[0]), start=1):
        if y:
            tp += 1
            ap += tp / i
    return ap / positives


def compute_metrics(samples: list[dict], n_days: int, start: date, end: date) -> tuple[list[dict], list[dict]]:
    """Оценка как в ТЗ ML: срезы «датчик × сутки» в 00:00, без срезов с отказом в последние 72 ч."""
    grid = [round(0.05 * i, 2) for i in range(1, 20)]
    thresholds = []
    for thr in grid:
        tp = sum(s["prob"] >= thr and s["y"] for s in samples)
        fp = sum(s["prob"] >= thr and not s["y"] for s in samples)
        fn = sum(s["prob"] < thr and s["y"] for s in samples)
        p, r, _ = _prf(tp, fp, fn)
        thresholds.append({
            "model_version": MODEL_VERSION, "threshold": thr, "precision": round(p or 0, 4), "recall": round(r or 0, 4),
            "alerts_per_day": round((tp + fp) / n_days, 2), "tp_per_day": round(tp / n_days, 2),
            "fp_per_day": round(fp / n_days, 2), "is_selected": False,
        })
    ok = [t for t in thresholds if t["precision"] >= TARGET_PRECISION and t["recall"] > 0]
    chosen = max(ok, key=lambda t: (t["recall"], -t["threshold"])) if ok else next(t for t in thresholds if t["threshold"] == 0.5)
    chosen["is_selected"] = True
    thr = chosen["threshold"]

    def metric(scope: str, key: str, subset: list[dict], predicate=None) -> dict:
        predicate = predicate or (lambda s: s["prob"] >= thr)
        tp = sum(predicate(s) and s["y"] for s in subset)
        fp = sum(predicate(s) and not s["y"] for s in subset)
        fn = sum(not predicate(s) and s["y"] for s in subset)
        p, r, f1 = _prf(tp, fp, fn)
        ap = average_precision([(s["prob"], s["y"]) for s in subset]) if scope != "baseline" else None
        return {
            "model_version": MODEL_VERSION, "scope": scope, "key": key,
            "precision": _r(p), "recall": _r(r), "f1": _r(f1), "pr_auc": _r(ap),
            "support": tp + fn, "value": None, "period_from": start, "period_to": end,
        }

    metrics = [
        metric("overall", "", samples),
        metric("baseline", "", samples, lambda s: s["fault_7d"]),
    ]
    by_type: dict[str, list[dict]] = defaultdict(list)
    for s in samples:
        by_type[s["sensor_type"]].append(s)
    metrics += [metric("sensor_type", st, sub) for st, sub in sorted(by_type.items()) if sum(x["y"] for x in sub)]

    by_day: dict[date, list[dict]] = defaultdict(list)
    for s in samples:
        by_day[s["day"]].append(s)
    for k in (20, 50, 100):
        values = []
        for sub in by_day.values():
            top = sorted(sub, key=lambda s: -s["prob"])[:k]
            if top:
                values.append(sum(s["y"] for s in top) / len(top))
        metrics.append({
            "model_version": MODEL_VERSION, "scope": "precision_at_k", "key": str(k), "precision": None, "recall": None,
            "f1": None, "pr_auc": None, "support": None, "value": _r(statistics.mean(values)) if values else None,
            "period_from": start, "period_to": end,
        })
    return metrics, thresholds


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


# ---------- Основной сценарий ----------


def _bulk_insert(db: Session, model, rows: list[dict], chunk: int = 5000) -> None:
    for i in range(0, len(rows), chunk):
        db.execute(insert(model.__table__), rows[i : i + chunk])


def clear_all(db: Session) -> None:
    from app.models import UserRole, UserScope

    for model in (AuditLog, WorkOrder, Decision, PredictionOutcome, Prediction, EventRecent, ChannelDaily,
                  ModelThreshold, ModelMetric, UserScope, UserRole, Channel, Object, Recommendation, Reason, User):
        db.execute(delete(model))
    db.commit()


def seed(db: Session, scale: str = "demo", force: bool = False) -> bool:
    """Заполняет БД. Возвращает False, если данные уже есть и force не задан."""
    if db.scalar(select(func.count()).select_from(User)) and not force:
        log.info("Демо-данные уже есть — пропускаю (для пересоздания: --force)")
        return False
    if force:
        clear_all(db)

    cfg = SCALES[scale]
    rng = random.Random(SEED)
    start = datetime.combine(cfg["start"], datetime.min.time())
    end = datetime.combine(cfg["end"], datetime.max.time()).replace(microsecond=0)

    # Пользователи и справочники. В production демо-учётки нужны только как авторы демо-решений:
    # они заблокированы и со случайным паролем, войти под ними нельзя
    demo_login = not get_settings().is_production
    _bulk_insert(db, User, [
        {"id": i + 1, "username": u, "full_name": n, "role": r,
         "password_hash": hash_password(p if demo_login else secrets.token_urlsafe(32)),
         "auth_source": "local", "is_active": demo_login}
        for i, (u, p, n, r) in enumerate(DEMO_USERS)
    ])
    _bulk_insert(db, Reason, [
        {"id": i + 1, "code": c, "name": n, "decision_type": d, "sort_order": i, "is_active": True}
        for i, (c, n, d) in enumerate(REASONS)
    ])
    _bulk_insert(db, Recommendation, [
        {"id": i + 1, "code": c, "sensor_type": s, "text": t, "is_active": True}
        for i, (c, s, t) in enumerate(RECOMMENDATIONS)
    ])
    objects, parent_of = build_objects()
    _bulk_insert(db, Object, objects)

    channels = build_channels(rng, cfg["channels"], sorted(parent_of))
    _bulk_insert(db, Channel, [
        {"id": c.id, "object_id": c.object_id, "system_type": c.kind.system, "sensor_type": c.kind.sensor,
         "tag": c.tag, "name": c.name, **split_tag_levels(c.tag)}
        for c in channels
    ])
    log.info("Справочники: %d объектов, %d датчиков", len(objects), len(channels))
    apply_demo_access(db)

    # Отказы, события и суточные агрегаты
    simulate_faults(rng, channels, start, end)
    events, daily = [], []
    for ch in channels:
        evs = gen_events(rng, ch, start, end)
        events.extend(evs)
        daily.extend(daily_rows(ch.id, evs))
    _bulk_insert(db, EventRecent, events)
    _bulk_insert(db, ChannelDaily, daily)
    log.info("События: %d, суточных агрегатов: %d", len(events), len(daily))

    # Отказы по объекту верхнего уровня — для фактора «Отказов в этом объекте за 7 дней»
    faults_by_parent: dict[int, list[datetime]] = defaultdict(list)
    for ch in channels:
        faults_by_parent[parent_of.get(ch.object_id, ch.object_id)].extend(f.at for f in ch.faults)
    for v in faults_by_parent.values():
        v.sort()

    # Прогнозы и исходы
    predictions, outcomes, samples = [], [], []
    critical_ids: list[tuple[int, ChannelSim, datetime, bool]] = []
    pid = 0
    t = start
    while t <= end:
        for ch in channels:
            prob = round(predict_prob(rng, ch, t), 4)
            health = health_from_prob(prob)
            risk = risk_level_from_health(health)
            fts = faults_by_parent[parent_of.get(ch.object_id, ch.object_id)]
            obj_faults = bisect.bisect_right(fts, t) - bisect.bisect_right(fts, t - timedelta(days=7))
            pid += 1
            predictions.append({
                "id": pid, "channel_id": ch.id, "at": t, "horizon_h": 24, "prob": prob, "health": health,
                "risk_level": risk, "top_factors": factors_for(rng, ch, t, prob, obj_faults),
                "model_version": MODEL_VERSION,
            })
            nf = next_fault(ch, t)
            if t + timedelta(hours=24) <= end:  # исход известен, только если горизонт закрылся внутри данных
                outcomes.append({
                    "prediction_id": pid, "happened": nf is not None,
                    "fault_at": nf.at if nf else None, "fault_kind": nf.kind if nf else None, "labeled_at": t + timedelta(hours=24),
                })
                if t.hour == 0 and not recent_fault(ch, t, FAULT_EXCLUSION):
                    samples.append({
                        "prob": prob, "y": nf is not None, "sensor_type": ch.kind.sensor, "day": t.date(),
                        "fault_7d": recent_fault(ch, t, timedelta(days=7)) is not None,
                    })
            if risk in ("risk", "critical"):
                critical_ids.append((pid, ch, t, nf is not None))
        t += SNAPSHOT_STEP
    _bulk_insert(db, Prediction, predictions, chunk=2000)
    _bulk_insert(db, PredictionOutcome, outcomes)
    log.info("Прогнозы: %d, исходы: %d", len(predictions), len(outcomes))

    # Метрики модели по сгенерированным прогнозам
    n_days = max(1, len({s["day"] for s in samples}))
    metrics, thresholds = compute_metrics(samples, n_days, cfg["start"], cfg["end"])
    leads = []
    for ch in channels:
        for f in ch.faults:
            if f.detected and start + timedelta(hours=24) <= f.at <= end:
                # Упреждение — от первого среза, увидевшего отказ, до самого отказа
                first = f.at - timedelta(hours=24)
                first_snap = start + SNAPSHOT_STEP * math.ceil((first - start) / SNAPSHOT_STEP)
                leads.append((f.at - first_snap).total_seconds() / 3600)
    metrics.append({
        "model_version": MODEL_VERSION, "scope": "lead_time", "key": "median_h", "precision": None, "recall": None,
        "f1": None, "pr_auc": None, "support": len(leads), "value": round(statistics.median(leads), 1) if leads else None,
        "period_from": cfg["start"], "period_to": cfg["end"],
    })
    _bulk_insert(db, ModelMetric, metrics)
    _bulk_insert(db, ModelThreshold, thresholds)

    # Решения диспетчера и заявки — на части тревожных прогнозов
    seed_decisions_and_orders(db, rng, critical_ids, parent_of)

    reset_sequences(db, [User.__table__, Reason.__table__, Recommendation.__table__, Prediction.__table__,
                         Decision.__table__, WorkOrder.__table__, ModelMetric.__table__, ModelThreshold.__table__,
                         EventRecent.__table__])
    db.commit()
    # Справочник рекомендаций дополняется строками правил ТО (app/recommendations/rules.yaml)
    from app.recommendations.engine import sync_dictionary

    sync_dictionary(db)
    selected = next(t for t in thresholds if t["is_selected"])
    log.info("Готово. Рабочий порог %.2f: precision %.2f, recall %.2f", selected["threshold"], selected["precision"], selected["recall"])
    return True


def seed_decisions_and_orders(db: Session, rng: random.Random, alerts: list, parent_of: dict) -> None:
    reasons = {c: i + 1 for i, (c, _, _) in enumerate(REASONS)}
    rec_by_sensor = {s: i + 1 for i, (_, s, _) in enumerate(RECOMMENDATIONS) if s}
    users = {"dispatcher": 1, "engineer": 2}
    # Берём по одному тревожному прогнозу на датчик за сутки, чтобы решения не дублировались по срезам
    seen, candidates = set(), []
    for pid, ch, t, happened in alerts:
        key = (ch.id, t.date())
        if key not in seen and not (ch.id == EXAMPLE_CHANNEL_ID and t == EXAMPLE_AT):
            seen.add(key)
            candidates.append((pid, ch, t, happened))
    picked = rng.sample(candidates, min(len(candidates), max(12, len(candidates) // 6)))
    picked.sort(key=lambda x: x[2])

    decisions, orders = [], []
    for i, (pid, ch, t, happened) in enumerate(picked, start=1):
        if happened:
            dtype = "dispatch" if rng.random() < 0.8 else "monitor"
        else:
            dtype = rng.choice(["false_alarm", "false_alarm", "monitor", "dispatch"])
        reason = {
            "dispatch": rng.choice(["model_risk_confirmed", "repeated_fault_msgs", "link_loss"]
                                   + (["power_loss"] if ch.kind.group == "power" else [])),
            "false_alarm": rng.choice(["planned_works", "known_interference", "comm_glitch", "staff_pass"]),
            "monitor": rng.choice(["need_more_data", "single_spike", "wait_related_service"]),
        }[dtype]
        created = t + timedelta(minutes=rng.randint(5, 90))
        decisions.append({
            "id": i, "prediction_id": pid, "decision_type": dtype, "reason_id": reasons[reason],
            "comment": rng.choice([None, None, "Связались с участком", "Сверили с журналом смены", "Повторить проверку через 6 ч"]),
            "user_id": users["dispatcher"], "created_at": created,
        })
        if dtype == "dispatch":
            n = len(orders) + 1
            status = rng.choice(["draft", "submitted", "in_progress", "done", "done", "cancelled"])
            priority = "critical" if rng.random() < 0.4 else "high"
            wo_created = created + timedelta(minutes=rng.randint(2, 30))
            closed = wo_created + timedelta(hours=rng.uniform(2, 30)) if status in ("done", "cancelled") else None
            rec_id = rec_by_sensor.get(ch.kind.sensor, len(RECOMMENDATIONS) - 1)
            orders.append({
                "id": n, "number": f"ЗН-{wo_created.year}-{n:06d}", "prediction_id": pid, "channel_id": ch.id,
                "object_id": ch.object_id, "status": status, "priority": priority, "reason_id": reasons[reason],
                "recommendation_id": rec_id, "recommendation_text": RECOMMENDATIONS[rec_id - 1][2],
                "description": f"Черновик из прогноза отказа на 24 ч по датчику «{ch.name}».",
                "assignee": rng.choice([None, "Бригада КИПиА №1", "Бригада КИПиА №2", "Электромонтёры участка"]),
                "due_at": wo_created + (timedelta(hours=4) if priority == "critical" else timedelta(hours=24)),
                "created_by": users["dispatcher"], "created_at": wo_created,
                "updated_at": closed or wo_created, "closed_at": closed,
            })
    _bulk_insert(db, Decision, decisions)
    _bulk_insert(db, WorkOrder, orders)
    log.info("Решений: %d, заявок: %d", len(decisions), len(orders))


def main() -> None:
    parser = argparse.ArgumentParser(description="Демо-данные сервиса прогноза отказов")
    parser.add_argument("--scale", choices=sorted(SCALES), default="demo")
    parser.add_argument("--force", action="store_true", help="Удалить все данные и засеять заново")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from app.db import SessionLocal

    with SessionLocal() as db:
        seed(db, args.scale, args.force)


if __name__ == "__main__":
    main()
