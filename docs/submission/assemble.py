"""Собирает build/full.md: подставляет в doc.md таблицы из docs/TZ_COVERAGE.md и перечень библиотек deps/deps.md,
подбирает ширину колонок таблиц по содержимому.

Таблицы ТЗ берутся из файла проекта как есть (ссылки Markdown превращаются в текст) — чтобы документ и
репозиторий не расходились."""

import os
import re
from pathlib import Path

here = Path(__file__).resolve().parent
doc = (here / "doc.md").read_text(encoding="utf-8")


def unlink(s: str) -> str:
    return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)


# ---- TZ_COVERAGE: разделы «## N. …» и таблицы под ними → подразделы без автономера с подписями
tz = (here.parent / "TZ_COVERAGE.md").read_text(encoding="utf-8")
blocks, title, table = [], None, []
for line in tz.splitlines() + ["## end"]:
    if line.startswith("## "):
        if title and table:
            num, _, name = title.partition(". ")
            blocks.append(f"### Раздел {num} ТЗ. {name} {{-}}\n\n" + "\n".join(unlink(r) for r in table)
                          + f"\n\nTable: Сверка с общим ТЗ, раздел {num}. {name}\n")
        title, table = line[3:].strip(), []
    elif line.startswith("|") and title:
        table.append(line)
doc = doc.replace("<!--TZ_COVERAGE-->", "\n".join(blocks))

# ---- Зависимости
deps = (here / "deps" / "deps.md").read_text(encoding="utf-8")
direct, rest = deps.split("<!--LICENSES-->")
licenses, appendix = rest.split("<!--APPENDIX-->")


def captions(md: str, prefix: str) -> str:
    """Подпись к каждой таблице — из жирного заголовка над ней."""
    out, last = [], ""
    lines = md.strip("\n").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("**"):
            last = line.replace("**", "").split("—")[0].strip().rstrip(",")
        out.append(line)
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if line.startswith("|") and not nxt.startswith("|"):
            out += ["", f"Table: {prefix}: {last}", ""]
    return "\n".join(out)


doc = doc.replace("<!--DEPS_DIRECT-->", captions(direct, "Прямые зависимости"))
doc = doc.replace("<!--DEPS_LICENSES-->", licenses.strip() + "\n\nTable: Лицензии всех пакетов (бэкенд, ML, фронтенд)\n")
doc = doc.replace("<!--DEPS_APPENDIX-->", captions(appendix, "Все пакеты"))
assert "<!--" not in doc, "остались незаполненные вставки"

SEP = re.compile(r"^\|(\s*:?-+:?\s*\|)+$")


def fit_columns(md: str) -> str:
    """Ширина колонок — по длине содержимого. pandoc берёт пропорции из числа дефисов в строке-разделителе (когда
    строки таблицы длиннее ширины текста). Колонка не уже 8 % и не шире 60 %."""
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) and SEP.match(lines[i + 1].strip()):
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            rows = [[c.strip() for c in r.strip().strip("|").split("|")] for r in [lines[i]] + lines[i + 2:j]]
            n = len(rows[0])
            lens = [min(max(max((len(r[k]) if k < len(r) else 0) for r in rows), 6), 90) for k in range(n)]
            # самое длинное слово колонки не должно рваться: при 9 пт в строку таблицы помещается ≈ 75 знаков с полями ячеек
            words = [max((len(w) for r in rows if k < len(r) for w in r[k].split()), default=4) for k in range(n)]
            floor = [max(8, round(100 * (w + 3) / 75)) for w in words]
            total = sum(lens)
            pct = [max(floor[k], min(60, round(100 * lens[k] / total))) for k in range(n)]
            out.append(lines[i])
            out.append("|" + "|".join("-" * p for p in pct) + "|")
            out += lines[i + 2:j]
            i = j
        else:
            out.append(lines[i])
            i += 1
    return "\n".join(out)


# Вариант «реальные данные» (build.ps1 -Real): скриншоты среза июня 2026 вместо демо. Сами файлы копирует
# build.ps1 в build/real/ (вне git), здесь меняются только пути и подписи.
REAL_SCREENS = [
    ("../screenshots/1366x768/02_dashboard.png", "Дашборд (демо-данные)",
     "build/real/01_dashboard.png",
     "Дашборд на реальных данных, срез 29.06.2026 23:00. «Точность тревог за последние 30 дней» считается "
     "за короткий период и отличается от метрик всего теста (раздел 7)"),
    ("../screenshots/1366x768/04_channel.png", "Карточка датчика: прогноз, причины, рекомендация по ТО (демо-данные)",
     "build/real/04_channel.png",
     "Карточка датчика на реальных данных: прогноз, причина, фактический исход, рекомендация по ТО"),
    ("../screenshots/1366x768/03b_collector_scheme.png", "Схема коллектора по пикетам, геометрия синтетическая (демо-данные)",
     "build/real/03_objects_scheme.png",
     "Схема коллекторов по пикетам на реальных данных (геометрия синтетическая, пикеты — из названий датчиков)"),
    ("../screenshots/1366x768/05b_events.png", "Журнал событий с контекстом (демо-данные)",
     "build/real/05b_events.png",
     "Журнал тревожных событий на реальных данных"),
    ("../screenshots/1366x768/07_quality.png", "Качество модели (демо-данные; на срезе реальных данных — метрики v3)",
     "build/real/07_quality.png",
     "Качество модели на реальных данных: метрики v3 на тесте 2026 и сравнение с v2 на одной метке"),
    ("../screenshots/1366x768/12_users_roles.png", "Пользователи и роли (демо-данные)",
     "build/real/08_users.png",
     "Пользователи и роли (локальные учётные записи для проверки; в production — из AD)"),
]
if os.environ.get("DOC_SCREENS") == "real":
    for old_path, old_cap, new_path, new_cap in REAL_SCREENS:
        old = f"![{old_cap}]({old_path})"
        if old not in doc:
            raise SystemExit(f"в doc.md не найден рисунок: {old}")
        doc = doc.replace(old, f"![{new_cap}]({new_path})")

doc = fit_columns(doc)
out = here / "build" / "full.md"
out.parent.mkdir(exist_ok=True)
out.write_text(doc, encoding="utf-8")
print(f"{out}: {len(doc.splitlines())} строк")
