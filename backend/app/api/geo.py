"""Схема коллекторов по пикетам в GeoJSON и WKT (ТЗ, раздел 7: геоданные GeoJSON, WKT). Геометрия СИНТЕТИЧЕСКАЯ:
координат у заказчика нет, схема строится из пикетов в названиях датчиков (app/geo, docs/GEO_SCHEME.md)."""

import threading
from collections import OrderedDict
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app import schemas
from app.access import Access, get_access
from app.db import get_db
from app.deps import at_param
from app.geo.scheme import CRS_NOTE, Scheme, build, clusters
from app.labels import RISK_ORDER
from app.models import Channel, Object, Prediction
from app.risk import level_expr, level_in
from app.services import ObjectIndex, apply_channel_filters, snapshot_at

router = APIRouter(prefix="/api/geo", tags=["Схема коллекторов (GeoJSON, WKT)"])

ALERT = ("risk", "critical")
_cache: OrderedDict = OrderedDict()
_lock = threading.Lock()


def _scheme(db: Session, idx: ObjectIndex) -> Scheme:
    """Схема в пределах области пользователя: строится только из видимых объектов и датчиков (раскладка другой
    области ничего не говорит о чужих объектах). Кэш — по области и версии справочников."""
    channels = apply_channel_filters(select(Channel.id, Channel.object_id, Channel.name), idx)
    version = db.execute(select(func.count(Channel.id), func.max(Channel.id), func.count(func.distinct(Channel.object_id)))
                         ).one()
    objects_version = db.execute(select(func.count(Object.id), func.max(Object.id))).one()
    key = (idx.scope_key, tuple(version), tuple(objects_version))
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    objects = {i: (n.name, n.level, n.parent_id) for i, n in idx.nodes.items()}
    scheme = build(objects, [tuple(r) for r in db.execute(channels)])
    with _lock:
        _cache[key] = scheme
        while len(_cache) > 16:
            _cache.popitem(last=False)
    return scheme


def _risk(db: Session, idx: ObjectIndex, snap) -> dict[int, tuple[str, float]]:
    if snap is None:
        return {}
    stmt = apply_channel_filters(
        select(Prediction.channel_id, level_expr(), Prediction.prob)
        .join(Channel, Channel.id == Prediction.channel_id).where(Prediction.at == snap), idx)
    return {cid: (lvl, prob) for cid, lvl, prob in db.execute(stmt)}


def _wkt(geom: dict) -> str:
    def pts(coords):
        return ", ".join(f"{x:g} {y:g}" for x, y in coords)

    t, c = geom["type"], geom["coordinates"]
    if t == "Point":
        return f"POINT ({c[0]:g} {c[1]:g})"
    if t == "LineString":
        return f"LINESTRING ({pts(c)})"
    return "MULTILINESTRING (" + ", ".join(f"({pts(line)})" for line in c) + ")"


def _out(features: list[dict], fmt: str, meta: dict):
    base = {"synthetic": True, "crs_note": CRS_NOTE, **meta}
    if fmt == "wkt":
        return {**base, "items": [{**f["properties"], "wkt": _wkt(f["geometry"])} for f in features]}
    return {"type": "FeatureCollection", **base, "features": features}


def _complex_filter(idx: ObjectIndex, object_id: int | None) -> set[int] | None:
    return None if object_id is None else set(idx.subtree_ids(object_id)) | {object_id}


@router.get(
    "/collectors",
    summary="Коллекторы: основное тело, галереи, участки подобъектов, проблемные зоны (GeoJSON / WKT)",
    description=(
        "**Геометрия синтетическая** (координат у заказчика нет): условные метры, 1 ПК = 10 м. Объекты-комплексы — "
        "осевые `MultiLineString` (основное тело + боковые галереи), подобъекты — участки оси (`LineString`) или "
        "узлы (`Point`), проблемные зоны — подряд идущие датчики «Риск»/«Критично» на оси с разрывом не больше "
        "50 м (5 пикетов). Уровень риска — на срез прогнозов не позже `at`. Только объекты области пользователя. "
        "`format=wkt` — те же объекты списком со строкой WKT. Правила — docs/GEO_SCHEME.md."
    ),
    response_model=schemas.GeoCollectors,
    response_model_exclude_none=True,
)
def collectors(
    object_id: int | None = Query(None, description="Район или объект-комплекс; включает вложенные"),
    format: Literal["geojson", "wkt"] = Query("geojson"),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    keep = _complex_filter(idx, object_id)
    scheme = _scheme(db, idx)
    snap = snapshot_at(db, at)
    risk = _risk(db, idx, snap)
    features: list[dict] = []
    by_complex: dict[int, list[int]] = {}
    for p in scheme.places.values():
        by_complex.setdefault(p.complex_id, []).append(p.channel_id)
    for cx, c in scheme.complexes.items():
        if keep is not None and cx not in keep and not (keep & set(c.sections) | keep & set(c.nodes)):
            continue
        cids = by_complex.get(cx, [])
        counts = {lvl: 0 for lvl in RISK_ORDER}
        for cid in cids:
            if cid in risk:
                counts[risk[cid][0]] += 1
        present = [lvl for lvl, n in counts.items() if n]
        lines = [[[0.0, c.y0], [round(c.length_m, 1), c.y0]]]
        for g in c.galleries:
            sign = 1 if g.up else -1
            lines.append([[round(g.anchor_m, 1), c.y0], [round(g.anchor_m, 1), round(c.y0 + sign * g.length_m, 1)]])
        features.append({"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": lines}, "properties": {
            "kind": "collector", "object_id": cx, "name": c.name, "path": idx.path(cx), "length_m": round(c.length_m, 1),
            "pk_max": int(c.length_m // 10), "galleries": len(c.galleries), "channels": len(cids),
            "risk_counts": counts, "max_risk_level": max(present, key=RISK_ORDER.__getitem__) if present else None,
        }})
        for sub, (x0, x1) in sorted(c.sections.items()):
            if keep is not None and cx not in keep and sub not in keep:
                continue
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [
                [round(x0, 1), c.y0 + 12.0], [round(max(x1, x0 + 10.0), 1), c.y0 + 12.0]]}, "properties": {
                "kind": "section", "object_id": sub, "parent_id": cx, "name": idx.nodes[sub].name if sub in idx.nodes else "—",
            }})
        for sub, (nx, ny) in sorted(c.nodes.items()):
            if keep is not None and cx not in keep and sub not in keep:
                continue
            features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [nx, ny]}, "properties": {
                "kind": "node", "object_id": sub, "parent_id": cx, "name": idx.nodes[sub].name if sub in idx.nodes else "—",
            }})
        axis = [(scheme.places[cid].x, cid) for cid in cids
                if scheme.places[cid].placed_by == "picket" and risk.get(cid, ("normal",))[0] in ALERT]
        for x0, x1, members in clusters(axis):
            crit = sum(1 for m in members if risk[m][0] == "critical")
            features.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": [
                [round(x0 - 10, 1), c.y0], [round(x1 + 10, 1), c.y0]]}, "properties": {
                "kind": "problem_zone", "object_id": cx, "name": f"{c.name}: ПК{int(x0 // 10)}–ПК{int(x1 // 10)}",
                "channels": len(members), "critical": crit, "channel_ids": members,
                "max_prob": round(max(risk[m][1] for m in members), 4),
            }})
    return _out(features, format, {"at": at, "snapshot_at": snap, "stats": scheme.stats})


@router.get(
    "/channels",
    summary="Датчики на схеме: точки по пикетам с уровнем риска (GeoJSON / WKT)",
    description=(
        "**Геометрия синтетическая**: точка на оси по пикету из названия, в галерее — по её пикету, без пикета — "
        "в узле объекта (`placed_by`: picket | gallery | node). Фильтры — как у списка датчиков; только датчики "
        "области пользователя. Уровень риска и вероятность — на срез прогнозов не позже `at`. Пустые свойства "
        "не передаются; название объекта — в `/api/geo/collectors` (участки и узлы по `object_id`)."
    ),
    response_model=schemas.GeoChannels,
    response_model_exclude_none=True,
)
def channels(
    object_id: int | None = Query(None, description="Объект; включает вложенные"),
    system_type: str | None = None,
    sensor_type: str | None = None,
    risk_level: list[schemas.RiskLevel] | None = Query(None, description="Можно несколько"),
    format: Literal["geojson", "wkt"] = Query("geojson"),
    at=Depends(at_param),
    access: Access = Depends(get_access),
    db: Session = Depends(get_db),
):
    idx = ObjectIndex.load(db, access)
    scheme = _scheme(db, idx)
    snap = snapshot_at(db, at)
    stmt = select(Channel.id, Channel.name, Channel.sensor_type, Channel.system_type, Channel.object_id,
                  Prediction.prob, level_expr().label("lvl"))
    stmt = stmt.select_from(Channel).outerjoin(Prediction, and_(Prediction.channel_id == Channel.id, Prediction.at == snap))
    stmt = apply_channel_filters(stmt, idx, object_id, system_type, sensor_type)
    if risk_level:
        stmt = stmt.where(level_in(risk_level))
    features = []
    for cid, name, sensor, system, oid, prob, lvl in db.execute(stmt.order_by(Channel.id)):
        pl = scheme.places.get(cid)
        if pl is None:
            continue
        # Тысячи точек: пустые свойства не передаём, путь объекта — у участков в /collectors (по object_id)
        props = {
            "channel_id": cid, "name": name, "sensor_type": sensor, "system_type": system, "object_id": oid,
            "risk_level": lvl if prob is not None else None, "prob": round(prob, 3) if prob is not None else None,
            "picket": pl.picket.label or None, "placed_by": pl.placed_by, "side": pl.picket.side,
            "feature": pl.picket.feature,
        }
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(pl.x, 1), round(pl.y, 1)]},
                         "properties": {k: v for k, v in props.items() if v is not None}})
    return _out(features, format, {"at": at, "snapshot_at": snap, "total": len(features)})
