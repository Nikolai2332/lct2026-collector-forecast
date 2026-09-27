"""Симуляция потока: модельные часы, подмена «сейчас», скрытие будущего, уведомления без спама, роли и аудит,
сброс при старте, SIM_ENABLED=false; разметка исходов по расписанию."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import delete, func, select

from app import sim
from app.config import get_settings
from app.models import AuditLog, Channel, ChannelFault, EventRecent, Prediction, PredictionOutcome, SimState
from app.notifications import broker
from app.scheduler.outcomes import label_outcomes

DAY = "2026-08-01"
T0 = datetime(2026, 9, 25, 12, 0, 0)  # «реальное» время запуска в тестах


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock(monkeypatch, session_factory):
    c = Clock()
    monkeypatch.setattr(sim, "real_now", c)
    yield c
    with session_factory() as db:
        sim.stop(db, "manual")
        db.commit()
        sim.load(db)


@pytest.fixture
def published(monkeypatch):
    items, events = [], []
    monkeypatch.setattr(broker, "publish", items.append)
    # Уведомления симуляции собираются по областям подписчиков; в тесте подписчиков нет — полная область
    monkeypatch.setattr(broker, "publish_scoped", lambda build: (items.extend(got := build(None)), len(got))[1])
    monkeypatch.setattr(broker, "publish_event", lambda event, data: events.append((event, data)))
    return items, events


def _start(client, auth, day=DAY, speed=60, role="engineer"):
    return client.post("/api/sim/start", json={"day": day, "speed": speed}, headers=auth(role))


def test_state_readable_by_any_role(client, auth):
    for role in ("dispatcher", "manager", "engineer", "admin"):
        r = client.get("/api/sim/state", headers=auth(role))
        assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["active"] is False
    # Другие тесты импортируют прогнозы на более поздние даты, поэтому верхняя граница — «не раньше»
    assert body["available_from"] == "2026-07-27" and body["available_to"] >= "2026-08-04"
    assert "онлайн-пересчёт моделью не выполняется" in body["note"]


@pytest.mark.parametrize("role", ["dispatcher", "manager"])
def test_only_engineer_and_admin_control_simulation(client, auth, role, clock):
    assert _start(client, auth, role=role).status_code == 403
    assert client.post("/api/sim/stop", headers=auth(role)).status_code == 403


def test_day_without_predictions_is_422(client, auth, clock):
    r = _start(client, auth, day="2025-01-01")
    assert r.status_code == 422
    assert "нет прогнозов" in r.json()["detail"] and "27.07.2026" in r.json()["detail"]


@pytest.mark.parametrize("speed", [0, 601])
def test_speed_limits(client, auth, speed, clock):
    assert _start(client, auth, speed=speed).status_code == 422


def test_one_simulation_at_a_time_and_audit(client, auth, session_factory, clock):
    r = _start(client, auth)
    assert r.status_code == 200, r.text
    assert r.json()["active"] is True and r.json()["started_by"] == "engineer"
    r2 = _start(client, auth, day="2026-08-02", role="admin")
    assert r2.status_code == 409
    assert client.post("/api/sim/stop", headers=auth("admin")).json()["active"] is False
    # Повторная остановка — без ошибки и без лишней записи в аудит
    assert client.post("/api/sim/stop", headers=auth("admin")).status_code == 200
    with session_factory() as db:
        actions = list(db.scalars(select(AuditLog.action).where(AuditLog.action.like("sim_%")).order_by(AuditLog.id)))
    assert actions[-2:] == ["sim_start", "sim_stop"]


def test_model_clock_replaces_now_but_time_machine_wins(client, auth, clock):
    assert _start(client, auth, speed=60).status_code == 200
    clock.advance(minutes=14, seconds=30)  # ×60 → 14 ч 30 мин модельного времени
    state = client.get("/api/sim/state", headers=auth()).json()
    assert state["model_time"] == "2026-08-01T14:30:00"

    summary = client.get("/api/dashboard/summary", headers=auth()).json()
    assert summary["at"] == "2026-08-01T14:30:00"
    assert summary["snapshot_at"] == "2026-08-01T12:00:00"  # срезы small-сида — каждые 6 ч
    assert summary["accuracy_30d"]["window_to"] == "2026-07-31T14:30:00"
    assert client.get("/api/objects", headers=auth()).json()["snapshot_at"] == "2026-08-01T12:00:00"

    # Явный at («машина времени») важнее симуляции
    explicit = client.get("/api/dashboard/summary", params={"at": "2026-08-03T12:00:00"}, headers=auth()).json()
    assert explicit["at"] == "2026-08-03T12:00:00" and explicit["snapshot_at"] == "2026-08-03T12:00:00"

    # Будущие срезы не видны: самый поздний прогноз в ленте — не позже модельного времени
    feed = client.get("/api/notifications", params={"hours": 168}, headers=auth()).json()
    assert all(n["created_at"] <= "2026-08-01T14:30:00" for n in feed["items"])


def test_future_outcomes_hidden_during_simulation(client, auth, clock):
    assert _start(client, auth).status_code == 200
    clock.advance(minutes=14, seconds=30)
    moment = datetime(2026, 8, 1, 14, 30)

    items = client.get("/api/predictions", params={"limit": 500}, headers=auth()).json()["items"]
    assert items and all(i["outcome"] is None for i in items)  # срез 12:00 закроется только 02.08 12:00
    # Тот же момент через «машину времени» — исходы видны (так было и до симуляции)
    explicit = client.get("/api/predictions", params={"at": "2026-08-01T14:30:00", "limit": 500}, headers=auth()).json()
    assert any(i["outcome"] is not None for i in explicit["items"])

    detail = client.get(f"/api/predictions/{items[0]['prediction_id']}", headers=auth()).json()
    assert detail["outcome"] is None

    journal = client.get("/api/journal", params={"limit": 500, "only_alerts": False}, headers=auth()).json()["items"]
    for it in journal:
        closed = datetime.fromisoformat(it["at"]) + timedelta(hours=24) <= moment
        assert closed or it["outcome"] is None, it
    assert any(it["outcome"] is not None for it in journal)
    happened = client.get("/api/journal", params={"outcome": "happened", "limit": 500, "only_alerts": False},
                          headers=auth()).json()["items"]
    assert all(datetime.fromisoformat(it["at"]) + timedelta(hours=24) <= moment for it in happened)
    unknown = client.get("/api/journal", params={"outcome": "unknown", "limit": 500, "only_alerts": False},
                         headers=auth()).json()
    assert any(it["at"] == "2026-08-01T12:00:00" for it in unknown["items"])


def test_new_critical_published_without_spam(client, auth, session_factory, clock, published, monkeypatch):
    items, events = published
    monkeypatch.setattr(get_settings(), "sim_max_notifications_per_slice", 2)
    assert _start(client, auth, speed=600).status_code == 200
    assert events[-1][0] == "sim_clock" and events[-1][1]["active"] is True

    total_new = 0
    for hour in (6, 12, 18):
        before = len(items)
        clock.now = T0 + timedelta(hours=hour) / 600
        with session_factory() as db:
            snap = datetime(2026, 8, 1, hour)
            new_ids = sim.new_critical_ids(db, snap)
            # «Новый» — не был критичным ни в одном срезе за 24 ч до этого (не только в предыдущем)
            prev_critical = set(db.scalars(select(Prediction.channel_id).where(
                Prediction.at >= snap - timedelta(hours=24), Prediction.at < snap,
                Prediction.risk_level == "critical")))
            sim.tick(db)
            sim.tick(db)  # повторный такт без смены среза ничего не рассылает
        got = items[before:]
        expected = min(len(new_ids), 2) + (1 if len(new_ids) > 2 else 0)
        assert len(got) == expected, (hour, len(new_ids))
        for n in got:
            assert n.prediction.risk_level == "critical"
            assert n.prediction.channel.id not in prev_critical  # только новые критические
        assert [n.kind for n in got[:2]] == ["critical_prediction"] * min(len(new_ids), 2)
        if len(new_ids) > 2:
            assert got[-1].kind == "critical_summary" and got[-1].count == len(new_ids) - 2
            assert got[-1].title.startswith(f"Ещё {len(new_ids) - 2} ") and got[-1].title.endswith("в «Критично»")
        total_new += len(new_ids)
    assert total_new > 0
    assert sum(1 for e, _ in events if e == "sim_clock") >= 4  # старт + три новых среза
    state = client.get("/api/sim/state", headers=auth()).json()
    assert state["critical_sent"] == len(items)


def test_simulation_stops_itself_at_day_end(client, auth, session_factory, clock, published):
    assert _start(client, auth, speed=600).status_code == 200
    clock.advance(minutes=3)  # 30 модельных часов
    with session_factory() as db:
        view = sim.tick(db)
    assert view.active is False and view.stop_reason == "finished"
    assert view.model_time() == datetime(2026, 8, 1, 23, 59, 59)
    assert sim.model_now() is None
    # «Сейчас» снова реальное время: исходы прошлых срезов видны
    r = client.get("/api/predictions", params={"at": "2026-08-01T12:00:00"}, headers=auth()).json()
    assert any(i["outcome"] is not None for i in r["items"])


def test_ingest_during_simulation_counts_events(client, auth, session_factory, clock):
    assert _start(client, auth).status_code == 200
    with session_factory() as db:
        ev = db.scalars(select(EventRecent).where(EventRecent.ts >= datetime(2026, 8, 1)).limit(3)).all()
    body = {"events": [{"channel_id": e.channel_id, "ts": e.ts.isoformat(), "value": e.value_raw} for e in ev]}
    r = client.post("/api/ingest/events", json=body, headers=auth("engineer")).json()
    assert r["duplicates"] == 3 and r["accepted"] == 0  # события уже в базе — не дублируются
    state = client.get("/api/sim/state", headers=auth()).json()
    assert state["events_received"] == 3 and state["events_accepted"] == 0


def test_state_reset_on_startup(session_factory, clock):
    with session_factory() as db:
        row = db.get(SimState, 1)
        row.active, row.day, row.speed = True, datetime(2026, 8, 1).date(), 60
        row.model_start, row.started_at = datetime(2026, 8, 1), T0
        db.commit()
        sim.load(db)
        assert sim.model_now() is not None
        sim.reset_on_startup(db)
        row = db.get(SimState, 1)
        assert row.active is False and row.stop_reason == "restart"
    assert sim.model_now() is None


def test_sim_disabled(client, auth, monkeypatch, clock):
    monkeypatch.setattr(get_settings(), "sim_enabled", False)
    r = _start(client, auth)
    assert r.status_code == 403 and "SIM_ENABLED" in r.json()["detail"]
    assert client.get("/api/sim/state", headers=auth()).json()["enabled"] is False


def test_sim_disabled_by_default_in_production(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "sim_enabled", None)
    monkeypatch.setattr(s, "app_env", "production")
    assert s.sim_on is False
    monkeypatch.setattr(s, "app_env", "demo")
    assert s.sim_on is True
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "sim_enabled", True)
    assert s.sim_on is True


# ---------- Разметка исходов по расписанию ----------


def test_label_outcomes_is_idempotent_and_honest(session_factory):
    at = datetime(2026, 8, 3, 10, 0)
    with session_factory() as db:
        channels = list(db.scalars(select(Channel.id).order_by(Channel.id)))
        faulty = channels[0]
        # Канал без отказов в горизонте — для исхода «не случилось»
        quiet = next(
            c for c in channels[1:]
            if not db.scalar(select(func.count()).select_from(EventRecent).where(
                EventRecent.channel_id == c, EventRecent.ts > at, EventRecent.ts <= at + timedelta(hours=24),
                EventRecent.value_text.in_(["Неисправен", "Отключено устройство"])))
            and not db.scalar(select(func.count()).select_from(ChannelFault).where(
                ChannelFault.channel_id == c, ChannelFault.ts > at, ChannelFault.ts <= at + timedelta(hours=24)))
        )
        outcomes_before = db.scalar(select(func.count()).select_from(PredictionOutcome))
        common = {"horizon_h": 24, "prob": 0.5, "health": 50, "risk_level": "risk", "top_factors": [],
                  "model_version": "test"}
        p_fault = Prediction(channel_id=faulty, at=at, **common)
        p_quiet = Prediction(channel_id=quiet, at=at, **common)
        # Горизонт выходит за конец данных (04.08 23:59) — исход неизвестен, размечать нельзя
        p_open = Prediction(channel_id=faulty, at=datetime(2026, 8, 4, 10, 0), **common)
        fault = ChannelFault(channel_id=faulty, ts=at + timedelta(seconds=1), kind="Пропадание связи")
        db.add_all([p_fault, p_quiet, p_open, fault])
        db.commit()
        try:
            assert label_outcomes(db, now=datetime(2026, 9, 25)) == 2
            got = {o.prediction_id: o for o in db.scalars(select(PredictionOutcome).where(
                PredictionOutcome.prediction_id.in_([p_fault.id, p_quiet.id, p_open.id])))}
            assert got[p_fault.id].happened is True
            assert got[p_fault.id].fault_at == at + timedelta(seconds=1)
            assert got[p_fault.id].fault_kind == "Пропадание связи"
            assert got[p_quiet.id].happened is False and got[p_quiet.id].fault_at is None
            assert p_open.id not in got
            # Повторный запуск ничего не меняет; уже размеченные прогнозы сида не тронуты
            assert label_outcomes(db, now=datetime(2026, 9, 25)) == 0
            assert db.scalar(select(func.count()).select_from(PredictionOutcome)) == outcomes_before + 2
            # По реальному времени горизонт ещё не закрыт — тоже не размечаем
            assert label_outcomes(db, now=at + timedelta(hours=2)) == 0
        finally:
            ids = [p_fault.id, p_quiet.id, p_open.id]
            db.execute(delete(PredictionOutcome).where(PredictionOutcome.prediction_id.in_(ids)))
            db.execute(delete(Prediction).where(Prediction.id.in_(ids)))
            db.execute(delete(ChannelFault).where(ChannelFault.id == fault.id))
            db.commit()


def test_scheduler_job_errors_do_not_propagate(monkeypatch, caplog):
    from app import scheduler

    def boom(_db):
        raise RuntimeError("сбой задания")

    scheduler.run_job("test", boom)  # не бросает
    assert "Задание test завершилось с ошибкой" in caplog.text


def test_scheduler_disabled_starts_nothing():
    from app import scheduler

    s = get_settings()
    assert s.scheduler_enabled is False  # tests/conftest.py
    assert scheduler.start(s) is None


def test_scheduler_registers_jobs(monkeypatch):
    from app import scheduler

    s = get_settings()
    monkeypatch.setattr(s, "scheduler_enabled", True)
    sch = scheduler.start(s)
    try:
        # warm_cache — разовое задание при старте, к моменту проверки может уже выполниться
        assert {j.id for j in sch.get_jobs()} - {"warm_cache"} == {"sim_tick", "label_outcomes"}
    finally:
        scheduler.shutdown()
