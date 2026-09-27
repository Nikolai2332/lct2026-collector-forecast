"""Схема коллекторов по пикетам: разбор названий, синтетическая геометрия, API GeoJSON/WKT с областью видимости."""

import pytest

from app.geo.pickets import parse
from app.geo.scheme import build, clusters

AT = "2026-08-01T12:00:00"


@pytest.mark.parametrize("name, main, gallery, gallery_m, rng, side, feature", [
    ("Дым ПК447+5", 4475.0, None, None, None, None, None),
    ("ГАЗ Д34 пк2", 20.0, None, None, None, None, None),
    ("ДД ПК176+6,5(лев.)", 1766.5, None, None, None, "left", None),
    ("ГРО12 ПК113(ПК113-ПК126 прав.)", 1130.0, None, None, (1130.0, 1260.0), "right", None),
    ("ДД ПК187 - 184", 1870.0, None, None, (1840.0, 1870.0), None, None),
    ("ТД ПК146+5-ПК154 (15 шт.)", 1465.0, None, None, (1465.0, 1540.0), None, None),
    ("Управление ОЗК В5 ПК44 Г1 ПК14", 440.0, "1", 140.0, None, None, None),
    ("КД дверь склад ПК396Г3ПК0", 3960.0, "3", 0.0, None, None, None),
    ("Управление В12 ПК305Гал.ПК2", 3050.0, "?", 20.0, None, None, None),
    ("ОД АВ ПК167 Гал. ПК12", 1670.0, "?", 120.0, None, None, None),
    ("В3 ПК53 Г.АТС ПК9 (26БК)", 530.0, "АТС", 90.0, None, None, None),
    ("КД АВ1 3 этаж ПК18 Гал.172", 1720.0, "?", 180.0, None, None, None),
    ("ДД Г1 ПК2+6", None, "1", 26.0, None, None, None),
    ("Н1 ПК863 Гал.1 ПК7", 8630.0, "1", 70.0, None, None, None),  # не «галерея от ПК1 на 8,6 км»
    ("ОД развилка в Г1 ПК200", 2000.0, None, None, None, None, None),
    ("ОД ВШ ПК57", 570.0, None, None, None, None, "ВШ"),
    ("Управление ФАНС3", None, None, None, None, None, "АНС"),  # «Управление» — не «прав.»
    ("КД шкаф ОПС ДП Никулино", None, None, None, None, None, None),
])
def test_parse(name, main, gallery, gallery_m, rng, side, feature):
    p = parse(name)
    assert (p.main_m, p.gallery, p.gallery_m, p.range_m, p.side, p.feature) == (main, gallery, gallery_m, rng, side, feature)
    assert p.parsed == (main is not None or gallery_m is not None)


OBJECTS = {1: ("Район", 1, None), 2: ("объект А", 2, 1), 3: ("объект Б", 2, 1), 10: ("А-1", 3, 2), 11: ("А-2", 3, 2),
           20: ("Б-1", 3, 3)}
CHANNELS = [(1, 10, "Дым ПК10"), (2, 10, "Дым ПК10"), (3, 10, "ДД ПК12(лев.)"), (4, 11, "Дым ПК44 Г1 ПК5"),
            (5, 11, "ДД Г1 ПК8"), (6, 11, "КД дверь ДП"), (7, 20, "Темп. ПК300+5"), (8, 20, "Газ Д2 ПК2 Г2 ПК3")]


def test_build_is_deterministic_and_places_everything():
    a, b = build(OBJECTS, CHANNELS), build(OBJECTS, list(reversed(CHANNELS)))
    assert {k: (v.x, v.y) for k, v in a.places.items()} == {k: (v.x, v.y) for k, v in b.places.items()}
    assert set(a.places) == {c[0] for c in CHANNELS}
    assert a.stats == {"channels": 8, "with_picket": 7, "on_axis": 4, "in_gallery": 3, "in_node": 1,
                       "without_complex": 0, "galleries": 2}
    pa = a.places
    assert (pa[1].x, pa[2].x) == (100.0, 101.5) and pa[1].y == pa[2].y  # одна точка — разведены вдоль оси
    assert pa[3].y > pa[1].y  # «лев.» — выше оси
    # галерея без точки примыкания берёт её у другого датчика той же галереи (ПК44), нечётная — вверх
    assert pa[5].x == pa[4].x == 440.0 and pa[5].y > a.complexes[2].y0 and pa[4].y > a.complexes[2].y0
    assert pa[8].y < a.complexes[3].y0  # чётная галерея — вниз
    assert pa[6].placed_by == "node" and pa[6].x < 0
    assert a.complexes[2].y0 > a.complexes[3].y0  # полосы сверху вниз по id
    assert a.complexes[3].length_m == 3025.0  # ПК300+5 + 20 м
    assert all(g.length_m <= 100 for c in a.complexes.values() for g in c.galleries)


def test_clusters():
    assert clusters([(100, 1), (130, 2), (400, 3), (420, 4), (900, 5)]) == [(100, 130, [1, 2]), (400, 420, [3, 4])]


def test_geo_api_geojson_and_wkt(client, auth):
    r = client.get("/api/geo/collectors", params={"at": AT}, headers=auth("dispatcher"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["type"] == "FeatureCollection" and body["synthetic"] is True and "условн" in body["crs_note"]
    kinds = {f["properties"]["kind"] for f in body["features"]}
    assert "collector" in kinds
    col = next(f for f in body["features"] if f["properties"]["kind"] == "collector")
    assert col["geometry"]["type"] == "MultiLineString"
    wkt = client.get("/api/geo/collectors", params={"at": AT, "format": "wkt"}, headers=auth("dispatcher")).json()
    assert wkt["items"][0]["wkt"].startswith("MULTILINESTRING (")
    ch = client.get("/api/geo/channels", params={"at": AT}, headers=auth("dispatcher")).json()
    assert ch["total"] == 60 and all(f["geometry"]["type"] == "Point" for f in ch["features"])
    crit = client.get("/api/geo/channels", params={"at": AT, "risk_level": "critical"}, headers=auth("dispatcher")).json()
    assert all(f["properties"]["risk_level"] == "critical" for f in crit["features"])
    pts = client.get("/api/geo/channels", params={"at": AT, "format": "wkt"}, headers=auth("dispatcher")).json()
    assert pts["items"][0]["wkt"].startswith("POINT (")


def test_geo_respects_scope(client, auth):
    tech = client.post("/api/auth/login", json={"username": "technician", "password": "technician123"}).json()
    headers = {"Authorization": f"Bearer {tech['access_token']}"}
    complex_id = tech["user"]["scope"][0]["id"]
    body = client.get("/api/geo/collectors", params={"at": AT}, headers=headers).json()
    assert {f["properties"]["object_id"] for f in body["features"] if f["properties"]["kind"] == "collector"} == {complex_id}
    mine = client.get("/api/channels", params={"at": AT, "limit": 500}, headers=headers).json()
    pts = client.get("/api/geo/channels", params={"at": AT}, headers=headers).json()
    assert {f["properties"]["channel_id"] for f in pts["features"]} == {c["id"] for c in mine["items"]}
    others = client.get("/api/objects", params={"at": AT}, headers=auth("admin")).json()["items"][0]["children"]
    foreign = next(o["id"] for o in others if o["id"] != complex_id)
    assert client.get("/api/geo/collectors", params={"object_id": foreign}, headers=headers).status_code == 404
    assert client.get("/api/geo/channels", params={"object_id": foreign}, headers=headers).status_code == 404
