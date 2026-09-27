"""Рекомендации по ТО: каждое правило, обоснование, стабильность, API."""

from collections import Counter
from datetime import datetime

import pytest
import yaml

from app.recommendations.engine import (
    RULES_PATH,
    Context,
    Signals,
    advise,
    infer_fault_kind,
    load_rules,
    parse_rules,
)

AT = datetime(2026, 6, 29, 23, 0)
SILENCE = [
    {"feature": "hours_to_norm", "value": 6.2, "norm": 130.0, "phrase": "Часов до превышения нормы: 6,2"},
    {"feature": "hours_since_event", "value": 130.0, "norm": 20.0, "phrase": "Часов с последнего события: 130"},
    {"feature": "norm_last_h", "value": 136.0, "norm": 60.0, "phrase": "Норма паузы: 136"},
]


def ctx(sensor="КД Дверь", risk="critical", prob=0.95, factors=None, **signals) -> Context:
    s = Signals(**{"object_channels": 50, **signals})
    return Context(prediction_id=1, at=AT, sensor_type=sensor, system_type="Охранная подсистема", risk_level=risk,
                   prob=prob, top_factors=factors if factors is not None else list(SILENCE), signals=s)


@pytest.fixture(scope="module")
def rules():
    return load_rules()


def rule_of(c: Context, rules) -> str:
    return advise(c, rules).rule_id


# ---------- Каждое правило ----------


def test_mass_link_loss(rules):
    a = advise(ctx(neighbors_silent=9, object_channels=60), rules)
    assert a.rule_id == "mass_link_loss" and a.fault_kind == "link"
    assert "у 9 соседних датчиков объекта (из 59) тоже пропадает связь" in a.reason
    assert "Датчик молчит 5 сут 10 ч" in a.reason
    assert a.reason.endswith("вероятна проблема линии связи или шкафа объекта, а не датчика.")
    assert a.priority == "critical" and a.per_object
    assert any("одной заявки на объект" in x for x in a.avoid)


def test_mass_needs_share_not_only_count(rules):
    # 6 молчащих из 242 — фон (≈2,5 %), а не массовый отказ: правило для одного датчика
    a = advise(ctx(neighbors_silent=6, object_channels=243), rules)
    assert a.rule_id == "single_link"
    assert "обычный фон" in a.reason


def test_single_link(rules):
    a = advise(ctx(neighbors_silent=0, object_channels=12), rules)
    assert a.rule_id == "single_link"
    assert "признаков потери связи нет" in a.reason
    assert "питание и линию связи датчика" in a.action


def test_power_object_from_phases(rules):
    a = advise(ctx(object_power_off=3, neighbors_silent=10, object_channels=40), rules)
    assert a.fault_kind == "power" and a.rule_id == "power_object"
    assert a.reason.startswith("У 3 датчиков фаз питания объекта за сутки появилось «Обесточен»")
    assert a.priority == "critical"


def test_power_own_message_on_phase_sensor(rules):
    c = ctx(sensor="Состояние фазы", risk="attention", prob=0.3, factors=[], power_off_24h=2)
    a = advise(c, rules)
    assert (a.fault_kind, a.rule_id) == ("power", "power_object")
    assert a.priority == "high"  # не ниже «высокого»


def test_repeat_after_repair(rules):
    c = ctx(risk="risk", prob=0.6, last_repair=("ЗН-2026-000007", datetime(2026, 6, 25, 10), 2),
            faults_30d=Counter({"Пропадание связи": 2}), faults_90d=Counter({"Пропадание связи": 2}))
    a = advise(c, rules)
    assert a.rule_id == "repeat_after_repair"
    assert a.reason.startswith("По заявке ЗН-2026-000007 работы выполнены 25.06.2026, после этого отказов — 2")
    assert a.priority == "critical"  # «риск» → высокий, и на ступень выше


def test_chronic_fault(rules):
    c = ctx(sensor="Датчик дыма", risk="risk", prob=0.6, factors=[], fault_msgs_24h=4,
            faults_30d=Counter({"Неисправен": 3}), faults_90d=Counter({"Неисправен": 5}))
    a = advise(c, rules)
    assert (a.fault_kind, a.rule_id) == ("fault", "chronic_fault")
    assert a.reason.startswith("За сутки 4 сообщения «Неисправен» / «Отключено устройство»")
    assert "отказов за 30 дней — 3, за 90 дней — 5" in a.reason
    assert a.plan


def test_chronic_link(rules):
    c = ctx(risk="risk", prob=0.6, faults_30d=Counter({"Пропадание связи": 1}), faults_90d=Counter({"Пропадание связи": 4}))
    assert rule_of(c, rules) == "chronic_link"


def test_verify_remote_first_for_low_precision_type(rules):
    a = advise(ctx(sensor="Датчик дыма", risk="risk", prob=0.6, type_precision=0.26), rules)
    assert a.rule_id == "verify_remote_first"
    assert "Precision" in a.reason and "0,26" in a.reason
    assert a.due_hours == 4 and a.priority == "medium"
    assert any("без удалённой проверки" in x for x in a.avoid)


def test_fault_diagnostics_flapping(rules):
    f = [{"feature": "status_changes_24h", "value": 40, "norm": 2, "phrase": "Смен статуса за сутки: 40"}]
    c = ctx(sensor="Датчик дыма", risk="critical", prob=0.87, factors=f)
    kind, basis = infer_fault_kind(c, rules)
    assert (kind, basis) == ("fault", "дребезг статуса")
    a = advise(c, rules)
    assert a.rule_id == "fault_diagnostics"
    assert "дребезг" in a.reason


def test_gas_calibration(rules):
    f = [{"feature": "stuck_share_24h", "value": 0.9, "norm": 0.15, "phrase": "Одинаковых подряд: 90%"}]
    a = advise(ctx(sensor="Газовый датчик", risk="risk", prob=0.6, factors=f), rules)
    assert (a.fault_kind, a.rule_id) == ("drift", "gas_calibration")
    assert "поверочной смесью" in a.action
    assert "«застыли»" in a.reason


def test_temperature_check(rules):
    f = [{"feature": "stuck_share_24h", "value": 80, "norm": 15, "phrase": "Одинаковых подряд: 80%"}]  # демо — проценты
    assert rule_of(ctx(sensor="Датчик температуры", risk="risk", prob=0.6, factors=f), rules) == "temperature_check"


def test_drift_check_other_numeric(rules):
    f = [{"feature": "neighbor_deviation", "value": 3.1, "norm": 1.0, "phrase": "Отклонение от соседей: 3,1σ"}]
    assert rule_of(ctx(sensor="Датчик уровня воды", risk="risk", prob=0.6, factors=f), rules) == "drift_check"


@pytest.mark.parametrize("sensor,rule", [("Состояние насоса", "pump_unit"), ("Вентилятор", "fan_unit"), ("ИБП", "ups_check")])
def test_units(rules, sensor, rule):
    f = [{"feature": "starts_24h", "value": 30, "norm": 6, "phrase": "Пусков за сутки: 30"}]
    assert rule_of(ctx(sensor=sensor, risk="risk", prob=0.6, factors=f), rules) == rule


def test_watch_and_routine(rules):
    a = advise(ctx(sensor="Датчик движения", risk="attention", prob=0.3, factors=[]), rules)
    assert (a.rule_id, a.work_order, a.priority) == ("watch", False, "low")
    b = advise(ctx(sensor="Датчик движения", risk="normal", prob=0.02, factors=[]), rules)
    assert (b.rule_id, b.work_order) == ("routine", False)
    assert b.reason.startswith("Вероятность отказа в ближайшие 24 ч — 2 %")


def test_every_rule_is_reachable_by_tests():
    """Защита от правила без теста: при добавлении правила в rules.yaml добавьте сюда пример."""
    covered = {"mass_link_loss", "single_link", "power_object", "repeat_after_repair", "chronic_fault", "chronic_link",
               "verify_remote_first", "fault_diagnostics", "gas_calibration", "temperature_check", "drift_check",
               "pump_unit", "fan_unit", "ups_check", "watch", "routine", "group_planned_works", "value_failure"}
    assert {r.id for r in load_rules().rules} == covered


# ---------- Модификаторы, вид отказа, обоснование ----------


def test_modifiers_open_order_and_recent_visit(rules):
    a = advise(ctx(open_order="ЗН-2026-000011 (черновик)", recent_visit="ЗН-2026-000009"), rules)
    assert any("уже открыта ЗН-2026-000011 (черновик)" in x for x in a.avoid)
    assert any("Не выезжать повторно: заявка ЗН-2026-000009" in x for x in a.avoid)
    assert a.has_open_order


def test_fault_kind_from_model_probabilities_first(rules):
    c = ctx()
    c.kind_probs = {"link": 0.2, "fault": 0.7, "power": 0.1}
    kind, basis = infer_fault_kind(c, rules)
    assert kind == "fault" and "по оценке модели (70 % вероятности отказа)" in basis


def test_fault_kind_from_history_when_no_factors(rules):
    c = ctx(risk="normal", prob=0.05, factors=[], faults_90d=Counter({"Неисправен": 4, "Пропадание связи": 1}))
    assert infer_fault_kind(c, rules)[0] == "fault"


def test_90d_fault_counter_is_not_current_fault(rules):
    # «25 сообщений «Неисправен» за 90 дней» — это история, текущая проблема по тишине — связь
    f = [*SILENCE, {"feature": "fault_msgs_90d", "value": 25, "norm": 0, "phrase": "Сообщений «Неисправен» за 90 дней: 25"}]
    assert infer_fault_kind(ctx(factors=f), rules)[0] == "link"


def test_same_input_same_output(rules):
    make = lambda: ctx(neighbors_silent=9, object_channels=60, faults_90d=Counter({"Пропадание связи": 2}))  # noqa: E731
    first = advise(make(), rules)
    for _ in range(5):
        assert advise(make(), rules) == first


def test_recommendations_are_diverse_on_mixed_inputs(rules):
    inputs = [ctx(neighbors_silent=9, object_channels=60), ctx(neighbors_silent=0), ctx(object_power_off=2),
              ctx(sensor="Датчик дыма", type_precision=0.2), ctx(sensor="Газовый датчик", factors=[
                  {"feature": "stuck_share_24h", "value": 0.9, "norm": 0.1, "phrase": ""}])]
    advices = [advise(c, rules) for c in inputs]
    assert len({a.rule_id for a in advices}) == len(inputs)
    assert len({a.reason for a in advices}) == len(inputs)


# ---------- Файл правил ----------


def test_rules_file_is_valid_and_marked_as_draft():
    r = load_rules()
    assert "требует согласования" in r.source
    assert not r.rules[-1].when  # последнее правило срабатывает всегда
    assert "РТЭК" not in RULES_PATH.read_text(encoding="utf-8").split("rules:")[1]  # пункты регламентов не цитируются


@pytest.mark.parametrize("patch,msg", [
    ({"when": {"bogus": 1}}, "неизвестные условия"),
    ({"facts": ["nope"]}, "неизвестные факты"),
    ({"priority": "urgent"}, "приоритет"),
    ({"when": {"group": ["nosuch"]}}, "неизвестная группа"),
])
def test_rules_file_errors_are_loud(patch, msg):
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    data["rules"][0].update(patch)
    with pytest.raises(ValueError, match=msg):
        parse_rules(data)


def test_last_rule_must_be_catch_all():
    data = yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
    data["rules"] = data["rules"][:-1]
    with pytest.raises(ValueError, match="без условий"):
        parse_rules(data)


# ---------- API ----------

DEMO_AT = "2026-08-01T12:00:00"


def test_recommendation_endpoint(client, auth):
    top = client.get("/api/predictions", params={"at": DEMO_AT, "limit": 1}, headers=auth()).json()["items"][0]
    r = client.get(f"/api/predictions/{top['prediction_id']}/recommendation", headers=auth("manager"))
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["channel_id"] == top["channel"]["id"] and a["action"] and a["reason"] and a["facts"]
    assert a["priority"] in ("low", "medium", "high", "critical")
    assert "требует согласования" in a["source"]
    assert a["recommendation_id"] is not None
    # Стабильно между запросами
    assert client.get(f"/api/predictions/{top['prediction_id']}/recommendation", headers=auth()).json() == a
    assert client.get("/api/predictions/999999999/recommendation", headers=auth()).status_code == 404


def test_recommendation_requires_login(client):
    assert client.get("/api/predictions/1/recommendation").status_code == 401
    assert client.get("/api/maintenance/plan").status_code == 401
    assert client.post("/api/maintenance/drafts", json={"prediction_ids": [1]}).status_code == 401


def test_dictionary_has_rule_rows(client, auth):
    recs = client.get("/api/recommendations", params={"sensor_type": "Датчик дыма"}, headers=auth()).json()
    codes = {r["code"] for r in recs}
    assert "rule:mass_link_loss" in codes and "smoke_check" in codes
    assert "rule:routine" not in codes  # правила без заявки неактивны в справочнике


def test_work_order_prefilled_from_recommendation(client, auth):
    top = client.get("/api/predictions", params={"at": DEMO_AT, "limit": 1}, headers=auth()).json()["items"][0]
    advice = client.get(f"/api/predictions/{top['prediction_id']}/recommendation", headers=auth()).json()
    r = client.post("/api/work-orders", params={"at": DEMO_AT}, json={"prediction_id": top["prediction_id"]}, headers=auth())
    assert r.status_code == 201, r.text
    wo = r.json()
    assert wo["priority"] == advice["priority"]
    assert wo["recommendation_text"] == advice["action"]
    assert wo["recommendation"]["id"] == advice["recommendation_id"]
    assert advice["reason"] in wo["description"]
    assert wo["assignee"] == advice["assignee"]
    # Явные значения диспетчера важнее рекомендации
    r2 = client.post("/api/work-orders", params={"at": DEMO_AT},
                     json={"prediction_id": top["prediction_id"], "priority": "low", "recommendation_text": "Своё"}, headers=auth())
    assert (r2.json()["priority"], r2.json()["recommendation_text"]) == ("low", "Своё")
    for w in (wo, r2.json()):
        assert client.delete(f"/api/work-orders/{w['id']}", headers=auth()).status_code == 204


def test_journal_has_recommendation(client, auth):
    body = client.get("/api/journal", params={"at": DEMO_AT, "limit": 20}, headers=auth()).json()
    assert body["items"] and all(i["recommendation"]["title"] for i in body["items"])


def test_plan_and_drafts(client, auth, session_factory):
    plan = client.get("/api/maintenance/plan", params={"at": DEMO_AT}, headers=auth("manager")).json()
    assert plan["total_items"] >= 1 and plan["groups"]
    items = [it for g in plan["groups"] for it in g["items"]]
    assert all(it["advice"]["plan"] for it in items)
    pids = [it["prediction_id"] for it in items[:3]]
    # Руководитель не создаёт заявки
    assert client.post("/api/maintenance/drafts", params={"at": DEMO_AT}, json={"prediction_ids": pids},
                       headers=auth("manager")).status_code == 403
    r = client.post("/api/maintenance/drafts", params={"at": DEMO_AT}, json={"prediction_ids": [*pids, 999999999]},
                    headers=auth("engineer"))
    assert r.status_code == 201, r.text
    out = r.json()
    assert len(out["created"]) + len(out["skipped"]) == len(pids) + 1
    assert any(s["reason"] == "Прогноз не найден" for s in out["skipped"])
    # Повторно — уже есть открытые заявки
    again = client.post("/api/maintenance/drafts", params={"at": DEMO_AT}, json={"prediction_ids": pids}, headers=auth("engineer")).json()
    assert not again["created"]
    from app.models import AuditLog
    from sqlalchemy import select

    with session_factory() as s:
        logged = s.scalars(select(AuditLog).where(AuditLog.action == "work_order_create")).all()
        assert sum(1 for a in logged if (a.details or {}).get("source") == "maintenance_plan") >= len(out["created"])
    for w in out["created"]:
        assert client.delete(f"/api/work-orders/{w['id']}", headers=auth("engineer")).status_code == 204
    assert client.post("/api/maintenance/drafts", json={"prediction_ids": []}, headers=auth()).status_code == 422


def test_gas_priority_floor_and_planned_works_note(rules):
    # Заказчик: газовые датчики — первыми; массовая тишина газовых — возможно плановый демонтаж по ППР АКМ
    a = advise(ctx(sensor="Газовый датчик", risk="attention", prob=0.3, neighbors_silent=9, object_channels=60), rules)
    assert a.rule_id == "mass_link_loss" and a.priority == "high"
    assert any("ППР АКМ" in x for x in a.avoid)
    b = advise(ctx(sensor="Газовый датчик", risk="attention", prob=0.3, factors=[], fault_msgs_24h=1), rules)
    assert (b.rule_id, b.priority) == ("fault_diagnostics", "high")
    # Без заявки (наблюдение) приоритет не поднимается
    c = advise(ctx(sensor="Газовый датчик", risk="attention", prob=0.3, factors=[]), rules)
    assert (c.rule_id, c.priority) == ("watch", "low")
    # Не газовый — без пометки про ППР
    d = advise(ctx(neighbors_silent=9, object_channels=60), rules)
    assert not any("ППР" in x for x in d.avoid)


def test_fault_kind_wording_follows_customer_answer(rules):
    # «Неисправен» у заказчика — факт потери связи с устройством, причину выясняет выезд
    a = advise(ctx(sensor="Датчик дыма", risk="risk", prob=0.6, factors=[], fault_msgs_24h=2), rules)
    assert a.fault_kind_label == "«Неисправен» (связь с устройством)"
    assert "причину выясняет выезд" in a.reason


# ---------- v3: вид отказа из kind_probs, сбой значения, групповая тишина ----------


def test_v3_group_silence_is_planned_works_not_per_sensor(rules):
    f = [*SILENCE, {"feature": "nb_silent_share", "value": 0.9, "norm": 0.02, "phrase": "Доля соседей: 90 %"}]
    c = ctx(sensor="Газовый датчик", factors=f)
    c.kind_probs = {"link": 0.9, "fault": 0.05, "disconnected": 0.0, "value": 0.0}
    a = advise(c, rules)
    assert a.rule_id == "group_planned_works" and a.fault_kind == "group"
    assert not a.work_order and any("Не выезжать по каждому датчику" in x for x in a.avoid)
    assert "плановые работы" in a.reason


def test_v3_value_failure_from_kind_probs(rules):
    f = [{"feature": "date1970_7d", "value": 38, "norm": 0, "phrase": "Значений «01.01.1970» за 7 дней: 38"}]
    c = ctx(sensor="Состояние охраны", risk="risk", prob=0.6, factors=f)
    c.kind_probs = {"link": 0.01, "fault": 0.0, "disconnected": 0.0, "value": 0.59}
    a = advise(c, rules)
    assert a.rule_id == "value_failure" and a.fault_kind == "value" and a.fault_kind_label == "Сбой значения"
    assert "«01.01.1970»" in a.reason


def test_v3_disconnected_goes_to_fault_diagnostics(rules):
    c = ctx(risk="risk", prob=0.5, factors=[])
    c.kind_probs = {"link": 0.1, "fault": 0.1, "disconnected": 0.3, "value": 0.0}
    a = advise(c, rules)
    assert a.fault_kind == "disconnected" and a.rule_id == "fault_diagnostics"


def test_v3_small_silent_share_is_not_group(rules):
    f = [*SILENCE, {"feature": "nb_silent_share", "value": 0.1, "norm": 0.02, "phrase": "Доля соседей: 10 %"}]
    assert infer_fault_kind(ctx(factors=f), rules)[0] == "link"


def test_v3_group_from_object_signals(rules):
    """Групповое отключение и без признака среди причин: связь пропадает у половины датчиков объекта."""
    c = ctx(sensor="Газовый датчик", object_channels=500, neighbors_silent=45, same_type_channels=55, same_type_silent=50)
    c.kind_probs = {"link": 0.95, "fault": 0.01, "disconnected": 0.0, "value": 0.0}
    a = advise(c, rules)
    assert a.rule_id == "group_planned_works"
    assert "связь пропадает у 50 из 54 других датчиков того же типа в объекте (93 %)" in a.fault_kind_basis


def test_v3_few_same_type_silent_is_not_group(rules):
    c = ctx(sensor="Газовый датчик", object_channels=500, neighbors_silent=45, same_type_channels=55, same_type_silent=5)
    assert infer_fault_kind(c, rules)[0] == "link"
