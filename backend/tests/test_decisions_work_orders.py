import os
import re

import pytest

IS_SQLITE = os.environ.get("TEST_DATABASE_URL", "sqlite").startswith("sqlite")
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


def test_work_order_lifecycle(client, auth, free_predictions):
    pid = free_predictions(AT)[1]
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
    # Датчик нужен следующим тестам: открытая заявка по нему может быть только одна
    assert client.delete(f"/api/work-orders/{r.json()['id']}", headers=auth("engineer")).status_code == 204


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
        # В SQLite id удалённых черновиков переиспользуются — берём последнюю запись
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "work_order_delete", AuditLog.entity_id == str(wid))
                          .order_by(AuditLog.id.desc())).first()
        assert entry.username == "dispatcher" and entry.details["number"].startswith("ЗН-")


def test_delete_only_drafts(client, auth):
    wid = client.post("/api/work-orders", json={"channel_id": 334609, "priority": "low"}, headers=auth("dispatcher")).json()["id"]
    assert client.patch(f"/api/work-orders/{wid}", json={"status": "submitted"}, headers=auth("dispatcher")).status_code == 200
    r = client.delete(f"/api/work-orders/{wid}", headers=auth("admin"))
    assert r.status_code == 409
    assert "только черновик" in r.json()["detail"]
    assert client.patch(f"/api/work-orders/{wid}", json={"status": "cancelled"}, headers=auth("dispatcher")).status_code == 200


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


# ---------- Одна открытая заявка на датчик ----------


def _create(client, auth, role="dispatcher", **body):
    return client.post("/api/work-orders", params={"at": AT}, json=body, headers=auth(role))


def test_second_open_work_order_on_channel_is_409(client, auth, free_predictions):
    pid = free_predictions(AT)[2]
    first = _create(client, auth, prediction_id=pid)
    assert first.status_code == 201, first.text
    wo = first.json()
    ch = wo["channel"]["id"]
    # Повтор из прогноза, по датчику напрямую и другим пользователем — 409 с номером открытой заявки
    for role, body in (("dispatcher", {"prediction_id": pid}), ("engineer", {"channel_id": ch, "priority": "low"}),
                       ("admin", {"prediction_id": pid, "priority": "high"})):
        r = _create(client, auth, role, **body)
        assert r.status_code == 409, r.text
        conflict = r.json()
        assert conflict["work_order"] == {"id": wo["id"], "number": wo["number"], "status": "draft", "status_label": "Черновик"}
        assert f"уже есть открытая заявка {wo['number']} (черновик)" in conflict["detail"]
    # «Отправлена» и «в работе» — тоже открытые
    for st in ("submitted", "in_progress"):
        assert client.patch(f"/api/work-orders/{wo['id']}", json={"status": st}, headers=auth()).status_code == 200
        assert _create(client, auth, channel_id=ch).status_code == 409
    # Выполнена — можно новую
    assert client.patch(f"/api/work-orders/{wo['id']}", json={"status": "done"}, headers=auth()).status_code == 200
    again = _create(client, auth, prediction_id=pid)
    assert again.status_code == 201, again.text
    # Отменена — тоже можно
    assert client.patch(f"/api/work-orders/{again.json()['id']}", json={"status": "cancelled"}, headers=auth()).status_code == 200
    third = _create(client, auth, prediction_id=pid)
    assert third.status_code == 201
    assert client.delete(f"/api/work-orders/{third.json()['id']}", headers=auth()).status_code == 204


def test_open_work_order_check_ignores_time_machine(client, auth, free_predictions):
    """Открытая сейчас заявка, созданная «позже» выбранного момента, тоже не даёт завести вторую."""
    pid = free_predictions(AT)[3]
    wo = _create(client, auth, prediction_id=pid).json()
    earlier = client.post("/api/work-orders", params={"at": "2026-07-15T00:00:00"}, json={"prediction_id": pid},
                          headers=auth())
    assert earlier.status_code == 409 and earlier.json()["work_order"]["id"] == wo["id"]
    assert client.delete(f"/api/work-orders/{wo['id']}", headers=auth()).status_code == 204


def test_conflict_does_not_leak_other_scope(client, auth, session_factory):
    """Чужой датчик — по-прежнему 404 без номера заявки, даже если по нему открыта заявка."""
    from sqlalchemy import select

    from app.access import load_access
    from app.models import Channel, User
    from app.services import ObjectIndex

    login = client.post("/api/auth/login", json={"username": "technician", "password": "technician123"}).json()
    tech = {"Authorization": f"Bearer {login['access_token']}"}
    scope_ids = [o["id"] for o in login["user"]["scope"]]
    uid = login["user"]["id"]
    # На время теста технику — ещё и роль диспетчера района в той же области (создавать заявки может только она)
    grant = client.put(f"/api/users/{uid}/access", json={"roles": ["technician", "dispatcher"], "scope_object_ids": scope_ids},
                       headers=auth("admin"))
    assert grant.status_code == 200, grant.text
    try:
        with session_factory() as s:
            user = s.scalar(select(User).where(User.username == "technician"))
            idx = ObjectIndex.load(s, load_access(s, user))
            assert idx.channel_objects is not None
            foreign = s.scalar(select(Channel.id).where(Channel.object_id.not_in(sorted(idx.channel_objects))).limit(1))
            inside = s.scalar(select(Channel.id).where(Channel.object_id.in_(sorted(idx.channel_objects))).limit(1))
        own = _create(client, auth, "admin", channel_id=foreign, priority="low")
        assert own.status_code == 201, own.text
        r = client.post("/api/work-orders", json={"channel_id": foreign, "priority": "low"}, headers=tech)
        assert r.status_code == 404
        assert own.json()["number"] not in r.text and "work_order" not in r.json()
        assert client.delete(f"/api/work-orders/{own.json()['id']}", headers=auth("admin")).status_code == 204
        # В своей области — обычный 409 с номером
        mine = client.post("/api/work-orders", json={"channel_id": inside, "priority": "low"}, headers=tech)
        assert mine.status_code == 201, mine.text
        again = client.post("/api/work-orders", json={"channel_id": inside, "priority": "low"}, headers=tech)
        assert again.status_code == 409 and again.json()["work_order"]["number"] == mine.json()["number"]
        assert client.delete(f"/api/work-orders/{mine.json()['id']}", headers=auth("admin")).status_code == 204
    finally:
        back = client.put(f"/api/users/{uid}/access", json={"roles": ["technician"], "scope_object_ids": scope_ids},
                          headers=auth("admin"))
        assert back.status_code == 200, back.text


@pytest.mark.skipif(IS_SQLITE, reason="в тестах SQLite — одно общее соединение, одновременность проверяется на PostgreSQL")
def test_concurrent_double_click_creates_one_work_order(client, auth, free_predictions, session_factory):
    """Двойной клик: несколько одновременных запросов по одному датчику — одна заявка, остальные 409."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import func, select

    from app.labels import OPEN_WORK_ORDER_STATUSES
    from app.models import WorkOrder

    pid = free_predictions(AT)[5]
    barrier = Barrier(4)

    def post(_):
        barrier.wait()
        return _create(client, auth, prediction_id=pid)

    with ThreadPoolExecutor(4) as ex:
        codes = sorted(r.status_code for r in ex.map(post, range(4)))
    assert codes == [201, 409, 409, 409], codes
    ch = client.get(f"/api/predictions/{pid}", params={"at": AT}, headers=auth()).json()["channel"]["id"]
    with session_factory() as s:
        ids = s.scalars(select(WorkOrder.id).where(WorkOrder.channel_id == ch, WorkOrder.status.in_(OPEN_WORK_ORDER_STATUSES))).all()
        assert len(ids) == 1
    assert client.delete(f"/api/work-orders/{ids[0]}", headers=auth()).status_code == 204


def test_maintenance_drafts_skip_channel_with_open_order(client, auth, free_predictions):
    pid = free_predictions(AT)[4]
    wo = _create(client, auth, prediction_id=pid).json()
    r = client.post("/api/maintenance/drafts", params={"at": AT}, json={"prediction_ids": [pid]}, headers=auth())
    assert r.status_code == 201, r.text
    assert r.json()["created"] == []
    assert "уже открыта заявка" in r.json()["skipped"][0]["reason"]
    assert client.delete(f"/api/work-orders/{wo['id']}", headers=auth()).status_code == 204
