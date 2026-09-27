"""Разбор пикета из названия датчика (docs/GEO_SCHEME.md).

Ответ заказчика: координат нет, 1 пикет (ПК) = 10 м; у 10 638 из 11 485 датчиков пикет есть в названии:
«ПК16+5», «ПК1022+7», «ПК192(ПК192-ПК202 лев.)». Правила разбора (прозрачные, без ML):

- Пикет — «ПК N» или «ПК N+x»; регистр не важен («пк2»). Смещение x — **метры от пикета**: в данных оно 0–9 и
  половинки (x,5), то есть укладывается в 10-метровый пикет (единичные +10…+30 тоже считаем метрами).
  Положение на оси = N × 10 + x метров.
- Галерея: «ПК44 Г1 ПК14», «ПК172 Гал.ПК43», «ПК863 Гал.1 ПК7», «ПК396Г3ПК0», «ПК53 Г.АТС ПК9» — второй пикет лежит в боковой
  галерее, которая отходит от основного тела на первом пикете. «ПК18 Гал.172» — обратная запись (галерея от ПК172,
  положение ПК18). «Г1 ПК2+6» без основного пикета — галерея без точки примыкания (её находит генератор схемы
  по другим датчикам той же галереи объекта). «развилка в Г1 ПК200» — развилка на основном теле, ПК200.
- Диапазон: «ПК305-ПК307», «ПК153+7-ПК155», «(ПК87-106)» — участок, который обслуживает датчик (ДД, ТД, ГРО, ФВ);
  точка датчика — первый пикет названия, участок хранится отдельно.
- Сторона: «лев.» / «прав.»; метка: ВШ (вентшахта), АНС (насосная), щит (щитовая); уровень «ур.2».
- Нет «ПК» с числом — пикета нет: датчик показывается в узле своего объекта.
"""

import re
from dataclasses import dataclass

PICKET_M = 10.0  # 1 ПК = 10 м (ответ заказчика)

_NUM = r"(\d+(?:[.,]\d+)?)"
_PK = rf"ПК\s*{_NUM}(?:\s*\+\s*{_NUM})?"
# Галерея после основного пикета: «Г1», «Г.1», «Г.АТС», «Гал.», «гал», «ГАЛ», «Г» (слитно или через пробел)
# «Гал.» проверяется раньше «Г»; номер или имя галереи — после: «Г1», «Г.АТС», «Гал.1»
_GAL = r"(?:ГАЛ[А-ЯЁ]*|Г)\s*\.?\s*(\d+|[А-ЯЁ]{2,})?"
RE_MAIN_GALLERY = re.compile(rf"{_PK}\s*{_GAL}\s*\.?\s*{_PK}")
RE_GALLERY_ONLY = re.compile(rf"(?:^|[\s(,;])(?:ГАЛ[А-ЯЁ]*\.?|Г\s*\.?\s*(\d+))\s*{_PK}")
RE_GALLERY_REVERSE = re.compile(rf"{_PK}\s*ГАЛ\.?\s*(\d+)(?!\s*\.?\s*ПК)")  # «Гал.1 ПК7» — не обратная запись
RE_RANGE = re.compile(rf"{_PK}\s*[-–]\s*(?:ПК)?\s*{_NUM}(?:\s*\+\s*{_NUM})?")
RE_PK = re.compile(_PK)
RE_LEVEL = re.compile(r"УР\.?\s*(\d)")


@dataclass(frozen=True)
class Picket:
    """Результат разбора. Положения — в метрах от ПК0 своей оси (основного тела или галереи)."""

    main_m: float | None  # точка на основном теле (или точка примыкания галереи)
    gallery: str | None = None  # номер/имя галереи («1», «АТС»; «?» — без номера)
    gallery_m: float | None = None  # положение в галерее
    range_m: tuple[float, float] | None = None  # участок, который обслуживает датчик
    side: str | None = None  # left | right
    feature: str | None = None  # ВШ | АНС | щит
    level: int | None = None
    label: str = ""  # «ПК44+5», «ПК44 · Г1 ПК14», «ПК305–ПК307»

    @property
    def parsed(self) -> bool:
        return self.main_m is not None or self.gallery_m is not None


def _m(pk: str, off: str | None) -> float:
    return float(pk.replace(",", ".")) * PICKET_M + (float(off.replace(",", ".")) if off else 0.0)


def _pk_label(pk: str, off: str | None) -> str:
    return f"ПК{pk}" + (f"+{off}" if off else "")


def _gal_label(gname: str) -> str:
    return "гал." if gname == "?" else f"Г{gname}"


def parse(name: str) -> Picket:
    text = (name or "").upper().replace("–", "-").replace("—", "-")
    # «лев.»/«прав.» — отдельным словом: «Управление» не сторона
    side = "left" if re.search(r"(?<![А-ЯЁ])ЛЕВ", text) else "right" if re.search(r"(?<![А-ЯЁ])ПРАВ", text) else None
    feature = "ВШ" if re.search(r"(?<![А-ЯЁ])ВШ(?![А-ЯЁ])", text) else "АНС" if "АНС" in text else (
        "щит" if "ЩИТ" in text else None)
    lvl = RE_LEVEL.search(text)
    level = int(lvl.group(1)) if lvl else None
    common = {"side": side, "feature": feature, "level": level}

    rng = None
    r = RE_RANGE.search(text)
    if r:
        a, b = _m(r.group(1), r.group(2)), _m(r.group(3), r.group(4))
        rng = (min(a, b), max(a, b))

    g = RE_MAIN_GALLERY.search(text)
    if g:
        main = _m(g.group(1), g.group(2))
        gname = g.group(3) or "?"
        pos = _m(g.group(4), g.group(5))
        return Picket(main, gname, pos, None, **common,
                      label=f"{_pk_label(g.group(1), g.group(2))} · {_gal_label(gname)} "
                            f"{_pk_label(g.group(4), g.group(5))}")
    rev = RE_GALLERY_REVERSE.search(text)
    if rev:
        pos, anchor = _m(rev.group(1), rev.group(2)), float(rev.group(3)) * PICKET_M
        return Picket(anchor, "?", pos, None, **common,
                      label=f"ПК{rev.group(3)} · гал. {_pk_label(rev.group(1), rev.group(2))}")
    go = RE_GALLERY_ONLY.search(text)
    if go and not RE_PK.search(text[: go.start() + 1]) and "РАЗВИЛК" not in text:
        pos = _m(go.group(2), go.group(3))
        gname = go.group(1) or "?"
        return Picket(None, gname, pos, None, **common,
                      label=f"{_gal_label(gname)} {_pk_label(go.group(2), go.group(3))}")
    p = RE_PK.search(text)
    if p:
        main = _m(p.group(1), p.group(2))
        label = _pk_label(p.group(1), p.group(2))
        if rng and (rng[1] - rng[0]) > 0:
            label += f" (участок ПК{rng[0] / PICKET_M:g}–ПК{rng[1] / PICKET_M:g})"
        return Picket(main, None, None, rng, **common, label=label)
    return Picket(None, **common)
