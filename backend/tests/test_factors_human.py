"""Причины прогноза языком диспетчера: фразы по видам признаков и склейка похожих признаков."""

import pytest

from app.factors_human import duration, humanize, plural


def f(feature, value, norm=None, phrase=None):
    return {"feature": feature, "value": value, "norm": norm, "phrase": phrase or f"{feature}: {value}"}


def texts(top):
    return [h.text for h in humanize(top)]


@pytest.mark.parametrize(
    ("hours", "expected"),
    [(0.004, "меньше минуты"), (0.42, "25 мин"), (3.67, "3 ч 40 мин"), (2.0, "2 ч"), (14.3, "14 ч"),
     (137.7, "5 сут 18 ч"), (48.2, "2 сут"), (2210, "92 сут")],
)
def test_duration(hours, expected):
    assert duration(hours) == expected


def test_plural():
    assert [plural(n, "датчик", "датчика", "датчиков") for n in (1, 2, 5, 11, 21, 104, 112)] == [
        "датчик", "датчика", "датчиков", "датчиков", "датчик", "датчика", "датчиков"]


def test_silence_merged_into_one_reason():
    """Три признака про одну паузу (как у топа реальных критичных) — один довод, без «обычно 133,0»."""
    top = [
        f("hours_to_norm", 5.7111, 132.99, "Часов до превышения личной нормы тишины: 5,7 (обычно 133,0)"),
        f("gap_to_norm", 0.958, 0.29, "Текущая пауза к личной норме: 1,0× (обычно 0,3×)"),
        f("norm_last_h", 136.0022, 215.22, "Личная норма паузы на день последнего сообщения, ч: 136,0 (обычно 215,2)"),
    ]
    result = humanize(top)
    assert len(result) == 1
    h = result[0]
    assert h.text == ("Датчик молчит 5 сут 10 ч — обычно выходит на связь хотя бы раз в 5 сут 16 ч. "
                      "Через ~6 ч связь будет считаться потерянной")
    assert h.short == "Молчит 5 сут 10 ч, связь потеряется через ~6 ч"
    assert h.features == ["hours_to_norm", "gap_to_norm", "norm_last_h"]
    assert h.tech[0].startswith("Часов до превышения")
    assert "обычно 133" not in h.text


def test_silence_already_lost():
    [h] = humanize([f("hours_since_event", 6.14, 133), f("norm_gap_h", 6.0, 383.8), f("hours_to_norm", -0.14, 130)])
    assert h.text.startswith("Датчик молчит 6 ч 8 мин — обычно выходит на связь хотя бы раз в 6 ч")
    assert "связь считается потерянной" in h.text


def test_silence_only_hours_to_norm():
    assert texts([f("hours_to_norm", 1.7, 126)]) == ["Через ~1 ч 40 мин пауза в связи превысит обычную для этого датчика"]


@pytest.mark.parametrize(
    ("ratio", "expected"),
    [(0.99, "Текущая пауза в связи уже равна обычному максимуму для этого датчика"),
     (1.7, "Текущая пауза в связи в 1,7 раза длиннее обычного максимума для этого датчика"),
     (0.6, "Текущая пауза в связи — 60 % обычного максимума для этого датчика")],
)
def test_gap_to_norm(ratio, expected):
    assert texts([f("gap_to_norm", ratio, 0.3)]) == [expected]


def test_fault_messages():
    assert texts([f("fault_msgs_24h", 6, 0)]) == ["6 сообщений «Неисправен» за сутки (обычно 0)"]
    assert texts([f("fault_msgs_90d", 1, 0)]) == ["1 сообщение «Неисправен» за 90 дней (обычно 0)"]


def test_same_kind_windows_merged():
    top = [f("fault_msgs_24h", 6, 0), f("status_changes_24h", 40, 2), f("fault_msgs_90d", 25, 2)]
    result = humanize(top)
    assert [h.text for h in result] == [
        "6 сообщений «Неисправен» за сутки (обычно 0); 25 — за 90 дней (обычно 2)",
        "Статус менялся 40 раз за сутки (обычно 2) — дребезг",
    ]
    assert result[0].features == ["fault_msgs_24h", "fault_msgs_90d"]


def test_status_changes_without_flapping():
    assert texts([f("status_changes_7d", 3, 2)]) == ["Статус менялся 3 раза за 7 дней (обычно 2)"]


def test_faults_history():
    assert texts([f("faults_90d", 82, 2), f("days_since_fault", 12)]) == [
        "Датчик уже отказывал: 82 отказа за 90 дней (обычно 2). Последний отказ — 12 сут назад"]


def test_activity_and_past_gap():
    assert texts([f("ev_7d", 5370, 2)]) == ["Необычно много сообщений: 5 370 сообщений за 7 дней (обычно 2)"]
    assert texts([f("max_gap_7d_h", 2210.9, 168.4)]) == [
        "За 7 дней датчик вышел на связь после паузы 92 сут (обычно не дольше 7 сут)"]
    assert texts([f("max_gap_30d_h", 30.5, 20)]) == [
        "Самая долгая пауза в связи за 30 дней: 1 сут 6 ч (обычно не дольше 20 ч)"]


def test_shares_and_neighbors():
    assert texts([f("alarm_share_90d", 0.18, 0.0)]) == ["Тревожных сообщений — 18 % за 90 дней (обычно 0 %)"]
    assert texts([f("unc_share_24h", 0.35, 0)]) == ["«Неопределен» — 35 % сообщений за сутки (обычно 0 %)"]
    assert texts([f("obj_faults_7d", 3, 0)]) == ["3 отказа у других датчиков этого объекта за 7 дней"]
    assert texts([f("sensor_type", "Состояние насоса")]) == ["Особенность типа датчика «Состояние насоса»"]


def test_demo_seed_features():
    """Признаки демо-сида (backend/app/seed.py) тоже переводятся, доли в процентах не умножаются."""
    assert texts([f("stuck_share_24h", 80, 15)]) == [
        "Показания «застыли»: 80 % одинаковых подряд за сутки (обычно до 15 %)"]
    assert texts([f("object_faults_7d", 1, 0)]) == ["1 отказ у других датчиков этого объекта за 7 дней"]
    assert texts([f("starts_24h", 34, 6)]) == ["34 пуска за сутки (обычно 6) — частые пуски"]


def test_short_limit_and_fallback():
    long = "Совсем новый признак, которого нет в словаре, с очень длинной технической формулировкой от ML"
    [h] = humanize([{"feature": "brand_new_feature", "value": 1, "norm": None, "phrase": long}])
    assert h.text == long
    assert len(h.short) <= 60 and h.short.endswith("…")
    assert humanize(["Просто фраза"])[0].text == "Просто фраза"
    assert humanize(None) == [] and humanize([]) == []


def test_at_most_three_distinct_reasons():
    top = [f("fault_msgs_24h", 1, 0), f("status_changes_24h", 1, 0), f("faults_90d", 1, 0), f("ev_7d", 1, 1)]
    assert len(humanize(top)) == 3


# ---------- v3: служебные значения, газ, история по журналу, групповая тишина ----------


def test_v3_sentinel_temperature():
    item = f("sentinel_24h", 12, 0) | {"sensor_type": "Датчик температуры"}
    assert texts([item]) == ["За сутки 12 служебных значений «−127» вместо показаний (обычно 0) — вероятен сбой термодатчика"]


def test_v3_sentinel_other_sensor():
    assert texts([f("sentinel_7d", 3)]) == ["За 7 дней 3 служебных значения вместо показаний — вероятен сбой датчика"]


def test_v3_gas_offhours_thrice():
    assert texts([f("gas1_offhours_24h", 3, 0)]) == [
        "Газ выше 1 % трижды за сутки вне рабочего времени (обычно 0) — вне плановых проверок баллонами"]


def test_v3_gas_workhours_is_planned_check():
    assert "плановую проверку баллоном" in texts([f("gas1_work_24h", 2)])[0]


def test_v3_date1970():
    assert texts([f("date1970_7d", 38)])[0].startswith("За 7 дней 38 значений «01.01.1970» вместо показаний")


def test_v3_repairs_merged():
    top = [f("fault_episodes_90d", 4, 0), f("recovery_h_90d", 14.2), f("recoveries_90d", 5)]
    hs = humanize(top)
    assert len(hs) == 1
    assert hs[0].text == ("История по журналу: 4 эпизода «Неисправен» (потеря связи с устройством) за 90 дней; "
                          "связь восстанавливалась 5 раз за 90 дней, в среднем через 14 ч")


def test_v3_group_silence_planned_works():
    t = texts([f("nb_silent_share", 0.85)])[0]
    assert t == "Сейчас молчат 85 % однотипных датчиков объекта — вероятно, плановые работы или отключение объекта"


def test_v3_fleet_and_pulse():
    assert texts([f("type_fault_trend_7d_90d", 1.8)]) == [
        "Отказов у датчиков этого типа по всему парку за неделю в 1,8 раза больше обычного"]
    assert texts([f("history_days", 12)]) == ["Датчик в журнале 12 дней — обычная пауза связи ещё ненадёжна"]


def test_v3_sensor_type_not_in_features():
    item = f("sentinel_24h", 1) | {"sensor_type": "Газовый датчик"}
    h = humanize([item])[0]
    assert h.features == ["sentinel_24h"] and "газового датчика" in h.text
