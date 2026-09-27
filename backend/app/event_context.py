"""Разметка контекста тревожных событий прозрачными правилами (не ML) — чтобы диспетчер отсеивал ложные выезды.

Правила — по официальным ответам заказчика (docs/CUSTOMER_ANSWERS.md) и разметке модели v3 (docs/ML_REPORT.md):

- planned_check «Вероятная плановая проверка»: «Обнаружен газ» или показание газового датчика ≥ 1 % (порог тревоги)
  в будни с 9 до 14 — заказчик подтвердил, что это проверки контрольными баллонами и обходы техников; по данным
  2023–2026 так выглядят 88 % событий «Обнаружен газ».
- group_off «Групповое отключение — вероятно, плановые работы»: «Неисправен» / «Отключено устройство» / «Обесточен»,
  и в пределах ±12 ч такие же сообщения или потеря связи (отказы разметки ML) есть у ≥ 80 % и не меньше 5
  однотипных датчиков того же подобъекта или объекта-комплекса — как правило v3 (GROUP_MIN_SHARE, GROUP_MIN_CH,
  GROUP_TOL_H в ml/features/build_base.py). Упрощение: доля считается от всех однотипных датчиков объекта, а не
  только «активных» — правило срабатывает реже, не чаще.
- value_failure «Сбой значения»: служебный код производителя (-127, -100, 255, 327,68, -3276,8 …, |x| ≥ 1000),
  дата вместо показания («01.01.1970 03:00:00») или отрицательный газ — по ответу заказчика это неисправность,
  а не реальная величина.
"""

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import Channel, ChannelFault, EventRecent

GAS_ALARM_PCT = 1.0
CHECK_HOURS = (9, 14)  # [9, 14)
SENTINEL_CODES = (-127.0, -100.0, 255.0, 327.68, -3276.8, -3276.0, 3276.7, 32767.0, -32768.0, 65535.0)
GROUP_STATUSES = ("Неисправен", "Отключено устройство", "Обесточен")
GROUP_MIN_SHARE, GROUP_MIN_CH, GROUP_TOL = 0.8, 5, timedelta(hours=12)
DATE_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}(\s+\d{1,2}:\d{2}(:\d{2})?)?$")

LABELS = {
    "planned_check": "Вероятная плановая проверка",
    "group_off": "Групповое отключение — вероятно, плановые работы",
    "value_failure": "Сбой значения",
}
VALUE_KIND_LABELS = {"status": "статус", "number": "показание", "service": "служебный код", "date": "дата вместо значения"}


@dataclass
class Tag:
    code: str
    label: str
    reason: str


def is_gas(sensor_type: str) -> bool:
    return "газ" in (sensor_type or "").lower()


def value_kind(e: EventRecent) -> str:
    if e.value_text:
        return "date" if DATE_RE.match(e.value_text.strip()) else "status"
    if e.value_num is not None:
        return "service" if e.value_num in SENTINEL_CODES or abs(e.value_num) >= 1000 else "number"
    return "date" if DATE_RE.match((e.value_raw or "").strip()) else "status"


def _num(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def simple_tags(e: EventRecent, ch: Channel) -> list[Tag]:
    """Правила, которым хватает самого события: плановая проверка газа и сбой значения."""
    tags = []
    kind = value_kind(e)
    gas = is_gas(ch.sensor_type)
    if kind in ("service", "date"):
        what = "дата вместо показания" if kind == "date" else f"служебный код {_num(e.value_num)}"
        tags.append(Tag("value_failure", LABELS["value_failure"], f"{what} — по ответу заказчика это сбой, а не реальная величина"))
    elif gas and kind == "number" and e.value_num is not None and e.value_num < 0:
        tags.append(Tag("value_failure", LABELS["value_failure"], "отрицательное значение газа — по ответу заказчика неисправность"))
    in_hours = e.ts.weekday() < 5 and CHECK_HOURS[0] <= e.ts.hour < CHECK_HOURS[1]
    gas_event = (e.value_text or "").strip() == "Обнаружен газ" or (
        gas and kind == "number" and e.value_num is not None and e.value_num >= GAS_ALARM_PCT)
    if gas_event and in_hours:
        tags.append(Tag("planned_check", LABELS["planned_check"],
                        "газ в будни 9–14: так выглядят плановые проверки баллонами и обходы (88 % таких событий)"))
    return tags


def annotate(db: Session, events: Sequence[EventRecent], channels: dict[int, Channel],
             parent_of: dict[int, int | None]) -> dict[int, list[Tag]]:
    """Теги для страницы событий. Групповое отключение — отдельными запросами по соседям в окне ±12 ч."""
    result = {e.id: simple_tags(e, channels[e.channel_id]) for e in events}
    candidates = [e for e in events if (e.value_text or "").strip() in GROUP_STATUSES]
    if not candidates:
        return result
    # Однотипные соседи по подобъекту и по объекту-комплексу
    keys = {(channels[e.channel_id].object_id, channels[e.channel_id].sensor_type) for e in candidates}
    obj_ids = {k[0] for k in keys} | {parent_of.get(k[0]) for k in keys if parent_of.get(k[0]) is not None}
    by_obj_type: dict[tuple[int, str], set[int]] = defaultdict(set)
    sub_objects: dict[int, list[int]] = defaultdict(list)
    for oid, parent in parent_of.items():
        if parent in obj_ids:
            sub_objects[parent].append(oid)
    lookup_objs = obj_ids | {o for p in obj_ids for o in sub_objects.get(p, [])}
    types = {k[1] for k in keys}
    for cid, oid, st in db.execute(select(Channel.id, Channel.object_id, Channel.sensor_type)
                                   .where(Channel.object_id.in_(lookup_objs), Channel.sensor_type.in_(types))):
        by_obj_type[(oid, st)].add(cid)
        parent = parent_of.get(oid)
        if parent is not None:
            by_obj_type[(parent, st)].add(cid)
    lo = min(e.ts for e in candidates) - GROUP_TOL
    hi = max(e.ts for e in candidates) + GROUP_TOL
    all_ids = set().union(*by_obj_type.values()) if by_obj_type else set()
    marks: dict[int, list[datetime]] = defaultdict(list)
    if all_ids:
        ids = sorted(all_ids)
        for cid, ts in db.execute(select(EventRecent.channel_id, EventRecent.ts).where(
                EventRecent.channel_id.in_(ids), EventRecent.ts.between(lo, hi),
                or_(*[EventRecent.value_text == s for s in GROUP_STATUSES]))):
            marks[cid].append(ts)
        for cid, ts in db.execute(select(ChannelFault.channel_id, ChannelFault.ts).where(
                ChannelFault.channel_id.in_(ids), ChannelFault.ts.between(lo, hi))):
            marks[cid].append(ts)
    for e in candidates:
        ch = channels[e.channel_id]
        for scope_id, scope_name in ((ch.object_id, "подобъекта"), (parent_of.get(ch.object_id), "объекта")):
            peers = by_obj_type.get((scope_id, ch.sensor_type), set()) if scope_id is not None else set()
            if len(peers) < GROUP_MIN_CH:
                continue
            hit = sum(1 for p in peers if any(abs(t - e.ts) <= GROUP_TOL for t in marks.get(p, ())))
            if hit / len(peers) >= GROUP_MIN_SHARE:
                result[e.id].append(Tag("group_off", LABELS["group_off"],
                                        f"в пределах ±12 ч так же отключились {hit} из {len(peers)} однотипных "
                                        f"датчиков {scope_name} — это плановые работы или отключение объекта, "
                                        "а не отказ одного датчика"))
                break
    return result
