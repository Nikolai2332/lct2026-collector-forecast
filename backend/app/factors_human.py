"""Причины прогноза языком диспетчера — строятся при выдаче из top_factors ML, таблицы не меняются.

ML (ml/model/factors.py) отдаёт до трёх признаков с наибольшим вкладом SHAP: код признака, значение, «норму»
(медиану признака у датчиков того же типа) и техническую фразу. Здесь по коду признака строится понятная фраза,
а признаки об одном и том же (например, пять признаков про текущую паузу связи) склеиваются в один довод.
Техническая фраза сохраняется в `tech` — для подсказки «Подробнее для аналитика».
"""

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field

SHORT_MAX = 60


@dataclass
class HumanFactor:
    text: str
    short: str
    features: list[str] = field(default_factory=list)
    tech: list[str] = field(default_factory=list)


# ---------- Числа и слова ----------


def plural(n: float, one: str, few: str, many: str) -> str:
    """Форма слова для целого n: 1 датчик, 2 датчика, 5 датчиков."""
    n = abs(int(round(n)))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def num(v: float, digits: int = 0) -> str:
    """Русская запись числа: 1 234,5."""
    if digits == 0:
        s = f"{int(round(v)):,}".replace(",", " ")
    else:
        s = f"{v:,.{digits}f}".replace(",", " ").replace(".", ",")
        s = s.rstrip("0").rstrip(",") if "," in s else s
    return s.replace("-", "−")


def duration(hours: float) -> str:
    """Длительность для человека: 25 мин, 3 ч 40 мин, 14 ч, 5 сут 18 ч, 12 сут."""
    hours = abs(hours)
    minutes = int(round(hours * 60))
    if minutes < 1:
        return "меньше минуты"
    if minutes < 60:
        return f"{minutes} мин"
    if hours < 10:
        h, m = divmod(minutes, 60)
        return f"{h} ч {m} мин" if m else f"{h} ч"
    total_h = int(round(hours))
    if total_h < 24:
        return f"{total_h} ч"
    d, h = divmod(total_h, 24)
    if d >= 10 or h == 0:
        d = int(round(hours / 24))
        return f"{d} сут"
    return f"{d} сут {h} ч"


def approx(hours: float) -> str:
    """Оценка на будущее — грубее: «~15 мин», «~1 ч 40 мин», «~6 ч»."""
    hours = abs(hours)
    if hours < 1:
        return duration(max(5, round(hours * 12) * 5) / 60)
    if hours < 3:
        return duration(round(hours * 6) / 6)
    return duration(round(hours))


def pct(v: float) -> str:
    """Доля: ML отдаёт 0..1, демо-сид — уже проценты (20..95)."""
    p = v * 100 if abs(v) <= 1 else v
    return f"{num(p)} %"


WINDOWS = {"1h": "за час", "6h": "за 6 ч", "24h": "за сутки", "7d": "за 7 дней", "30d": "за 30 дней", "90d": "за 90 дней"}


def window_of(feature: str) -> str:
    m = re.search(r"_(1h|6h|24h|7d|30d|90d)(?:_h)?$", feature)
    return WINDOWS[m.group(1)] if m else ""


def shorten(text: str, limit: int = SHORT_MAX) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip(" ,.;—–-(") + "…"


def _f(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _usual(norm: float | None, render: Callable[[float], str]) -> str:
    return f" (обычно {render(norm)})" if norm is not None else ""


# ---------- Группы признаков ----------

# Все признаки о текущей паузе связи — один довод «датчик молчит»
SILENCE = {"hours_since_event", "gap_to_norm", "norm_gap_h", "norm_last_h", "hours_to_norm"}


def group_of(feature: str) -> str:
    if feature in SILENCE:
        return "silence"
    for prefix, group in (
        # v3: служебные значения, газ, «история ремонтов», групповая тишина, парк, «пульс»
        ("sentinel_", "sentinel"), ("date1970_", "date1970"), ("date_current_", "date_current"),
        ("gas1_offhours_", "gas_offhours"), ("gas1_work_", "gas_work"), ("gas5_", "gas5"),
        ("gas_detected_", "gas_detected"), ("fault_episodes_", "repairs"), ("disc_episodes_", "repairs"),
        ("recoveries_", "repairs"), ("recovery_h_", "repairs"), ("value_failures_", "value_failures"),
        ("group_silences_", "group_silence"), ("nb_silent_share", "group_silence"), ("type_fault_", "fleet"),
        ("zero_days_", "pulse"), ("daily_cv_", "pulse"), ("history_days", "pulse"),
        ("fault_msgs_", "fault_msgs"), ("disc_msgs_", "disc_msgs"), ("faults_", "faults"),
        ("link_losses_", "link_losses"), ("max_gap_", "past_gaps"), ("max_silence", "past_gaps"),
        ("status_changes_", "status_changes"), ("unc_share_", "unc_share"), ("uncertain_share_", "unc_share"),
        ("off_share_", "off_share"), ("power_off_", "power_off"), ("alarm_share_", "alarm_share"),
        ("ev_ratio_", "activity"), ("ev_", "activity"), ("txt_", "activity"), ("num_", "activity"),
        ("v_mean_", "values"), ("v_std_", "values"), ("trend_", "values"), ("stuck_share_", "stuck"),
        ("obj_faults_", "object_faults"), ("object_faults_", "object_faults"), ("parent_faults_", "object_faults"),
        ("obj_phase_off_", "phase_off"), ("nb_dev_", "neighbors"), ("neighbor_", "neighbors"),
        ("starts_", "starts"), ("cal_", "calendar"), ("tag_", "place"), ("has_", "place"),
    ):
        if feature.startswith(prefix):
            return group
    if feature in ("sensor_type", "system_type", "parent_id", "nb_same_type"):
        return "static"
    if feature == "days_since_fault":
        return "faults"
    return f"other:{feature}"


Facts = dict[str, tuple[float | None, float | None, object]]  # признак → (значение, норма, сырое значение)


def _silence(f: Facts) -> tuple[str, str]:
    def v(name):
        return f[name][0] if name in f else None

    silent, norm, left, ratio = v("hours_since_event"), v("norm_last_h"), v("hours_to_norm"), v("gap_to_norm")
    norm_now = v("norm_gap_h")
    if norm is None:
        norm = norm_now
    # Недостающее восстанавливаем из соседних признаков: hours_to_norm = norm_last_h − hours_since_event
    if silent is None and left is not None and v("norm_last_h") is not None:
        silent = v("norm_last_h") - left
    if silent is None and ratio is not None and norm_now is not None:
        silent = ratio * norm_now
    if norm is None and silent is not None and left is not None:
        norm = silent + left
    if left is None and silent is not None and v("norm_last_h") is not None:
        left = v("norm_last_h") - silent

    if silent is not None and norm is not None:
        text = f"Датчик молчит {duration(silent)} — обычно выходит на связь хотя бы раз в {duration(norm)}."
        if left is not None and left > 0:
            text += f" Через ~{approx(left)} связь будет считаться потерянной"
            short = f"Молчит {duration(silent)}, связь потеряется через ~{approx(left)}"
        elif left is not None:
            text += " Обычная пауза уже превышена — связь считается потерянной"
            short = f"Молчит {duration(silent)} — дольше обычного"
        else:
            short = f"Молчит {duration(silent)} (норма — {duration(norm)})"
        return text, short
    if left is not None:
        if left > 0:
            return (f"Через ~{approx(left)} пауза в связи превысит обычную для этого датчика",
                    f"Через ~{approx(left)} связь будет считаться потерянной")
        return (f"Пауза в связи уже на {duration(left)} длиннее обычной для этого датчика — связь считается потерянной",
                f"Пауза в связи длиннее обычной на {duration(left)}")
    if ratio is not None:
        if 0.95 <= ratio <= 1.05:
            text = "Текущая пауза в связи уже равна обычному максимуму для этого датчика"
            return text, "Пауза в связи — на пределе нормы"
        if ratio > 1.05:
            text = f"Текущая пауза в связи в {num(ratio, 1)} раза длиннее обычного максимума для этого датчика"
            return text, f"Пауза в связи в {num(ratio, 1)} раза длиннее нормы"
        text = f"Текущая пауза в связи — {num(ratio * 100)} % обычного максимума для этого датчика"
        return text, f"Пауза в связи — {num(ratio * 100)} % нормы"
    if silent is not None:
        usual = f["hours_since_event"][1]
        text = f"Датчик молчит {duration(silent)}"
        if usual is not None:
            text += f" (у датчиков этого типа обычно {duration(usual)})"
        return text, f"Молчит {duration(silent)}"
    if norm is not None:
        feat = "norm_last_h" if "norm_last_h" in f else "norm_gap_h"
        usual = f[feat][1]
        text = f"Датчик обычно выходит на связь хотя бы раз в {duration(norm)}"
        if usual is not None:
            text += f" (у датчиков этого типа — раз в {duration(usual)})"
        return text, f"Обычно выходит на связь раз в {duration(norm)}"
    return "", ""


def _counts(f: Facts, one: str, few: str, many: str) -> tuple[str, str]:
    """Счётчики по окнам: «6 сообщений «Неисправен» за сутки (обычно 0); 25 — за 90 дней (обычно 2)»."""
    parts = []
    for feat, (value, norm, _) in f.items():
        if value is None:
            continue
        w = window_of(feat)
        if not parts:
            parts.append(f"{num(value)} {plural(value, one, few, many)} {w}".strip() + _usual(norm, num))
        else:
            parts.append(f"{num(value)} — {w}" + _usual(norm, num))
    return ("; ".join(parts), parts[0]) if parts else ("", "")


def _shares(f: Facts, template: str) -> tuple[str, str]:
    """template: «{p}» — доля, «{w}» — окно."""
    feat, (value, norm, _) = next(iter(f.items()))
    if value is None:
        return "", ""
    text = template.format(p=pct(value), w=window_of(feat)).strip() + _usual(norm, pct)
    return text, text


def _faults(f: Facts) -> tuple[str, str]:
    counts = {k: v for k, v in f.items() if k != "days_since_fault"}
    texts, short = [], ""
    if counts:
        t, short = _counts(counts, "отказ", "отказа", "отказов")
        texts.append("Датчик уже отказывал: " + t)
    if "days_since_fault" in f and f["days_since_fault"][0] is not None:
        d = f["days_since_fault"][0]
        last = "Последний отказ — сегодня" if d < 1 else f"Последний отказ — {duration(d * 24)} назад"
        texts.append(last)
        short = short or last
    return ". ".join(texts), short


def _status(f: Facts) -> tuple[str, str]:
    feat, (value, norm, _) = next(iter(f.items()))
    if value is None:
        return "", ""
    w = window_of(feat)
    word = plural(value, "раз", "раза", "раз")
    flap = feat.endswith(("_1h", "_6h", "_24h")) and value >= 10 and value >= 3 * max(norm or 0, 1)
    text = f"Статус менялся {num(value)} {word} {w}".strip() + _usual(norm, num) + (" — дребезг" if flap else "")
    short = f"{num(value)} {plural(value, 'смена', 'смены', 'смен')} статуса {w}".strip() + (" — дребезг" if flap else "")
    return text, short


def _activity(f: Facts) -> tuple[str, str]:
    texts, shorts = [], []
    for feat, (value, norm, _) in f.items():
        if value is None:
            continue
        w = window_of(feat)
        if feat.startswith("ev_ratio_"):
            if value >= 1.5:
                texts.append(f"Сообщений {w} в {num(value, 1)} раза больше обычного для этого датчика")
            elif value <= 0.67:
                texts.append(f"Сообщений {w} заметно меньше обычного: {num(value * 100)} % от нормы датчика")
            else:
                texts.append(f"Активность {w} — {num(value * 100)} % от обычной для этого датчика")
            continue
        noun = {"txt_": ("статусное сообщение", "статусных сообщения", "статусных сообщений"),
                "num_": ("показание", "показания", "показаний")}.get(feat[:4], ("сообщение", "сообщения", "сообщений"))
        head = f"{num(value)} {plural(value, *noun)} {w}".strip() + _usual(norm, num)
        shorts.append(head)
        if norm is not None and norm > 0 and value >= 3 * norm:
            texts.append(f"Необычно много сообщений: {head}")
        elif norm is not None and value <= norm / 3:
            texts.append(f"Необычно мало сообщений: {head}")
        else:
            texts.append(head)
    if not texts:
        return "", ""
    return "; ".join(texts), (shorts or texts)[0]


def _values(f: Facts) -> tuple[str, str]:
    texts = []
    for feat, (value, norm, _) in f.items():
        if value is None:
            continue
        w = window_of(feat)
        if feat.startswith("trend_"):
            sign = "выросли" if value >= 0 else "снизились"
            texts.append(f"Показания {sign} на {num(abs(value))} % {w}" + (f" (обычно до {num(norm)} %)" if norm is not None else ""))
        elif feat.startswith("v_std_"):
            texts.append(f"Разброс показаний {w}: {num(value, 2)}" + _usual(norm, lambda x: num(x, 2)))
        else:
            texts.append(f"Среднее показание {w}: {num(value, 2)}" + _usual(norm, lambda x: num(x, 2)))
    return ("; ".join(texts), texts[0]) if texts else ("", "")


def _stuck(f: Facts) -> tuple[str, str]:
    feat, (value, norm, _) = next(iter(f.items()))
    if value is None:
        return "", ""
    w = window_of(feat)
    text = f"Показания «застыли»: {pct(value)} одинаковых подряд {w}".strip() + (f" (обычно до {pct(norm)})" if norm is not None else "")
    return text, f"Показания «застыли»: {pct(value)} одинаковых {w}".strip()


def _object_faults(f: Facts) -> tuple[str, str]:
    feat, (value, _norm, _) = next(iter(f.items()))
    if value is None:
        return "", ""
    where = "в родительском объекте" if feat.startswith("parent_") else "у других датчиков этого объекта"
    text = f"{num(value)} {plural(value, 'отказ', 'отказа', 'отказов')} {where} {window_of(feat)}".strip()
    short = f"{num(value)} {plural(value, 'отказ', 'отказа', 'отказов')} рядом {window_of(feat)}".strip()
    return text[0].upper() + text[1:], short


def _neighbors(f: Facts) -> tuple[str, str]:
    texts = []
    for feat, (value, norm, _) in f.items():
        if value is None:
            continue
        if feat == "neighbor_deviation":
            texts.append(f"Показания отличаются от соседних датчиков того же типа на {num(value, 1)}σ"
                         + (f" (обычно до {num(norm, 1)}σ)" if norm is not None else ""))
        elif feat == "nb_dev_ev_ratio_24h":
            # ev_ratio_24h датчика минус медиана того же отношения у однотипных датчиков объекта
            more = "больше" if value > 0 else "меньше"
            much = "заметно " if abs(value) >= 0.5 else ""
            texts.append(f"Сообщений за сутки {much}{more}, чем у однотипных датчиков объекта (с учётом их обычной активности)")
        elif feat == "nb_dev_v_mean_24h":
            texts.append(f"Показания за сутки отличаются от однотипных соседей на {num(value, 2)}")
        elif feat == "nb_dev_unc_share_24h":
            p = value * 100 if abs(value) <= 1 else value
            more = "чаще" if p > 0 else "реже"
            texts.append(f"«Неопределен» {more}, чем у однотипных соседей ({num(abs(p))} п. п.)")
    return ("; ".join(texts), texts[0]) if texts else ("", "")


def _static(f: Facts) -> tuple[str, str]:
    texts = []
    for feat, (value, _norm, raw) in f.items():
        if feat == "sensor_type":
            texts.append(f"Особенность типа датчика «{raw}»")
        elif feat == "system_type":
            texts.append(f"Особенность системы «{raw}»")
        elif feat == "parent_id":
            texts.append("Особенность объекта, где стоит датчик")
        elif feat == "nb_same_type" and value is not None:
            texts.append(f"В объекте {num(value)} {plural(value, 'однотипный датчик', 'однотипных датчика', 'однотипных датчиков')}")
    return ("; ".join(texts), texts[0]) if texts else ("", "")


PLACES = {"has_pk": "на пикете (ПК)", "has_ans": "в насосной (АНС)", "has_shield": "в щите или шкафу", "has_vsh": "в вентшахте (ВШ)"}


def _place(f: Facts) -> tuple[str, str]:
    places = [PLACES[k] for k, (v, _, _) in f.items() if k in PLACES and v]
    text = "Место установки: " + ", ".join(places) if places else "Особенность места установки датчика"
    return text, text


def _calendar(f: Facts) -> tuple[str, str]:
    feat, (value, _, _) = next(iter(f.items()))
    if feat == "cal_hour" and value is not None:
        text = f"Время суток ({int(value)} ч): в эти часы отказы бывают чаще"
    elif feat == "cal_weekend":
        text = "Выходной день: в выходные отказы бывают чаще" if value else "Будний день"
    else:
        text = "Сезонность: в это время отказы бывают чаще"
    return text, shorten(text)


def _past_gaps(f: Facts) -> tuple[str, str]:
    feat, (value, norm, _) = next(iter(f.items()))
    if value is None:
        return "", ""
    w = window_of(feat) or "за последнее время"
    usual = f" (обычно не дольше {duration(norm)})" if norm is not None else ""
    span = {"за 7 дней": 7 * 24, "за 30 дней": 30 * 24}.get(w)
    if span is not None and value > span:  # пауза началась раньше окна и закончилась в нём
        return (f"{w[0].upper()}{w[1:]} датчик вышел на связь после паузы {duration(value)}{usual}",
                f"Недавно была пауза в связи {duration(value)}")
    return f"Самая долгая пауза в связи {w}: {duration(value)}{usual}", f"Пауза в связи до {duration(value)} {w}"


def _phase_off(f: Facts) -> tuple[str, str]:
    feat, (value, norm, _) = next(iter(f.items()))
    if value is None:
        return "", ""
    text = f"Фазы питания объекта обесточены: {pct(value)} сообщений {window_of(feat)}".strip() + _usual(norm, pct)
    return text, f"Обесточены фазы объекта: {pct(value)}"


def times(n: float) -> str:
    """«один раз», «дважды», «трижды», «5 раз»."""
    n = int(round(n))
    return {1: "один раз", 2: "дважды", 3: "трижды"}.get(n, f"{num(n)} {plural(n, 'раз', 'раза', 'раз')}")


SENSOR_WORD = {"Датчик температуры": "термодатчика", "Газовый датчик": "газового датчика", "ИБП": "ИБП"}


def _first(f: Facts):
    """Первый признак группы с известным значением: (признак, значение, норма)."""
    for feat, (value, norm, _) in f.items():
        if value is not None and not feat.startswith("__"):
            return feat, value, norm
    raise StopIteration


def _sentinel(f: Facts) -> tuple[str, str]:
    st = f.get("__sensor_type", (None, None, None))[2]
    who = SENSOR_WORD.get(str(st), "датчика")
    feat, value, norm = _first(f)
    if feat == "sentinel_share_7d":
        text = f"Служебные коды вместо показаний — {pct(value)} сообщений за 7 дней" + _usual(norm, pct)
        return text + (f" — вероятен сбой {who}" if value > 0 else ""), f"Служебные коды: {pct(value)} сообщений"
    w = window_of(feat)
    word = plural(value, "служебное значение", "служебных значения", "служебных значений")
    code = " «−127»" if who == "термодатчика" else ""
    text = f"{w.capitalize()} {num(value)} {word}{code} вместо показаний".strip() + _usual(norm, num)
    if value > 0:
        text += f" — вероятен сбой {who}"
    return text, f"{num(value)} {word} {w}".strip()


def _date1970(f: Facts) -> tuple[str, str]:
    feat, value, norm = _first(f)
    w = window_of(feat)
    word = plural(value, "значение", "значения", "значений")
    text = f"{w.capitalize()} {num(value)} {word} «01.01.1970» вместо показаний — сбой передачи значения".strip()
    return text + _usual(norm, num), f"«01.01.1970» вместо значения: {num(value)} {w}".strip()


def _date_current(f: Facts) -> tuple[str, str]:
    feat, value, norm = _first(f)
    text = f"Вместо значения приходит текущая дата: {num(value)} {window_of(feat)}".strip() + _usual(norm, num)
    return text, text


def _gas(f: Facts, where: str, tail: str = "") -> tuple[str, str]:
    feat, value, norm = _first(f)
    w = window_of(feat)
    text = f"Газ выше 1 % {times(value)} {w} {where}".strip() + _usual(norm, num) + tail
    return text, f"Газ > 1 % {times(value)} {w}".strip()


def _gas5(f: Facts) -> tuple[str, str]:
    feat, value, norm = _first(f)
    w = window_of(feat)
    word = plural(value, "показание", "показания", "показаний")
    text = (f"Газ 5 % и выше (взрывоопасная концентрация) — {num(value)} {word} {w}".strip() + _usual(norm, num)
            + ". Без подтверждения тревогой и обходом это обычно сбой датчика")
    return text, f"Газ ≥ 5 %: {num(value)} {word} {w}".strip()


def _repairs(f: Facts) -> tuple[str, str]:
    def v(name):
        return f[name][0] if name in f else None

    parts = []
    for name in ("fault_episodes_90d", "fault_episodes_30d"):
        if v(name) is not None:
            n = v(name)
            parts.append(f"{num(n)} {plural(n, 'эпизод', 'эпизода', 'эпизодов')} «Неисправен» "
                         f"(потеря связи с устройством) {window_of(name)}")
            break
    for name in ("disc_episodes_90d", "disc_episodes_30d"):
        if v(name) is not None:
            n = v(name)
            parts.append(f"{num(n)} {plural(n, 'отключение', 'отключения', 'отключений')} устройства {window_of(name)}")
            break
    rec, rec_h = v("recoveries_90d"), v("recovery_h_90d")
    if rec is not None:
        t = f"связь восстанавливалась {times(rec)} за 90 дней"
        if rec_h is not None:
            t += f", в среднем через {duration(rec_h)}"
        parts.append(t)
    elif rec_h is not None:
        parts.append(f"после потери связь в среднем восстанавливалась через {duration(rec_h)}")
    if not parts:
        return "", ""
    return "История по журналу: " + "; ".join(parts), shorten(parts[0])


def _group_silence(f: Facts) -> tuple[str, str]:
    texts, short = [], ""
    if "nb_silent_share" in f and f["nb_silent_share"][0] is not None:
        share = f["nb_silent_share"][0]
        texts.append(f"Сейчас молчат {pct(share)} однотипных датчиков объекта"
                     + (" — вероятно, плановые работы или отключение объекта" if share >= 0.5 else ""))
        short = f"Молчат {pct(share)} соседей того же типа"
    for feat in ("group_silences_30d", "group_silences_90d"):
        if feat in f and f[feat][0] is not None:
            n = f[feat][0]
            texts.append(f"Датчик {times(n)} попадал в групповое отключение объекта {window_of(feat)} "
                         "(плановые работы, не отказ датчика)")
            short = short or f"Групповых отключений {window_of(feat)}: {num(n)}"
            break
    return ". ".join(texts), short


def _fleet(f: Facts) -> tuple[str, str]:
    texts = []
    for feat, (value, norm, _) in f.items():
        if value is None:
            continue
        if feat == "type_fault_trend_7d_90d":
            texts.append(f"Отказов у датчиков этого типа по всему парку за неделю в {num(value, 1)} раза "
                         f"{'больше' if value >= 1 else 'меньше'} обычного")
        elif feat == "type_fault_msg_rate_7d":
            texts.append(f"Сообщений «Неисправен» на датчик этого типа по парку за 7 дней — {num(value, 2)}"
                         + _usual(norm, lambda x: num(x, 2)))
        else:
            texts.append(f"Отказов на датчик этого типа по парку {window_of(feat)} — {num(value, 2)}"
                         + _usual(norm, lambda x: num(x, 2)))
    return ("; ".join(texts), texts[0]) if texts else ("", "")


def _pulse(f: Facts) -> tuple[str, str]:
    texts = []
    for feat, (value, norm, _) in f.items():
        if value is None:
            continue
        if feat == "zero_days_30d":
            texts.append(f"Дней без единого сообщения за 30 дней: {num(value)}" + _usual(norm, num))
        elif feat == "daily_cv_30d":
            texts.append(f"Сообщения приходят неравномерно: разброс по дням {num(value, 1)}"
                         + _usual(norm, lambda x: num(x, 1)))
        elif feat == "history_days":
            texts.append(f"Датчик в журнале {num(value)} {plural(value, 'день', 'дня', 'дней')}"
                         + (" — обычная пауза связи ещё ненадёжна" if value < 30 else ""))
    return ("; ".join(texts), texts[0]) if texts else ("", "")


BUILDERS: dict[str, Callable[[Facts], tuple[str, str]]] = {
    "sentinel": _sentinel,
    "date1970": _date1970,
    "date_current": _date_current,
    "gas_offhours": lambda f: _gas(f, "вне рабочего времени", " — вне плановых проверок баллонами"),
    "gas_work": lambda f: _gas(f, "в рабочее время будней", " — похоже на плановую проверку баллоном"),
    "gas5": _gas5,
    "gas_detected": lambda f: _counts(f, "сообщение «Обнаружен газ»", "сообщения «Обнаружен газ»",
                                      "сообщений «Обнаружен газ»"),
    "repairs": _repairs,
    "value_failures": lambda f: (lambda t: ("Сбои значения уже были: " + t[0], t[1]) if t[0] else t)(
        _counts(f, "сбой значения", "сбоя значения", "сбоев значения")),
    "group_silence": _group_silence,
    "fleet": _fleet,
    "pulse": _pulse,
    "silence": _silence,
    "fault_msgs": lambda f: _counts(f, "сообщение «Неисправен»", "сообщения «Неисправен»", "сообщений «Неисправен»"),
    "disc_msgs": lambda f: _counts(f, "сообщение «Отключено устройство»", "сообщения «Отключено устройство»",
                                   "сообщений «Отключено устройство»"),
    "power_off": lambda f: _counts(f, "сообщение «Обесточен»", "сообщения «Обесточен»", "сообщений «Обесточен»"),
    "faults": _faults,
    "link_losses": lambda f: (lambda t: ("Связь уже пропадала: " + t[0], t[1]) if t[0] else t)(
        _counts(f, "пропадание связи", "пропадания связи", "пропаданий связи")),
    "past_gaps": _past_gaps,
    "status_changes": _status,
    "unc_share": lambda f: _shares(f, "«Неопределен» — {p} сообщений {w}"),
    "off_share": lambda f: _shares(f, "«Обесточен» — {p} сообщений {w}"),
    "alarm_share": lambda f: _shares(f, "Тревожных сообщений — {p} {w}"),
    "activity": _activity,
    "values": _values,
    "stuck": _stuck,
    "object_faults": _object_faults,
    "phase_off": _phase_off,
    "neighbors": _neighbors,
    "starts": lambda f: (lambda t: (t[0] + (" — частые пуски" if t[0] else ""), t[1]))(
        _counts(f, "пуск", "пуска", "пусков")),
    "static": _static,
    "place": _place,
    "calendar": _calendar,
}


def humanize(top_factors, limit: int = 3) -> list[HumanFactor]:
    """top_factors (список {feature, value, norm, phrase} или просто фраз) → до limit разных доводов."""
    groups: dict[str, Facts] = {}
    techs: dict[str, list[str]] = {}
    order: list[str] = []
    for i, item in enumerate(top_factors or []):
        if isinstance(item, str):
            key = f"text:{i}"
            groups[key], techs[key] = {}, [item]
            order.append(key)
            continue
        if not isinstance(item, dict):
            continue
        feature = str(item.get("feature") or "")
        key = group_of(feature) if feature else f"text:{i}"
        if key not in groups:
            groups[key], techs[key] = {}, []
            order.append(key)
        if feature:
            groups[key][feature] = (_f(item.get("value")), _f(item.get("norm")), item.get("value"))
            if key == "sentinel" and item.get("sensor_type"):
                groups[key]["__sensor_type"] = (None, None, item["sensor_type"])
        if item.get("phrase"):
            techs[key].append(str(item["phrase"]))

    result = []
    for key in order:
        text, short = "", ""
        builder = BUILDERS.get(key)
        if builder is not None:
            try:
                text, short = builder(groups[key])
            except (TypeError, ValueError, KeyError, StopIteration):  # неожиданные значения — техническая фраза
                text, short = "", ""
        if not text:
            text = "; ".join(techs[key]) or ", ".join(groups[key])
        if not text:
            continue
        text = text.rstrip(".")
        result.append(HumanFactor(text=text, short=shorten(short or text),
                                  features=[x for x in groups[key] if not x.startswith("__")], tech=techs[key]))
        if len(result) >= limit:
            break
    return result
