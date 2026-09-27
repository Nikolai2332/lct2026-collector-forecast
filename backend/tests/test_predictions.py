"""Главный контракт с фронтендом: GET /api/predictions?at=… по примеру из ТЗ фронтенда."""

AT = "2026-08-01T12:00:00"


def test_predictions_shape_matches_frontend_example(client, auth):
    r = client.get("/api/predictions", params={"at": AT}, headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"at", "total", "items"}
    assert body["at"] == AT
    assert body["total"] == 60  # все датчики small-сида

    first = body["items"][0]
    assert set(first) == {
        "prediction_id", "channel", "object", "prob", "health", "risk_level",
        "factors", "factors_human", "outcome", "decision", "work_order_id",
        "kind_probs", "likely_kind", "likely_kind_label",  # v3, добавляющие поля
    }
    assert set(first["channel"]) == {"id", "name", "sensor_type", "system_type"}
    assert set(first["object"]) == {"id", "name", "path"}
    assert set(first["outcome"]) == {"happened", "fault_at"}

    # Сид воспроизводит пример из ТЗ буквально
    assert first["channel"] == {
        "id": 334609, "name": "Дым ПК 1101+2", "sensor_type": "Датчик дыма", "system_type": "Пожарная охрана",
    }
    assert first["object"] == {
        "id": 20, "name": "объект Фита", "path": ["Район по эксплуатации", "объект Бета", "объект Фита"],
    }
    assert first["prob"] == 0.87
    assert first["health"] == 13
    assert first["risk_level"] == "critical"
    assert first["factors"] == [
        "Сообщений о неисправности за сутки: 6 (обычно 0)",
        "Смен статуса за сутки: 40",
        "Отказов в этом объекте за 7 дней: 3",
    ]
    # Для диспетчера — те же причины понятным языком, техническая фраза сохранена рядом
    assert [f["text"] for f in first["factors_human"]] == [
        "6 сообщений «Неисправен» за сутки (обычно 0)",
        "Статус менялся 40 раз за сутки (обычно 2) — дребезг",
        "3 отказа у других датчиков этого объекта за 7 дней",
    ]
    assert first["factors_human"][0]["tech"] == ["Сообщений о неисправности за сутки: 6 (обычно 0)"]
    assert all(len(f["short"]) <= 60 for f in first["factors_human"])
    assert first["outcome"] == {"happened": True, "fault_at": "2026-08-01T18:42:10"}
    assert first["decision"] is None
    assert first["work_order_id"] is None


def test_predictions_sorted_by_risk_desc(client, auth):
    items = client.get("/api/predictions", params={"at": AT, "limit": 500}, headers=auth()).json()["items"]
    probs = [i["prob"] for i in items]
    assert probs == sorted(probs, reverse=True)


def test_time_machine_uses_latest_snapshot_before_at(client, auth):
    # 14:59 → срез 12:00 того же дня
    a = client.get("/api/predictions", params={"at": "2026-08-01T14:59:00", "limit": 1}, headers=auth()).json()
    b = client.get("/api/predictions", params={"at": AT, "limit": 1}, headers=auth()).json()
    assert a["items"][0]["prediction_id"] == b["items"][0]["prediction_id"]


def test_time_machine_accepts_timezone(client, auth):
    # 09:00Z = 12:00 МСК
    r = client.get("/api/predictions", params={"at": "2026-08-01T09:00:00Z", "limit": 1}, headers=auth())
    assert r.json()["at"] == AT


def test_before_any_data_is_empty(client, auth):
    body = client.get("/api/predictions", params={"at": "2020-01-01T00:00:00"}, headers=auth()).json()
    assert body == {"at": "2020-01-01T00:00:00", "total": 0, "items": []}


def test_outcome_unknown_for_last_snapshot(client, auth):
    # Горизонт последнего среза выходит за конец данных — исход ещё неизвестен
    body = client.get("/api/predictions", params={"at": "2026-08-04T18:00:00", "limit": 5}, headers=auth()).json()
    assert body["items"] and all(i["outcome"] is None for i in body["items"])


def test_filters_and_pagination(client, auth):
    crit = client.get("/api/predictions", params={"at": AT, "risk_level": "critical"}, headers=auth()).json()
    assert crit["total"] >= 1 and all(i["risk_level"] == "critical" for i in crit["items"])

    page = client.get("/api/predictions", params={"at": AT, "limit": 10, "offset": 10}, headers=auth()).json()
    assert page["total"] == 60 and len(page["items"]) == 10

    found = client.get("/api/predictions", params={"at": AT, "q": "1101+2"}, headers=auth()).json()
    assert [i["channel"]["id"] for i in found["items"]] == [334609]

    by_object = client.get("/api/predictions", params={"at": AT, "object_id": 3}, headers=auth()).json()
    assert all("объект Бета" in i["object"]["path"] for i in by_object["items"])


def test_bad_at_gives_russian_422(client, auth):
    r = client.get("/api/predictions", params={"at": "вчера"}, headers=auth())
    assert r.status_code == 422
    assert "ISO 8601" in r.json()["detail"][0]["msg"]


def test_prediction_detail(client, auth):
    pid = client.get("/api/predictions", params={"at": AT, "limit": 1}, headers=auth()).json()["items"][0]["prediction_id"]
    r = client.get(f"/api/predictions/{pid}", headers=auth())
    assert r.status_code == 200
    body = r.json()
    assert body["horizon_h"] == 24
    assert body["factors_detail"][0]["feature"] == "fault_msgs_24h"
    assert client.get("/api/predictions/999999999", headers=auth()).status_code == 404
