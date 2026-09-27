"""Кэш тяжёлых агрегатов: повторный запрос не пересчитывает, изменение данных — пересчитывает."""

from datetime import timedelta

from app import cache
from app.api import dashboard

AT = "2026-08-01T12:00:00"


def test_summary_accuracy_cached_and_invalidated(client, auth, monkeypatch):
    calls = []
    original = dashboard.accuracy

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(dashboard, "accuracy", counting)
    first = client.get("/api/dashboard/summary", params={"at": AT}, headers=auth()).json()
    second = client.get("/api/dashboard/summary", params={"at": AT}, headers=auth()).json()
    assert first == second and len(calls) == 1

    cache.bump()  # импорт, разметка исходов
    client.get("/api/dashboard/summary", params={"at": AT}, headers=auth())
    assert len(calls) == 2

    client.get("/api/dashboard/summary", params={"at": "2026-08-02T12:00:00"}, headers=auth())
    assert len(calls) == 3  # другой момент — другой ключ


def test_metrics_daily_cached(client, auth, monkeypatch):
    from app.api import model

    calls = []
    original = model._daily_facts
    monkeypatch.setattr(model, "_daily_facts", lambda *a: calls.append(1) or original(*a))
    params = {"at": AT, "date_from": "2026-07-10", "date_to": "2026-07-31"}
    a = client.get("/api/model/metrics", params=params, headers=auth()).json()
    b = client.get("/api/model/metrics", params=params, headers=auth()).json()
    assert a["daily"] == b["daily"] and a["daily"] and len(calls) == 1


def test_warm_cache_prepares_default_moment(client, auth, session_factory, monkeypatch):
    """Прогрев при старте: «Последние данные» (последний срез − 24 ч) открываются без пересчёта."""
    from sqlalchemy import func, select

    from app.models import Prediction
    from app.scheduler import warm_cache

    with session_factory() as db:
        warm_cache(db)
        snap = db.scalar(select(func.max(Prediction.at)))
    at = (snap.replace(minute=0, second=0) - timedelta(hours=24)).isoformat()
    monkeypatch.setattr(dashboard, "accuracy", lambda *a: (_ for _ in ()).throw(AssertionError("пересчёт")))
    assert client.get("/api/dashboard/summary", params={"at": at}, headers=auth()).status_code == 200
