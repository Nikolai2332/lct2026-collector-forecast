"""Обмен XML: приём потока и импорт с теми же проверками, что JSON/CSV; защита от XXE и «миллиарда смешков»."""

import pytest
from defusedxml.ElementTree import fromstring

KNOWN_CHANNEL = 334609
XML = {"Content-Type": "application/xml"}

EVENTS_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<events>
  <event><event_id>91</event_id><channel_id>{KNOWN_CHANNEL}</channel_id><ts>2026-08-05T10:00:00</ts>
         <is_alarm>f</is_alarm><value>Норма</value></event>
  <event channel_id="{KNOWN_CHANNEL}" ts="2026-08-05T10:00:00" is_alarm="false" value="Норма"/>
  <event><channel_id>{KNOWN_CHANNEL}</channel_id><ts>2026-08-05T10:01:00</ts><is_alarm>t</is_alarm>
         <value>01.01.1970 03:00:00</value></event>
  <event><channel_id>1</channel_id><ts>2026-08-05T10:02:00</ts><is_alarm>true</is_alarm><value>0.5</value></event>
</events>""".encode()

XXE = b"""<?xml version="1.0"?>
<!DOCTYPE events [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
<events><event><channel_id>334609</channel_id><ts>2026-08-05T10:00:00</ts><value>&xxe;</value></event></events>"""

LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
 <!ENTITY lol "lol">
 <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
 <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
 <!ENTITY lol9 "&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;&lol3;">
]>
<events><event><channel_id>334609</channel_id><ts>2026-08-05T10:00:00</ts><value>&lol9;</value></event></events>"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE events SYSTEM "http://example.com/evil.dtd">
<events/>"""


def test_ingest_xml_same_result_as_json(client, auth):
    r = client.post("/api/ingest/events", content=EVENTS_XML, headers={**XML, **auth("admin")})
    assert r.status_code == 200, r.text
    assert r.json() == {"received": 4, "accepted": 1, "duplicates": 1, "skipped_unknown_channel": 1,
                        "skipped_invalid": 1, "affected_channels": [KNOWN_CHANNEL]}
    # text/xml тоже принимается; повтор — дубль
    again = client.post("/api/ingest/events", content=EVENTS_XML, headers={"Content-Type": "text/xml; charset=utf-8", **auth("admin")})
    assert again.json()["duplicates"] == 2


@pytest.mark.parametrize("payload", [XXE, LAUGHS, EXTERNAL_DTD], ids=["xxe", "billion_laughs", "external_dtd"])
def test_ingest_rejects_dtd_and_entities(client, auth, payload):
    r = client.post("/api/ingest/events", content=payload, headers={**XML, **auth("admin")})
    assert r.status_code == 422
    assert "DTD" in r.json()["detail"]


@pytest.mark.parametrize("payload", [XXE, LAUGHS], ids=["xxe", "billion_laughs"])
def test_import_rejects_dtd_and_entities(client, auth, payload):
    r = client.post("/api/import", data={"kind": "events"}, files={"file": ("e.xml", payload, "application/xml")},
                    headers=auth("admin"))
    assert r.status_code == 422
    assert "DTD" in r.json()["detail"]


def test_ingest_xml_validation_like_json(client, auth):
    bad = b"<events><event><channel_id>abc</channel_id><ts>2026-08-05T10:00:00</ts><value>1</value></event></events>"
    r = client.post("/api/ingest/events", content=bad, headers={**XML, **auth("admin")})
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"][:3] == ["body", "events", 0]
    assert client.post("/api/ingest/events", content=b"<rows/>", headers={**XML, **auth("admin")}).status_code == 422
    assert client.post("/api/ingest/events", content=b"<events><event>", headers={**XML, **auth("admin")}).status_code == 422
    r = client.post("/api/ingest/events", content=b"a,b", headers={"Content-Type": "text/csv", **auth("admin")})
    assert r.status_code == 415
    # Лимит пачки — тот же, что у JSON
    many = b"<events>" + b"<event><channel_id>1</channel_id><ts>2026-08-05T10:00:00</ts><value>1</value></event>" * 10001 + b"</events>"
    assert client.post("/api/ingest/events", content=many, headers={**XML, **auth("admin")}).status_code == 422


def test_ingest_body_size_limit(client, auth):
    big = b"<events>" + b" " * (10 * 1024 * 1024 + 1) + b"</events>"
    r = client.post("/api/ingest/events", content=big, headers={**XML, **auth("admin")})
    assert r.status_code == 413


def test_ingest_xml_requires_role_before_parsing(client, auth):
    assert client.post("/api/ingest/events", content=XXE, headers=XML).status_code == 401
    assert client.post("/api/ingest/events", content=EVENTS_XML, headers={**XML, **auth("dispatcher")}).status_code == 403


def test_import_events_xml(client, auth):
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rows>
  <row><ид_канала_данных>{KNOWN_CHANNEL}</ид_канала_данных><дата>06.08.2026</дата><время>09:00:00</время>
       <тревожное>f</тревожное><значение_датчика>Норма</значение_датчика></row>
  <row channel_id="{KNOWN_CHANNEL}" ts="2026-08-06T09:05:00" is_alarm="t" value="Неисправен"/>
  <row><channel_id>1</channel_id><ts>2026-08-06T09:06:00</ts><is_alarm>f</is_alarm><value>1</value></row>
</rows>""".encode()
    r = client.post("/api/import", data={"kind": "events"}, files={"file": ("events.xml", body, "application/xml")},
                    headers=auth("admin"))
    assert r.status_code == 200, r.text
    rep = r.json()
    assert (rep["rows_total"], rep["rows_imported"], rep["rows_skipped"]) == (3, 2, 1)


def test_import_xml_that_is_not_xml(client, auth):
    r = client.post("/api/import", data={"kind": "events"}, files={"file": ("e.xml", b"PK\x03\x04zip", "application/xml")},
                    headers=auth("admin"))
    assert r.status_code == 422 and "не XML" in r.json()["detail"]


def test_export_journal_xml(client, auth):
    params = {"at": "2026-08-03T00:00:00", "only_alerts": "false", "date_from": "2026-08-02T00:00:00", "format": "xml"}
    r = client.get("/api/export/journal", params=params, headers=auth("manager"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert 'filename="journal_20260803_0000.xml"' in r.headers["content-disposition"]
    root = fromstring(r.content)
    rows = root.findall("prediction")
    assert root.tag == "journal" and root.get("rows") == str(len(rows)) == "300"
    first = rows[0]
    assert first.get("id") and first.findtext("channel") and first.findtext("risk_level")
    assert first.findtext("recommendation")
    assert client.get("/api/export/journal", params={**params, "format": "pdf"}, headers=auth()).status_code == 422
