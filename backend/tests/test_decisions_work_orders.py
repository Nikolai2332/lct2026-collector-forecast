import re

import pytest

AT = "2026-08-02T06:00:00"


@pytest.fixture(scope="module")
def alert_predictions(client, auth):
    """Несколько прогнозов без решения на одном срезе — каждому тесту свой."""
    items = client.get("/api/predictions", params={"at": AT, "limit": 20}, headers=auth()).json()["items"]
    return [i["prediction_id"] for i in items if i["decision"] is None]


def test_decision_requires_reason(client, auth, alert_predictions):
    pid = alert_predictions[0]
    r = client.post(f"/api/predictions/{pid}/decision", json={"decision_type": "dispatch"}, headers=auth())
    assert r.status_code == 422
    err = r.json()["detail"][0]
    assert err["loc"][-1] == "reason_id"
    assert err["msg"] == "Обязательное поле"


def test_decision_rejects_unknown_or_mismatched_reason(client, auth, alert_predictions):
    pid = alert_predictions[0]
    r = client.post(f"/api/predictions/{pid}/decision", json={"decision_type": "dispatch", "reason_id": 9999},
                    headers=auth())
    assert r.status_code == 422
    assert r.json()["detail"] == "Причина не найдена в справочнике"

    false_alarm_reason = next(x["id"] for x in client.get("/api/reasons", params={"decision_type": "false_alarm"},
                                                          headers=auth()).json() if x["decision_type"] == "false_alarm")
    r = client.post(f"/api/predictions/{pid}/decision", json={"decision_type": "dispatch", "reason_id": false_alarm_reason},
                    headers=auth())
    assert r.status_code == 422


def test_decision_created_and_visible_everywhere(client, auth, alert_predictions):
    pid = alert_predictions[0]
    reason = client.get("/api/reasons", params={"decision_type": "dispatch"}, headers=auth()).json()[0]
    r = client.post(
        f"/api/predictions/{pid}/decision",
        params={"at": AT},
        json={"decision_type": "dispatch", "reason_id": reason["id"], "comment": "Выезд согласован"},
        headers=auth("dispatcher"),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["decision"]["decision_type"] == "dispatch"
    assert body["decision"]["decision_label"] == "Выезд бригады"
    assert body["decision"]["reason"]["id"] == reason["id"]
    assert body["decision"]["user"]["username"] == "dispatcher"

    # В списке прогнозов на тот же момент
    items = client.get("/api/predictions", params={"at": AT, "limit": 50}, headers=auth()).json()["items"]
    assert next(i for i in items if i["prediction_id"] == pid)["decision"]["decision_type"] == "dispatch"

    # В журнале с фильтром по решению
    journal = client.get("/api/journal", params={"at": AT, "decision": "dispatch", "limit": 500},
                         headers=auth()).json()
    assert pid in [i["prediction_id"] for i in journal["items"]]

    # Раньше момента решения его ещё не было
    before = client.get(f"/api/predictions/{pid}", params={"at": "2026-08-02T05:00:00"}, headers=auth()).json()
    assert before["decision"] is None


def test_work_order_lifecycle(client, auth, alert_predictions):
    pid = alert_predictions[1]
    r = client.post("/api/work-orders", params={"at": AT}, json={"prediction_id": pid}, headers=auth("dispatcher"))
    assert r.status_code == 201, r.text
    wo = r.json()
    assert wo["status"] == "draft"
    assert re.fullmatch(r"ЗН-2026-\d{6}", wo["number"])
    assert wo["prediction_id"] == pid
    assert wo["recommendation_text"]  # подставилась рекомендация по типу датчика
    assert wo["description"].startswith("Прогноз отказа на 24 ч")
    assert wo["due_at"] is not None
    wid = wo["id"]

    # Заявка видна у прогноза и в списке
    assert client.get(f"/api/predictions/{pid}", params={"at": AT}, headers=auth()).json()["work_order_id"] == wid
    listed = client.get("/api/work-orders", params={"status": "draft", "at": AT}, headers=auth()).json()
    assert wid in [w["id"] for w in listed["items"]]
    # В журнале — номер заявки, а описание заявки собрано из понятных причин
    journal = client.get("/api/journal", params={"at": AT, "only_alerts": "false", "limit": 500}, headers=auth()).json()
    row = next(i for i in journal["items"] if i["prediction_id"] == pid)
    assert (row["work_order_id"], row["work_order_number"]) == (wid, wo["number"])
    assert "Часов до превышения" not in wo["description"]

    for new_status in ("submitted", "in_progress"):
        r = client.patch(f"/api/work-orders/{wid}", json={"status": new_status}, headers=auth("engineer"))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == new_status

    # Недопустимый переход
    r = client.patch(f"/api/work-orders/{wid}", json={"status": "draft"}, headers=auth("engineer"))
    assert r.status_code == 409
    assert "Нельзя перевести заявку" in r.json()["detail"]

    r = client.patch(f"/api/work-orders/{wid}", json={"status": "done", "assignee": "Бригада №3"}, headers=auth("engineer"))
    assert r.status_code == 200
    assert r.json()["closed_at"] is not None
    assert r.json()["status_label"] == "Выполнена"

    # Закрытую не меняем
    r = client.patch(f"/api/work-orders/{wid}", json={"priority": "low"}, headers=auth("engineer"))
    assert r.status_code == 409


def test_work_order_needs_source(client, auth):
    r = client.post("/api/work-orders", json={"priority": "high"}, headers=auth())
    assert r.status_code == 422
    assert r.json()["detail"][0]["msg"] == "Укажите prediction_id или channel_id"


def test_work_order_by_channel(client, auth):
    r = client.post("/api/work-orders", json={"channel_id": 334609, "priority": "low", "description": "Плановый осмотр"},
                    headers=auth("engineer"))
    assert r.status_code == 201
    assert r.json()["object"]["id"] == 20
    assert r.json()["priority"] == "low"


def test_manager_cannot_create_work_order(client, auth, alert_predictions):
    r = client.post("/api/work-orders", json={"prediction_id": alert_predictions[2]}, headers=auth("manager"))
    assert r.status_code == 403


def test_actions_are_audited(client, auth, session_factory):
    from sqlalchemy import select

    from app.models import AuditLog

    with session_factory() as s:
        actions = set(s.scalars(select(AuditLog.action)))
    assert {"decision_create", "work_order_create", "work_order_update"} <= actions


def test_delete_draft_work_order(client, auth, session_factory):
    r = client.post("/api/work-orders", json={"channel_id": 334609, "priority": "low", "description": "Проверка удаления"},
                    headers=auth("dispatcher"))
    assert r.status_code == 201
    wid = r.json()["id"]
    # чужой черновик не автору и не администратору — нельзя
    assert client.delete(f"/api/work-orders/{wid}", headers=auth("engineer")).status_code == 403
    assert client.delete(f"/api/work-orders/{wid}", headers=auth("manager")).status_code == 403
    assert client.delete(f"/api/work-orders/{wid}", headers=auth("dispatcher")).status_code == 204
    assert client.get(f"/api/work-orders/{wid}", headers=auth()).status_code == 404
    assert client.delete(f"/api/work-orders/{wid}", headers=auth("dispatcher")).status_code == 404

    from sqlalchemy import select

    from app.models import AuditLog

    with session_factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "work_order_delete", AuditLog.entity_id == str(wid))).one()
        assert entry.username == "dispatcher" and entry.details["number"].startswith("ЗН-")


def test_delete_only_drafts(client, auth):
    wid = client.post("/api/work-orders", json={"channel_id": 334609, "priority": "low"}, headers=auth("dispatcher")).json()["id"]
    assert client.patch(f"/api/work-orders/{wid}", json={"status": "submitted"}, headers=auth("dispatcher")).status_code == 200
    r = client.delete(f"/api/work-orders/{wid}", headers=auth("admin"))
    assert r.status_code == 409
    assert "только черновик" in r.json()["detail"]


def test_admin_deletes_any_draft(client, auth):
    wid = client.post("/api/work-orders", json={"channel_id": 334609, "priority": "low"}, headers=auth("engineer")).json()["id"]
    assert client.delete(f"/api/work-orders/{wid}", headers=auth("admin")).status_code == 204


def test_list_survives_draft_deleted_between_queries(client, auth, session_factory):
    """Гонка из нагрузочного теста: черновик удалён между выборкой id и загрузкой строк — пропуск, а не 500."""
    from app.api.work_orders import load_work_orders

    wid = client.post("/api/work-orders", json={"channel_id": 334609, "priority": "low"}, headers=auth()).json()["id"]
    assert client.delete(f"/api/work-orders/{wid}", headers=auth()).status_code == 204
    with session_factory() as s:
        some = client.get("/api/work-orders", headers=auth()).json()["items"][0]["id"]
        assert [w.id for w in load_work_orders(s, [some, wid])] == [some]
