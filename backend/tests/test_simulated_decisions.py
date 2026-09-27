"""Смоделированный журнал решений: правдоподобие, повторяемость, пометка source=simulation, выгрузка для дообучения."""

import csv
import io
import sys
from collections import Counter

import pytest
from sqlalchemy import delete, select

from app.models import Decision, PredictionOutcome
from scripts import simulate_decisions

PERIOD = ["--from", "2026-07-27", "--to", "2026-08-04"]


@pytest.fixture
def simulated(session_factory, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["simulate_decisions", *PERIOD])
    assert simulate_decisions.main() == 0
    yield
    with session_factory() as s:
        s.execute(delete(Decision).where(Decision.source == "simulation"))
        s.commit()


def _sim(session_factory):
    with session_factory() as s:
        return s.execute(
            select(Decision.id, Decision.decision_type, PredictionOutcome.happened, Decision.comment)
            .outerjoin(PredictionOutcome, PredictionOutcome.prediction_id == Decision.prediction_id)
            .where(Decision.source == "simulation")
        ).all()


def test_decision_model_is_plausible_not_uniform():
    """Модель решения: сбывшиеся — чаще выезд (и чаще при высокой вероятности), несбывшиеся — чаще «ложное» или
    «наблюдение», но с шумом: диспетчер не знает будущего. Не равномерный случайный выбор."""
    import random

    rng = random.Random(1)
    hit = Counter(simulate_decisions.decide(rng, True, 0.9) for _ in range(10_000))
    hit_low = Counter(simulate_decisions.decide(rng, True, 0.4) for _ in range(10_000))
    miss = Counter(simulate_decisions.decide(rng, False, 0.6) for _ in range(10_000))
    assert hit["dispatch"] / 10_000 > 0.8 > hit_low["dispatch"] / 10_000 > 0.6
    assert 0.8 < (miss["false_alarm"] + miss["monitor"]) / 10_000 < 0.95 and miss["dispatch"] > 0
    assert simulate_decisions.reason_code(rng, "false_alarm", "Газовый датчик",
                                          __import__("datetime").datetime(2026, 6, 23, 10), None) == "planned_works"
    assert simulate_decisions.reason_code(rng, "dispatch", "Датчик дыма",
                                          __import__("datetime").datetime(2026, 6, 23, 10), "link") == "link_loss"


def test_simulation_is_marked_and_repeatable(simulated, session_factory, monkeypatch):
    rows = _sim(session_factory)
    assert rows  # маленький тестовый сид: несколько новых переходов в «Критично»
    assert all("Смоделировано" in r.comment for r in rows)
    by = Counter((r.decision_type, r.happened) for r in rows)
    # Повторный запуск за тот же период: прежние смоделированные удаляются, результат тот же
    monkeypatch.setattr(sys, "argv", ["simulate_decisions", *PERIOD])
    assert simulate_decisions.main() == 0
    again = _sim(session_factory)
    assert Counter((r.decision_type, r.happened) for r in again) == by


def test_simulated_badge_in_api_and_export(simulated, client, auth, session_factory):
    with session_factory() as s:
        pid = s.scalar(select(Decision.prediction_id).where(Decision.source == "simulation").limit(1))
    detail = client.get(f"/api/predictions/{pid}", params={"at": "2026-08-31T00:00:00"}, headers=auth("admin")).json()
    assert detail["decisions"][0]["source"] == "simulation"
    r = client.get("/api/export/decisions", headers=auth("admin"), params={
        "date_from": "2026-07-27T00:00:00", "date_to": "2026-08-04T23:59:59", "at": "2026-08-31T00:00:00",
        "source": "simulation"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert rows and {x["source"] for x in rows} == {"simulation"} and rows[0]["outcome_happened"] in ("0", "1", "")
    assert len(rows) == len(_sim(session_factory))
    # Техник — только свой комплекс
    tech = client.post("/api/auth/login", json={"username": "technician", "password": "technician123"}).json()
    mine = {c["id"] for c in client.get("/api/channels", params={"limit": 500}, headers={
        "Authorization": f"Bearer {tech['access_token']}"}).json()["items"]}
    t = client.get("/api/export/decisions", headers={"Authorization": f"Bearer {tech['access_token']}"}, params={
        "date_from": "2026-07-27T00:00:00", "date_to": "2026-08-04T23:59:59", "at": "2026-08-31T00:00:00"})
    assert {int(x["channel_id"]) for x in csv.DictReader(io.StringIO(t.content.decode("utf-8-sig")), delimiter=";")} <= mine
