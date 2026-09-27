"""Дымовые проверки остальных эндпоинтов на демо-срезе."""

import json
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.models import Prediction

AT = "2026-08-01T12:00:00"


def test_dashboard_summary(client, auth):
    body = client.get("/api/dashboard/summary", params={"at": AT}, headers=auth()).json()
    assert body["snapshot_at"] == AT
    assert body["channels_total"] >= 60
    assert sum(body["risk_distribution"].values()) == 60
    assert body["channels_at_risk"] == body["risk_distribution"]["risk"] + body["risk_distribution"]["critical"]
    assert body["accuracy_30d"]["window_to"] == "2026-07-31T12:00:00"


def test_object_tree(client, auth):
    body = client.get("/api/objects", params={"at": AT}, headers=auth()).json()
    assert len(body["items"]) == 1
    district = body["items"][0]
    assert district["kind"] == "district"
    assert len(district["children"]) == 16
    assert sum(len(c["children"]) for c in district["children"]) == 78
    assert district["max_risk_level"] == "critical"
    beta = next(c for c in district["children"] if c["name"] == "объект Бета")
    assert beta["max_risk_level"] == "critical"  # внутри — объект Фита с датчиком из примера


def test_object_detail(client, auth):
    body = client.get("/api/objects/20", params={"at": AT}, headers=auth()).json()
    assert body["path"] == ["Район по эксплуатации", "объект Бета", "объект Фита"]
    assert body["channels"]["items"][0]["id"] == 334609
    assert client.get("/api/objects/12345", headers=auth()).status_code == 404


def test_channels_list_and_card(client, auth):
    body = client.get("/api/channels", params={"at": AT, "sensor_type": "Датчик дыма"}, headers=auth()).json()
    assert body["total"] >= 1 and all(c["sensor_type"] == "Датчик дыма" for c in body["items"])

    card = client.get("/api/channels/334609", params={"at": AT}, headers=auth()).json()
    assert card["prediction"]["prob"] == 0.87
    assert card["tag"] == "847-1.1.131.2"
    assert card["last_event"] is not None
    assert client.get("/api/channels/1", headers=auth()).status_code == 404


def test_channel_history(client, auth):
    day = client.get("/api/channels/334609/history", params={"at": AT, "days": 5}, headers=auth()).json()
    assert day["granularity"] == "day" and 1 <= len(day["points"]) <= 5
    assert day["predictions"]

    hour = client.get("/api/channels/334609/history", params={"at": AT, "days": 2, "granularity": "hour"},
                      headers=auth()).json()
    assert len(hour["points"]) == 48  # 2 суток по часам, граница не удваивается

    after = client.get("/api/channels/334609/history", params={"at": "2026-08-02T00:00:00", "days": 1},
                       headers=auth()).json()
    assert any(f["ts"] == "2026-08-01T18:42:10" for f in after["faults"])

    r = client.get("/api/channels/334609/history", params={"days": 60, "granularity": "hour"}, headers=auth())
    assert r.status_code == 422


def test_journal_filters(client, auth):
    body = client.get("/api/journal", params={"at": AT, "risk_level": "critical"}, headers=auth()).json()
    assert body["total"] >= 1
    assert all(i["risk_level"] == "critical" for i in body["items"])
    ats = [i["at"] for i in body["items"]]
    assert ats == sorted(ats, reverse=True)

    happened = client.get("/api/journal", params={"at": AT, "outcome": "happened"}, headers=auth()).json()
    assert all(i["outcome"]["happened"] for i in happened["items"])


def test_model_screens(client, auth):
    metrics = client.get("/api/model/metrics", params={"at": "2026-08-04T00:00:00"}, headers=auth()).json()
    assert metrics["model_version"] == "lgbm-2026.09-demo"
    assert metrics["overall"]["precision"] is not None
    assert [p["k"] for p in metrics["precision_at_k"]] == [20, 50, 100]
    assert metrics["daily"] and {"date", "predicted", "actual", "true_positive"} == set(metrics["daily"][0])

    thresholds = client.get("/api/model/thresholds", headers=auth()).json()
    assert len(thresholds["items"]) == 19
    assert thresholds["selected_threshold"] in [t["threshold"] for t in thresholds["items"]]


def test_dictionaries_and_reasons(client, auth):
    d = client.get("/api/dictionaries", headers=auth()).json()
    assert [r["code"] for r in d["risk_levels"]] == ["normal", "attention", "risk", "critical"]
    assert d["sensor_types"]
    reasons = client.get("/api/reasons", headers=auth()).json()
    assert len(reasons) == 14
    recs = client.get("/api/recommendations", params={"sensor_type": "Насос"}, headers=auth()).json()
    assert recs[0]["sensor_type"] == "Насос"


def test_notifications_feed(client, auth):
    body = client.get("/api/notifications", params={"at": AT}, headers=auth()).json()
    assert body["total"] >= 1
    first = body["items"][0]
    assert first["kind"] == "critical_prediction"
    assert first["prediction"]["risk_level"] == "critical"
    assert sum(g["count"] for g in body["groups"]) == body["total"]


def test_notifications_feed_only_new_critical(client, auth, session_factory):
    """Лента — только переходы в «Критично»: канал не был критичным 24 ч до среза и не повторяется."""
    at = datetime(2026, 8, 3, 12)
    body = client.get("/api/notifications", params={"at": at.isoformat(), "hours": 48, "limit": 500},
                      headers=auth()).json()
    with session_factory() as db:
        all_critical = db.scalar(select(func.count()).where(
            Prediction.risk_level == "critical", Prediction.at > at - timedelta(hours=48), Prediction.at <= at))
        channels = [n["prediction"]["channel"]["id"] for n in body["items"]]
        assert len(channels) == len(set(channels)) == body["total"]  # один канал — один раз
        assert 0 < body["total"] < all_critical
        for n in body["items"]:
            snap = datetime.fromisoformat(n["created_at"])
            before = db.scalars(select(Prediction.channel_id).where(
                Prediction.risk_level == "critical", Prediction.at >= snap - timedelta(hours=24), Prediction.at < snap))
            assert n["prediction"]["channel"]["id"] not in set(before)

def test_sse_stream_sends_snapshot_and_live_events(session_factory):
    """Генератор потока проверяем напрямую: TestClient не умеет обрывать бесконечный поток."""
    import asyncio
    from datetime import datetime

    from app.api.notifications import stream
    from app.models import Prediction, User
    from app.notifications import broker, notifications_for
    from app.timeutil import resolve_at

    class FakeRequest:
        """Клиент «отключается» при второй проверке — после одного живого события."""

        def __init__(self):
            self.calls = 0

        async def is_disconnected(self):
            self.calls += 1
            return self.calls > 1

    async def run():
        broker.bind_loop(asyncio.get_running_loop())
        with session_factory() as s:
            user = s.query(User).filter_by(username="dispatcher").one()
            pred = s.query(Prediction).filter_by(channel_id=334609, at=datetime.fromisoformat(AT)).one()
            live = notifications_for(s, [pred])[0]
        response = await stream(FakeRequest(), at=resolve_at(datetime.fromisoformat(AT)), _user_id=user.id)
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk)
            if chunk.startswith("event: snapshot"):
                broker.publish(live)
        return chunks

    chunks = asyncio.run(run())
    snapshot = next(c for c in chunks if c.startswith("event: snapshot"))
    data = json.loads(snapshot.split("data: ", 1)[1])
    assert data and data[0]["prediction"]["channel"]["id"] == 334609

    live = next(c for c in chunks if c.startswith("event: critical_prediction"))
    assert json.loads(live.split("data: ", 1)[1])["prediction"]["prob"] == 0.87
    assert broker.subscribers == 0  # подписка снята после отключения клиента


def test_hourly_history_has_exactly_720_points(client, auth):
    first = client.get("/api/predictions", params={"at": AT, "limit": 1}, headers=auth()).json()["items"][0]
    body = client.get(f"/api/channels/{first['channel']['id']}/history",
                      params={"at": AT, "granularity": "hour", "days": 30}, headers=auth()).json()
    assert len(body["points"]) == 720
    assert body["points"][-1]["t"] == AT


def test_default_periods_count_from_last_snapshot(client, auth):
    """«Сейчас» далеко после конца данных: журнал и график качества по умолчанию считаются от последнего среза."""
    late = "2027-01-01T00:00:00"
    journal = client.get("/api/journal", params={"at": late}, headers=auth()).json()
    assert journal["total"] > 0
    metrics = client.get("/api/model/metrics", params={"at": late}, headers=auth()).json()
    assert metrics["date_to"] < "2026-09-01" and metrics["daily"]


def test_model_screen_v3_blocks(client, auth, session_factory):
    """v3: Recall по видам, группы заказчика, сравнение с v2 на той же метке и пояснение про метку."""
    from app.models import ModelMetric

    with session_factory() as s:
        version = client.get("/api/model/metrics", headers=auth()).json()["model_version"]
        rows = [
            ModelMetric(model_version=version, scope="fault_kind", key="Сбой значения", recall=0.57, support=81),
            ModelMetric(model_version=version, scope="fault_kind", key="Пропадание связи", recall=0.75, support=19950),
            ModelMetric(model_version=version, scope="sensor_group", key="охранные", precision=0.75, recall=0.8,
                        f1=0.77, pr_auc=0.77, support=7000, value=40.0),
            ModelMetric(model_version=version, scope="sensor_group", key="газовые", precision=0.64, recall=0.33,
                        f1=0.43, pr_auc=0.49, support=1972, value=6.0),
            ModelMetric(model_version=version, scope="compare", key="v2@b", precision=0.46, recall=0.63, f1=0.54, pr_auc=0.5),
            ModelMetric(model_version=version, scope="compare", key="v3@b", precision=0.7, recall=0.57, f1=0.63, pr_auc=0.62),
            ModelMetric(model_version=version, scope="compare", key="v2@v2", precision=0.62, recall=0.7, f1=0.66, pr_auc=0.68),
        ]
        s.add_all(rows)
        s.commit()
        try:
            m = client.get("/api/model/metrics", headers=auth()).json()
            assert [k["kind"] for k in m["recall_by_kind"]] == ["Пропадание связи", "Сбой значения"]
            assert [g["group"] for g in m["by_group"]] == ["газовые", "охранные"]
            assert m["by_group"][0]["alerts_per_day"] == 6.0
            assert [(c["model"], c["label"]) for c in m["comparison"]] == [("v2", "b"), ("v3", "b"), ("v2", "v2")]
            assert "плановые работы" in m["label_note"] and "23.09" in m["label_note"]
        finally:
            for r in rows:
                s.delete(r)
            s.commit()
