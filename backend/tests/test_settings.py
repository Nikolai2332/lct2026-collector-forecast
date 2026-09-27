"""Настраиваемые параметры: границы уровней риска и лимиты уведомлений без перезапуска."""

from collections import Counter

import pytest
from sqlalchemy import select

from app import cache
from app.models import AuditLog

AT = "2026-08-01T12:00:00"
DEFAULTS = {"risk_attention": 0.2, "risk_risk": 0.5, "risk_critical": 0.8, "notify_cooldown_hours": 24,
            "notify_per_slice": 3, "notify_sim_per_slice": 5}


@pytest.fixture
def restore(client, auth):
    yield
    r = client.put("/api/settings", json=DEFAULTS, headers=auth("admin"))
    assert r.status_code == 200 and r.json()["custom_risk_bounds"] is False


def test_defaults_readable_by_any_role(client, auth):
    for role in ("dispatcher", "manager"):
        body = client.get("/api/settings", headers=auth(role)).json()
        assert body["custom_risk_bounds"] is False
        assert body["defaults"] == DEFAULTS
        assert {k: body[k] for k in DEFAULTS} == DEFAULTS


def test_only_admin_can_change(client, auth):
    assert client.put("/api/settings", json=DEFAULTS).status_code == 401
    for role in ("dispatcher", "engineer", "manager"):
        assert client.put("/api/settings", json=DEFAULTS, headers=auth(role)).status_code == 403


@pytest.mark.parametrize("patch", [
    {"risk_attention": 0.6, "risk_risk": 0.5},
    {"risk_critical": 1.0},
    {"notify_per_slice": 0},
    {"notify_cooldown_hours": 1000},
])
def test_validation(client, auth, patch):
    r = client.put("/api/settings", json={**DEFAULTS, **patch}, headers=auth("admin"))
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list)


def test_risk_bounds_apply_everywhere_without_restart(client, auth, session_factory, restore):
    before = client.get("/api/dashboard/summary", params={"at": AT}, headers=auth()).json()["risk_distribution"]
    generation = cache.data_version.__globals__["_generation"]
    new = {**DEFAULTS, "risk_attention": 0.1, "risk_risk": 0.3, "risk_critical": 0.6}
    r = client.put("/api/settings", json=new, headers=auth("admin"))
    assert r.status_code == 200, r.text
    assert r.json()["custom_risk_bounds"] is True and r.json()["updated_by"] == "admin"
    assert cache.data_version.__globals__["_generation"] > generation  # кэш агрегатов сброшен

    items = client.get("/api/predictions", params={"at": AT, "limit": 500}, headers=auth()).json()["items"]
    for it in items:
        p = it["prob"]
        expected = "critical" if p >= 0.6 else "risk" if p >= 0.3 else "attention" if p >= 0.1 else "normal"
        assert it["risk_level"] == expected, it
    crit = client.get("/api/predictions", params={"at": AT, "risk_level": "critical", "limit": 500}, headers=auth()).json()
    assert crit["total"] == sum(1 for it in items if it["prob"] >= 0.6) and crit["total"] > 0
    after = client.get("/api/dashboard/summary", params={"at": AT}, headers=auth()).json()["risk_distribution"]
    counts = Counter(it["risk_level"] for it in items)
    assert all(after[level] == counts.get(level, 0) for level in after)
    assert after != before and sum(after.values()) == sum(before.values())
    # Схема объектов, журнал и карточка — те же уровни
    tree = client.get("/api/objects", params={"at": AT}, headers=auth()).json()
    root_counts = tree["items"][0]["risk_counts"]
    assert root_counts["critical"] == after["critical"]
    top = items[0]
    card = client.get(f"/api/channels/{top['channel']['id']}", params={"at": AT}, headers=auth()).json()
    assert card["prediction"]["risk_level"] == top["risk_level"]
    journal = client.get("/api/journal", params={"at": AT, "risk_level": "critical", "limit": 500}, headers=auth()).json()
    assert all(i["prob"] >= 0.6 for i in journal["items"])

    with session_factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "settings_update").order_by(AuditLog.id.desc())).first()
        assert entry.username == "admin"
        assert entry.details["changes"]["risk_critical"] == [0.8, 0.6]


def test_notification_limit(client, auth, restore):
    body = client.get("/api/notifications", params={"at": AT}, headers=auth()).json()
    per_slice = Counter(i["created_at"] for i in body["items"] if i["kind"] == "critical_prediction")
    r = client.put("/api/settings", json={**DEFAULTS, "notify_per_slice": 1}, headers=auth("admin"))
    assert r.status_code == 200
    body1 = client.get("/api/notifications", params={"at": AT}, headers=auth()).json()
    per_slice1 = Counter(i["created_at"] for i in body1["items"] if i["kind"] == "critical_prediction")
    assert max(per_slice1.values()) == 1
    if max(per_slice.values()) > 1:
        assert any(i["kind"] == "critical_summary" for i in body1["items"])


def test_reload_is_single_flight(monkeypatch):
    """Под нагрузкой настройки перечитывает один поток, остальные берут последнее значение — без второго
    соединения на запрос (иначе пул соединений кончался: нагрузочный тест, 40 пользователей)."""
    import threading
    import time as time_mod

    from app import runtime_settings as rs

    rs.current()  # есть последнее значение
    monkeypatch.setattr(rs, "_cached", (time_mod.monotonic() - rs.RELOAD_SECONDS - 1, rs.DEFAULTS))  # устарело
    calls, real_load = [], rs.load

    def slow_load(db):
        calls.append(1)
        time_mod.sleep(0.3)
        return real_load(db)

    monkeypatch.setattr(rs, "load", slow_load)
    results = []
    threads = [threading.Thread(target=lambda: results.append(rs.current())) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1 and len(results) == 20
