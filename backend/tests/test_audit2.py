"""Регрессионные тесты находок аудита 2 (27.09.2026, docs/SECURITY_AUDIT.md). Матрица областей видимости —
tests/test_scope_matrix.py, срез — tests/test_expert_slice.py, LDAP — tests/test_ldap.py."""

import csv
import io
from datetime import datetime

from sqlalchemy import select

from app.api import auth as auth_api
from app.config import get_settings
from app.models import Channel, Decision, Prediction

AT = "2026-08-01T12:00:00"


def test_decisions_csv_neutralizes_formulas(client, auth, session_factory):
    """S-30: строки из импорта (тип датчика, система) в CSV решений не становятся формулой Excel."""
    with session_factory() as s:
        pred = s.scalars(select(Prediction).where(Prediction.at <= datetime.fromisoformat(AT)).order_by(Prediction.at.desc())).first()
        ch = s.get(Channel, pred.channel_id)
        old = (ch.sensor_type, ch.system_type)
        ch.sensor_type, ch.system_type = '=HYPERLINK("http://evil","x")', "@SUM(1)"
        s.commit()
        pid, cid = pred.id, ch.id
    try:
        r = client.post(f"/api/predictions/{pid}/decision", headers=auth("dispatcher"), params={"at": AT},
                        json={"decision_type": "monitor", "reason_id": 11})
        assert r.status_code in (200, 201), r.text
        r = client.get("/api/export/decisions", headers=auth("admin"), params={
            "at": AT, "date_from": "2026-07-20T00:00:00", "date_to": AT})
        rows = [x for x in csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";")
                if x["channel_id"] == str(cid)]
        assert rows
        assert all(x["sensor_type"].startswith("'=") and x["system_type"].startswith("'@") for x in rows)
    finally:
        with session_factory() as s:
            s.query(Decision).filter(Decision.prediction_id == pid).delete()
            ch = s.get(Channel, cid)
            ch.sensor_type, ch.system_type = old
            s.commit()


def test_login_failures_are_limited_per_account_across_ips(client, monkeypatch):
    """S-32: перебор пароля одного логина с многих адресов упирается в 429, хотя с каждого адреса — одна попытка."""
    monkeypatch.setattr(get_settings(), "login_max_failures_per_account", 4)
    ips = iter(f"203.0.113.{i}" for i in range(1, 100))
    monkeypatch.setattr(auth_api, "_client_ip", lambda request: next(ips))
    for _ in range(4):
        assert client.post("/api/auth/login", json={"username": "engineer", "password": "wrong"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "engineer", "password": "engineer123"})
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 0
    # другой логин не затронут
    assert client.post("/api/auth/login", json={"username": "manager", "password": "manager123"}).status_code == 200


def test_simulate_stream_accepts_only_http_api_url():
    """bandit B310: адрес API скрипта симуляции — только http(s), не file:// и не ftp://."""
    import pytest

    from scripts.simulate_stream import Api, ApiError

    for bad in ("file:///etc/passwd", "ftp://host/x"):
        with pytest.raises(ApiError):
            Api(bad)
    assert Api("http://localhost:8000/").base == "http://localhost:8000"


def test_dates_at_calendar_edge_are_422_not_500(client, auth):
    """S-34: at=0001-01-01 давал 500 (OverflowError при «минус 30 дней»); даты у края календаря — 422."""
    for at in ("0001-01-01T00:00:00", "9999-12-31T23:59:59"):
        r = client.get("/api/dashboard/summary", headers=auth("dispatcher"), params={"at": at})
        assert r.status_code == 422, (at, r.status_code)
    r = client.get("/api/events", headers=auth("dispatcher"), params={"at": AT, "date_to": "0001-01-01T00:00:00"})
    assert r.status_code == 422
    assert client.get("/api/dashboard/summary", headers=auth("dispatcher"), params={"at": AT}).status_code == 200
    # проба «конца данных» из интерфейса (DataMomentProvider)
    assert client.get("/api/objects", headers=auth("dispatcher"), params={"at": "2100-01-01T00:00:00"}).status_code == 200
