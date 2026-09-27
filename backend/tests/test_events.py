"""Журнал тревожных событий: правила контекста, фильтры, область видимости, выгрузка XLSX."""

import io
from datetime import datetime, timedelta

from openpyxl import load_workbook
from sqlalchemy import delete, select

from app import event_context
from app.models import AuditLog, Channel, ChannelFault, EventRecent, Object

AT = "2026-08-01T12:00:00"


def _ev(ts, value_raw, value_num=None, value_text=None, alarm=True, channel_id=1, eid=1):
    return EventRecent(id=eid, channel_id=channel_id, ts=ts, is_alarm=alarm, value_raw=value_raw,
                       value_num=value_num, value_text=value_text)


GAS = Channel(id=1, object_id=1, system_type="Газовая охрана", sensor_type="Газовый датчик", tag="t", name="ГАЗ Д1 ПК5")
TEMP = Channel(id=2, object_id=1, system_type="Температурная подсистема", sensor_type="Датчик температуры", tag="t",
               name="Темп. ПК7")
WEEKDAY_10 = datetime(2026, 6, 23, 10, 58)  # вторник
SATURDAY_10 = datetime(2026, 6, 27, 10, 0)


def codes(e, ch):
    return [t.code for t in event_context.simple_tags(e, ch)]


def test_planned_check_rule():
    assert codes(_ev(WEEKDAY_10, "Обнаружен газ", value_text="Обнаружен газ"), GAS) == ["planned_check"]
    assert codes(_ev(WEEKDAY_10, "1.4", value_num=1.4), GAS) == ["planned_check"]  # ≥ 1 % в будни 9–14
    assert codes(_ev(WEEKDAY_10, "0.4", value_num=0.4), GAS) == []
    assert codes(_ev(SATURDAY_10, "Обнаружен газ", value_text="Обнаружен газ"), GAS) == []
    assert codes(_ev(WEEKDAY_10.replace(hour=14), "Обнаружен газ", value_text="Обнаружен газ"), GAS) == []


def test_value_failure_rule():
    assert codes(_ev(WEEKDAY_10, "-127", value_num=-127.0), TEMP) == ["value_failure"]
    assert codes(_ev(WEEKDAY_10, "01.01.1970 03:00:00"), TEMP) == ["value_failure"]
    assert codes(_ev(SATURDAY_10, "327.68", value_num=327.68), GAS) == ["value_failure"]
    assert codes(_ev(SATURDAY_10, "-0.5", value_num=-0.5), GAS) == ["value_failure"]
    assert codes(_ev(SATURDAY_10, "21.5", value_num=21.5), TEMP) == []
    assert event_context.value_kind(_ev(WEEKDAY_10, "01.01.1970 03:00:00")) == "date"


def test_group_off_rule(session_factory):
    """5 однотипных датчиков подобъекта отключились в пределах 12 ч — «групповое отключение»; 1 из 5 — нет."""
    with session_factory() as s:
        sub = s.scalar(select(Object.id).where(Object.level == 3).order_by(Object.id.desc()).limit(1))
        ids = list(range(990001, 990006))
        s.add_all([Channel(id=i, object_id=sub, system_type="Охранная подсистема", sensor_type="Тестовый тип",
                           tag="T", name=f"КД тест ПК{i % 100}") for i in ids])
        t0 = datetime(2026, 7, 30, 3, 0)
        s.add_all([EventRecent(channel_id=ids[0], ts=t0, is_alarm=True, value_raw="Отключено устройство",
                               value_text="Отключено устройство")])
        s.add_all([ChannelFault(channel_id=i, ts=t0 + timedelta(hours=k), kind="Пропадание связи")
                   for k, i in enumerate(ids[1:], start=1)])
        s.commit()
        try:
            e = s.scalar(select(EventRecent).where(EventRecent.channel_id == ids[0]))
            channels = {c.id: c for c in s.scalars(select(Channel).where(Channel.id.in_(ids)))}
            parent_of = dict(s.execute(select(Object.id, Object.parent_id)).all())
            tags = event_context.annotate(s, [e], channels, parent_of)[e.id]
            assert [t.code for t in tags] == ["group_off"] and "5 из 5" in tags[0].reason
            s.execute(delete(ChannelFault).where(ChannelFault.channel_id.in_(ids[2:])))
            s.commit()
            assert event_context.annotate(s, [e], channels, parent_of)[e.id] == []
        finally:
            s.execute(delete(ChannelFault).where(ChannelFault.channel_id.in_(ids)))
            s.execute(delete(EventRecent).where(EventRecent.channel_id.in_(ids)))
            s.execute(delete(Channel).where(Channel.id.in_(ids)))
            s.commit()


def test_events_list_and_filters(client, auth):
    r = client.get("/api/events", params={"at": AT}, headers=auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] >= len(body["items"]) > 0
    assert all(i["is_alarm"] for i in body["items"])
    first = body["items"][0]
    assert {"ts", "channel", "object", "picket", "value", "value_kind", "value_kind_label", "tags"} <= set(first)
    assert body["date_to"] > body["date_from"]
    everything = client.get("/api/events", params={"at": AT, "only_alarms": False, "kind": "all"}, headers=auth()).json()
    assert everything["total"] > body["total"]
    one = client.get("/api/events", params={"at": AT, "channel_id": first["channel"]["id"], "only_alarms": False},
                     headers=auth()).json()
    assert one["items"] and {i["channel"]["id"] for i in one["items"]} == {first["channel"]["id"]}
    too_long = client.get("/api/events", params={"at": AT, "date_from": "2026-06-01T00:00:00"}, headers=auth())
    assert too_long.status_code == 422


def test_events_scope_and_export(client, auth, session_factory):
    tech = client.post("/api/auth/login", json={"username": "technician", "password": "technician123"}).json()
    headers = {"Authorization": f"Bearer {tech['access_token']}"}
    mine = {c["id"] for c in client.get("/api/channels", params={"at": AT, "limit": 500}, headers=headers).json()["items"]}
    params = {"at": AT, "only_alarms": False, "kind": "all", "limit": 500}
    ev = client.get("/api/events", params=params, headers=headers).json()
    assert ev["items"] and {i["channel"]["id"] for i in ev["items"]} <= mine
    assert ev["total"] < client.get("/api/events", params=params, headers=auth("admin")).json()["total"]
    others = client.get("/api/objects", params={"at": AT}, headers=auth("admin")).json()["items"][0]["children"]
    foreign = next(o["id"] for o in others if o["id"] != tech["user"]["scope"][0]["id"])
    assert client.get("/api/events", params={"object_id": foreign}, headers=headers).status_code == 404
    x = client.get("/api/export/events", params={"at": AT, "only_alarms": False, "kind": "all"}, headers=headers)
    assert x.status_code == 200
    ws = load_workbook(io.BytesIO(x.content)).active
    rows = list(ws.iter_rows(min_row=2, values_only=True))
    ids = {row[1] for row in rows}
    assert ids and ids <= mine
    with session_factory() as s:
        last = s.scalars(select(AuditLog).where(AuditLog.action == "export_events").order_by(AuditLog.id.desc())).first()
        assert last.username == "technician" and last.details["rows"] == len(rows)
