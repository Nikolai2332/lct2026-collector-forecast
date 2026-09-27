"""Человекочитаемые причины прогноза: вклад SHAP → «признак, значение, норма, фраза» на русском."""

import math

import numpy as np
import pandas as pd

# признак → (подпись, формат значения)
LABELS: dict[str, tuple[str, str]] = {
    "fault_msgs_1h": ("Сообщений о неисправности за час", "int"),
    "fault_msgs_6h": ("Сообщений о неисправности за 6 ч", "int"),
    "fault_msgs_24h": ("Сообщений о неисправности за сутки", "int"),
    "fault_msgs_7d": ("Сообщений о неисправности за 7 дней", "int"),
    "fault_msgs_30d": ("Сообщений о неисправности за 30 дней", "int"),
    "fault_msgs_90d": ("Сообщений о неисправности за 90 дней", "int"),
    "disc_msgs_24h": ("Сообщений «Отключено устройство» за сутки", "int"),
    "disc_msgs_7d": ("Сообщений «Отключено устройство» за 7 дней", "int"),
    "disc_msgs_30d": ("Сообщений «Отключено устройство» за 30 дней", "int"),
    "disc_msgs_90d": ("Сообщений «Отключено устройство» за 90 дней", "int"),
    "faults_24h": ("Отказов за сутки", "int"),
    "faults_7d": ("Отказов за 7 дней", "int"),
    "faults_30d": ("Отказов за 30 дней", "int"),
    "faults_90d": ("Отказов за 90 дней", "int"),
    "link_losses_24h": ("Пропаданий связи за сутки", "int"),
    "link_losses_7d": ("Пропаданий связи за 7 дней", "int"),
    "link_losses_30d": ("Пропаданий связи за 30 дней", "int"),
    "link_losses_90d": ("Пропаданий связи за 90 дней", "int"),
    "days_since_fault": ("Дней с последнего отказа", "float1"),
    "ev_1h": ("Сообщений за час", "int"),
    "ev_6h": ("Сообщений за 6 ч", "int"),
    "ev_24h": ("Сообщений за сутки", "int"),
    "ev_7d": ("Сообщений за 7 дней", "int"),
    "ev_30d": ("Сообщений за 30 дней", "int"),
    "ev_90d": ("Сообщений за 90 дней", "int"),
    "ev_ratio_1h": ("Активность за час к норме канала", "x"),
    "ev_ratio_6h": ("Активность за 6 ч к норме канала", "x"),
    "ev_ratio_24h": ("Активность за сутки к норме канала", "x"),
    "ev_ratio_7d": ("Активность за 7 дней к норме канала", "x"),
    "hours_since_event": ("Часов без сообщений", "float1"),
    "gap_to_norm": ("Текущая пауза к личной норме", "x"),
    "norm_gap_h": ("Личная норма паузы, ч", "float1"),
    "norm_last_h": ("Личная норма паузы на день последнего сообщения, ч", "float1"),
    "hours_to_norm": ("Часов до превышения личной нормы тишины", "float1"),
    "max_gap_7d_h": ("Самая долгая пауза за 7 дней, ч", "float1"),
    "max_gap_30d_h": ("Самая долгая пауза за 30 дней, ч", "float1"),
    "status_changes_24h": ("Смен статуса за сутки", "int"),
    "status_changes_7d": ("Смен статуса за 7 дней", "int"),
    "status_changes_30d": ("Смен статуса за 30 дней", "int"),
    "status_changes_90d": ("Смен статуса за 90 дней", "int"),
    "unc_share_24h": ("Доля «Неопределен» за сутки", "pct"),
    "unc_share_7d": ("Доля «Неопределен» за 7 дней", "pct"),
    "unc_share_30d": ("Доля «Неопределен» за 30 дней", "pct"),
    "unc_share_90d": ("Доля «Неопределен» за 90 дней", "pct"),
    "off_share_24h": ("Доля «Обесточен» за сутки", "pct"),
    "off_share_7d": ("Доля «Обесточен» за 7 дней", "pct"),
    "off_share_30d": ("Доля «Обесточен» за 30 дней", "pct"),
    "off_share_90d": ("Доля «Обесточен» за 90 дней", "pct"),
    "alarm_share_24h": ("Доля тревожных сообщений за сутки", "pct"),
    "alarm_share_7d": ("Доля тревожных сообщений за 7 дней", "pct"),
    "alarm_share_30d": ("Доля тревожных сообщений за 30 дней", "pct"),
    "alarm_share_90d": ("Доля тревожных сообщений за 90 дней", "pct"),
    "txt_24h": ("Статусных сообщений за сутки", "int"),
    "txt_7d": ("Статусных сообщений за 7 дней", "int"),
    "txt_30d": ("Статусных сообщений за 30 дней", "int"),
    "txt_90d": ("Статусных сообщений за 90 дней", "int"),
    "num_24h": ("Показаний за сутки", "int"),
    "num_7d": ("Показаний за 7 дней", "int"),
    "num_30d": ("Показаний за 30 дней", "int"),
    "v_mean_24h": ("Среднее показание за сутки", "float2"),
    "v_mean_7d": ("Среднее показание за 7 дней", "float2"),
    "v_mean_30d": ("Среднее показание за 30 дней", "float2"),
    "v_std_24h": ("Разброс показаний за сутки", "float2"),
    "v_std_7d": ("Разброс показаний за 7 дней", "float2"),
    "v_std_30d": ("Разброс показаний за 30 дней", "float2"),
    "stuck_share_24h": ("Доля одинаковых показаний подряд за сутки", "pct"),
    "stuck_share_7d": ("Доля одинаковых показаний подряд за 7 дней", "pct"),
    "stuck_share_30d": ("Доля одинаковых показаний подряд за 30 дней", "pct"),
    "obj_faults_24h": ("Отказов у других датчиков объекта за сутки", "int"),
    "obj_faults_7d": ("Отказов у других датчиков объекта за 7 дней", "int"),
    "parent_faults_24h": ("Отказов в родительском объекте за сутки", "int"),
    "parent_faults_7d": ("Отказов в родительском объекте за 7 дней", "int"),
    "obj_phase_off_share_24h": ("Доля «Обесточен» у фаз объекта за сутки", "pct"),
    "obj_phase_off_share_7d": ("Доля «Обесточен» у фаз объекта за 7 дней", "pct"),
    "nb_dev_ev_ratio_24h": ("Отклонение активности от однотипных соседей", "signed"),
    "nb_dev_v_mean_24h": ("Отклонение показаний от однотипных соседей", "signed"),
    "nb_dev_unc_share_24h": ("Отклонение доли «Неопределен» от соседей", "signed_pct"),
    "nb_same_type": ("Однотипных датчиков в объекте", "int"),
    "sensor_type": ("Тип датчика", "cat"),
    "system_type": ("Инженерная система", "cat"),
    "parent_id": ("Объект", "cat"),
    "tag_depth": ("Глубина тега", "int"),
    "tag_l2": ("Уровень тега 2", "int"),
    "tag_l3": ("Уровень тега 3", "int"),
    "has_pk": ("Датчик на пикете (ПК)", "bool"),
    "has_ans": ("Датчик насосной (АНС)", "bool"),
    "has_shield": ("Датчик щита или шкафа", "bool"),
    "has_vsh": ("Датчик вентшахты (ВШ)", "bool"),
    "cal_hour": ("Час суток", "int"),
    "cal_dow": ("День недели", "int"),
    "cal_month": ("Месяц", "int"),
    "cal_weekend": ("Выходной день", "bool"),
    # v3
    "sentinel_24h": ("Служебных значений (коды производителя) за сутки", "int"),
    "sentinel_7d": ("Служебных значений (коды производителя) за 7 дней", "int"),
    "sentinel_30d": ("Служебных значений (коды производителя) за 30 дней", "int"),
    "sentinel_share_7d": ("Доля служебных значений за 7 дней", "pct"),
    "date1970_24h": ("Значений «01.01.1970» за сутки", "int"),
    "date1970_7d": ("Значений «01.01.1970» за 7 дней", "int"),
    "date1970_30d": ("Значений «01.01.1970» за 30 дней", "int"),
    "date_current_7d": ("Текущая дата вместо значения за 7 дней", "int"),
    "gas1_work_24h": ("Газ ≥ 1 % в рабочее время будней за сутки", "int"),
    "gas1_work_7d": ("Газ ≥ 1 % в рабочее время будней за 7 дней", "int"),
    "gas1_offhours_24h": ("Газ ≥ 1 % вне рабочего времени за сутки", "int"),
    "gas1_offhours_7d": ("Газ ≥ 1 % вне рабочего времени за 7 дней", "int"),
    "gas5_7d": ("Газ ≥ 5 % за 7 дней", "int"),
    "gas5_30d": ("Газ ≥ 5 % за 30 дней", "int"),
    "gas_detected_24h": ("Сообщений «Обнаружен газ» за сутки", "int"),
    "gas_detected_7d": ("Сообщений «Обнаружен газ» за 7 дней", "int"),
    "fault_episodes_30d": ("Эпизодов «Неисправен» за 30 дней", "int"),
    "fault_episodes_90d": ("Эпизодов «Неисправен» за 90 дней", "int"),
    "disc_episodes_30d": ("Эпизодов «Отключено устройство» за 30 дней", "int"),
    "disc_episodes_90d": ("Эпизодов «Отключено устройство» за 90 дней", "int"),
    "value_failures_30d": ("Сбоев значения за 30 дней", "int"),
    "value_failures_90d": ("Сбоев значения за 90 дней", "int"),
    "group_silences_30d": ("Групповых отключений объекта с участием датчика за 30 дней", "int"),
    "group_silences_90d": ("Групповых отключений объекта с участием датчика за 90 дней", "int"),
    "recoveries_90d": ("Восстановлений связи за 90 дней", "int"),
    "recovery_h_90d": ("Среднее время восстановления связи за 90 дней, ч", "float1"),
    "type_fault_rate_24h": ("Отказов на датчик этого типа по парку за сутки", "float2"),
    "type_fault_rate_7d": ("Отказов на датчик этого типа по парку за 7 дней", "float2"),
    "type_fault_msg_rate_7d": ("«Неисправен» на датчик этого типа по парку за 7 дней", "float2"),
    "type_fault_trend_7d_90d": ("Отказы типа за неделю к среднему за 90 дней", "x"),
    "zero_days_30d": ("Дней без сообщений за 30 дней", "int"),
    "daily_cv_30d": ("Разброс числа сообщений по дням", "float2"),
    "history_days": ("Дней истории канала", "int"),
    "nb_silent_share": ("Доля однотипных соседей, молчащих дольше нормы", "pct"),
}
SENSOR_TYPE_IN_FACTOR = ("sentinel_",)  # для фразы «вероятен сбой термодатчика» в бэкенде


def fmt(v, kind: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "нет данных"
    if kind == "int":
        return f"{int(round(float(v))):,}".replace(",", " ")
    if kind == "float1":
        return f"{float(v):.1f}".replace(".", ",")
    if kind == "float2":
        return f"{float(v):.2f}".replace(".", ",")
    if kind == "x":
        return f"{float(v):.1f}×".replace(".", ",")
    if kind == "pct":
        return f"{float(v) * 100:.0f} %"
    if kind == "signed":
        return f"{float(v):+.1f}".replace(".", ",")
    if kind == "signed_pct":
        return f"{float(v) * 100:+.0f} п.п."
    if kind == "bool":
        return "да" if v else "нет"
    return str(v)


def to_json_value(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return round(float(v), 4)
    return str(v) if not isinstance(v, (int, bool)) else v


def top_factors(X: pd.DataFrame, contrib: np.ndarray, feats: list[str], norms: pd.DataFrame, k: int = 3) -> list[list[dict]]:
    """contrib: SHAP-вклады (n × (m+1), последний столбец — базовое значение). Берём k признаков с наибольшим
    положительным вкладом (что толкает риск вверх); если таких меньше k — добираем по модулю."""
    c = contrib[:, :-1]
    order_pos = np.argsort(-c, axis=1)[:, :k]
    order_abs = np.argsort(-np.abs(c), axis=1)[:, :k]
    st = X["sensor_type"].astype(str).values
    out = []
    cols = {f: X[f].values for f in feats}
    norm_lookup = {s: norms.loc[s].to_dict() for s in norms.index}
    for i in range(len(X)):
        idx = [j for j in order_pos[i] if c[i, j] > 0]
        for j in order_abs[i]:
            if len(idx) >= k:
                break
            if j not in idx:
                idx.append(j)
        items = []
        for j in idx:
            f = feats[j]
            label, kind = LABELS.get(f, (f, "float2"))
            v = cols[f][i]
            nv = norm_lookup.get(st[i], {}).get(f)
            phrase = f"{label}: {fmt(v, kind)}"
            if kind != "cat" and nv is not None and not (isinstance(nv, float) and math.isnan(nv)):
                phrase += f" (обычно {fmt(nv, kind)})"
            item = {"feature": f, "value": to_json_value(v), "norm": to_json_value(nv), "phrase": phrase,
                    "shap": round(float(c[i, j]), 4)}
            if f.startswith(SENSOR_TYPE_IN_FACTOR):
                item["sensor_type"] = st[i]
            items.append(item)
        out.append(items)
    return out
