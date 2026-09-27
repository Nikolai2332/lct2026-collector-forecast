"""Синтетическая геометрия коллекторов в условных метрах (docs/GEO_SCHEME.md). СХЕМА УСЛОВНАЯ.

Координат у заказчика нет (ответ организаторов): разрешено сгенерировать синтетическую геометрию — осевые
MultiLineString, основное тело коллектора и боковые галереи — и показывать линейную схему с пикетами.
Правила (детерминированно: один и тот же справочник → одна и та же схема):

- Каждый объект-комплекс (уровень 2) — своя горизонтальная «полоса». Основное тело — ось вдоль X от ПК0 до
  последнего пикета датчиков комплекса + 20 м (1 ПК = 10 м). Полосы идут сверху вниз по id объекта; расстояние
  между ними — по длине галерей, чтобы галереи соседей не пересекались.
- Боковая галерея — перпендикуляр к оси в точке примыкания (пикет основного тела), длина — до последнего
  пикета галереи + 10 м; нечётные номера — вверх, чётные и без номера — вниз. Галерея без точки примыкания
  («Г1 ПК2») берёт её у других датчиков той же галереи комплекса, иначе примыкает к ПК0.
- Подобъект (уровень 3) — участок основного тела от первого до последнего пикета его датчиков (линия,
  параллельная оси); подобъект без пикетов — узел слева от ПК0.
- Датчик — точка на оси по пикету; сторона «лев./прав.» — ±4 м от оси; несколько датчиков в одной точке
  разводятся сеткой 4 в ряд с шагом 1,5 м. Без пикета — сетка в узле своего подобъекта слева от ПК0.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from app.geo.pickets import PICKET_M, Picket, parse

CRS_NOTE = "Схема условная: геометрия синтетическая, координаты — условные метры (1 ПК = 10 м), не географические."
SIDE_OFFSET_M = 4.0
STACK_M = 1.5
NODE_X_M = -60.0  # узел объекта (датчики без пикета) — левее ПК0
MIN_LEN_M = 100.0
ROW_MARGIN_M = 150.0


@dataclass
class ChannelPlace:
    channel_id: int
    x: float
    y: float
    picket: Picket
    placed_by: str  # picket | gallery | node
    complex_id: int


@dataclass
class Gallery:
    key: str
    anchor_m: float
    length_m: float
    up: bool


@dataclass
class ComplexScheme:
    object_id: int
    name: str
    y0: float
    length_m: float
    galleries: list[Gallery] = field(default_factory=list)
    sections: dict[int, tuple[float, float]] = field(default_factory=dict)  # подобъект → участок оси [x0, x1]
    nodes: dict[int, tuple[float, float]] = field(default_factory=dict)  # подобъект без пикетов → точка узла


@dataclass
class Scheme:
    complexes: dict[int, ComplexScheme]
    places: dict[int, ChannelPlace]
    stats: dict


def _gallery_key(p: Picket) -> str:
    return p.gallery or "?"


def build(objects: dict[int, tuple[str, int, int | None]], channels: list[tuple[int, int, str]]) -> Scheme:
    """objects: id → (название, уровень, parent_id); channels: (id, object_id, название). Порядок не важен."""
    complex_of: dict[int, int | None] = {}

    def find_complex(oid: int) -> int | None:
        if oid not in complex_of:
            cur, seen = oid, set()
            while cur is not None and cur in objects and objects[cur][1] > 2 and cur not in seen:
                seen.add(cur)
                cur = objects[cur][2]
            complex_of[oid] = cur if cur in objects and objects[cur][1] == 2 else None
        return complex_of[oid]

    parsed = {cid: (oid, parse(name)) for cid, oid, name in sorted(channels)}
    by_complex: dict[int, list[int]] = defaultdict(list)
    orphans = []
    for cid, (oid, _) in parsed.items():
        cx = find_complex(oid)
        (by_complex[cx] if cx is not None else orphans).append(cid)

    # Точки примыкания галерей по комплексу: из датчиков, где есть и основной пикет, и галерея
    anchors: dict[tuple[int, str], list[float]] = defaultdict(list)
    for cx, cids in by_complex.items():
        for cid in cids:
            p = parsed[cid][1]
            if p.gallery_m is not None and p.main_m is not None:
                anchors[(cx, _gallery_key(p))].append(p.main_m)

    complexes: dict[int, ComplexScheme] = {}
    places: dict[int, ChannelPlace] = {}
    y = 0.0
    for cx in sorted(by_complex):
        cids = by_complex[cx]
        main_positions = [parsed[c][1].main_m for c in cids if parsed[c][1].main_m is not None]
        for c in cids:
            rng = parsed[c][1].range_m
            if rng:
                main_positions.append(rng[1])
        length = max(MIN_LEN_M, max(main_positions, default=0.0) + 20.0)
        # Галереи: (ключ, точка примыкания) → длина
        gal_len: dict[tuple[str, float], float] = defaultdict(float)
        for c in cids:
            p = parsed[c][1]
            if p.gallery_m is None:
                continue
            key = _gallery_key(p)
            anchor = p.main_m if p.main_m is not None else (min(anchors[(cx, key)]) if anchors[(cx, key)] else 0.0)
            gal_len[(key, anchor)] = max(gal_len[(key, anchor)], p.gallery_m + PICKET_M, 30.0)
        galleries = [
            Gallery(key, anchor, ln, up=(key.isdigit() and int(key) % 2 == 1))
            for (key, anchor), ln in sorted(gal_len.items(), key=lambda kv: (kv[0][1], kv[0][0]))
        ]
        up_len = max((g.length_m for g in galleries if g.up), default=0.0)
        down_len = max((g.length_m for g in galleries if not g.up), default=0.0)
        y -= up_len + ROW_MARGIN_M  # Y растёт вверх; полосы идут вниз
        scheme = ComplexScheme(cx, objects[cx][0], y, length, galleries)
        complexes[cx] = scheme
        _place_channels(scheme, cids, parsed, anchors, places)
        y -= down_len + ROW_MARGIN_M

    total = len(parsed)
    by_rule = defaultdict(int)
    for pl in places.values():
        by_rule[pl.placed_by] += 1
    stats = {
        "channels": total,
        "with_picket": sum(1 for _, p in parsed.values() if p.parsed),
        "on_axis": by_rule["picket"],
        "in_gallery": by_rule["gallery"],
        "in_node": by_rule["node"],
        "without_complex": len(orphans),
        "galleries": sum(len(c.galleries) for c in complexes.values()),
    }
    return Scheme(complexes, places, stats)


def _place_channels(scheme: ComplexScheme, cids: list[int], parsed, anchors, places) -> None:
    cx, y0 = scheme.object_id, scheme.y0
    gal_by_key = defaultdict(list)
    for g in scheme.galleries:
        gal_by_key[g.key].append(g)
    used: dict[tuple[float, float], int] = defaultdict(int)
    per_sub_x: dict[int, list[float]] = defaultdict(list)
    no_picket: dict[int, list[int]] = defaultdict(list)

    for cid in cids:
        oid, p = parsed[cid]
        if p.gallery_m is not None:
            key = _gallery_key(p)
            anchor = p.main_m if p.main_m is not None else (min(anchors[(cx, key)]) if anchors[(cx, key)] else 0.0)
            g = next((g for g in gal_by_key[key] if g.anchor_m == anchor), None)
            sign = 1 if g is not None and g.up else -1
            side = SIDE_OFFSET_M if p.side == "right" else -SIDE_OFFSET_M if p.side == "left" else 0.0
            x, yy = anchor + side, y0 + sign * max(p.gallery_m, 2.0)
            placed_by = "gallery"
            per_sub_x[oid].append(anchor)
        elif p.main_m is not None:
            side = SIDE_OFFSET_M if p.side == "left" else -SIDE_OFFSET_M if p.side == "right" else 0.0
            x, yy = p.main_m, y0 + side
            placed_by = "picket"
            per_sub_x[oid].append(p.main_m)
            if p.range_m:
                per_sub_x[oid].extend(p.range_m)
        else:
            no_picket[oid].append(cid)
            continue
        k = used[(round(x, 1), round(yy, 1))]
        used[(round(x, 1), round(yy, 1))] += 1
        # Несколько датчиков в одной точке — сеткой 4 в ряд вдоль оси (не «столбом» поперёк)
        places[cid] = ChannelPlace(cid, x + (k % 4) * STACK_M, yy + (k // 4) * STACK_M * (1 if yy >= y0 else -1),
                                   p, placed_by, cx)

    for oid, xs in per_sub_x.items():
        scheme.sections[oid] = (min(xs), max(xs))
    # Узлы подобъектов без пикетов — столбиком слева от ПК0; датчики — сетка 6 в ряд
    for i, oid in enumerate(sorted(no_picket)):
        nx, ny = NODE_X_M, y0 - i * 25.0
        scheme.nodes[oid] = (nx, ny)
        for j, cid in enumerate(sorted(no_picket[oid])):
            places[cid] = ChannelPlace(cid, nx - (j % 6) * 4.0, ny - (j // 6) * 4.0, parsed[cid][1], "node", cx)


def clusters(points: list[tuple[float, int]], gap_m: float = 50.0, min_size: int = 2) -> list[tuple[float, float, list[int]]]:
    """Проблемные участки: подряд идущие тревожные датчики оси с разрывом не больше gap_m (5 пикетов)."""
    result, cur = [], []
    for x, cid in sorted(points):
        if cur and x - cur[-1][0] > gap_m:
            if len(cur) >= min_size:
                result.append((cur[0][0], cur[-1][0], [c for _, c in cur]))
            cur = []
        cur.append((x, cid))
    if len(cur) >= min_size:
        result.append((cur[0][0], cur[-1][0], [c for _, c in cur]))
    return result
