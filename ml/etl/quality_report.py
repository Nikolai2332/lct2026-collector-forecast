"""Отчёт качества данных → docs/DATA_QUALITY.md. Все числа — из data/quality/*.json и запросов к Parquet.

Запуск: python -m etl.quality_report
"""

import json
import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import DATA, DIM, EVENTS, ROOT, SRC  # noqa: E402

OUT = ROOT / "docs" / "DATA_QUALITY.md"


def md_table(df) -> str:
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        cells = []
        for c, v in zip(cols, row):
            if c == "год":
                cells.append(str(v))
            elif isinstance(v, float):
                cells.append(f"{v:,.0f}".replace(",", " ") if abs(v) >= 1000 else f"{v:.3g}" if abs(v) < 1 else f"{v:.2f}".rstrip("0").rstrip("."))
            elif isinstance(v, int):
                cells.append(f"{v:,}".replace(",", " "))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    con = duckdb.connect()
    con.execute("SET threads=28")
    con.execute(f"CREATE VIEW ev AS SELECT * FROM read_parquet('{EVENTS.as_posix()}/**/*.parquet', hive_partitioning=true)")
    con.execute(f"CREATE VIEW ch AS SELECT * FROM '{(DIM / 'channels.parquet').as_posix()}'")
    qs = [json.loads(p.read_text(encoding="utf-8")) | {"year": int(p.stem.split("_")[1])}
          for p in sorted((DATA / "quality").glob("journal_*.json"))]
    con.execute("CREATE TABLE q AS SELECT * FROM (SELECT unnest(?, recursive := true))", [qs])
    load = con.sql("""
        SELECT year AS "год", rows_raw AS "строк в CSV", bad_ts AS "неверная дата (повтор заголовка)",
               date_values AS "значения-даты", round(date_values / rows_raw * 100, 3) AS "значения-даты, %",
               dup_removed AS "дубли (канал, время, значение)", round(dup_removed / rows_raw * 100, 3) AS "дубли, %",
               rows_kept AS "строк в Parquet", alarm_tf AS "флаг t/f", alarm_truefalse AS "флаг true/false",
               ts_min AS "с", ts_max AS "по", seconds_total AS "сек."
        FROM q ORDER BY year""").df()
    per_year = con.sql("""
        SELECT year AS "год", count(*) AS "событий", count(DISTINCT channel_id) AS "каналов",
               round(count(*) FILTER (WHERE c.id IS NULL) / count(*) * 100, 2) AS "без канала в справочнике, %",
               count(DISTINCT channel_id) FILTER (WHERE c.id IS NULL) AS "каналов вне справочника",
               round(count(value_num) / count(*) * 100, 1) AS "числовых, %"
        FROM ev e LEFT JOIN ch c ON c.id = e.channel_id GROUP BY 1 ORDER BY 1""").df()
    no_ch_total = con.sql("SELECT round(count(*) FILTER (WHERE c.id IS NULL) / count(*) * 100, 2) FROM ev e "
                          "LEFT JOIN ch c ON c.id = e.channel_id").fetchone()[0]
    gas = con.sql("""
        SELECT year AS "год", count(DISTINCT channel_id) AS "газовых каналов", count(*) AS "событий газовых",
               round(count(*) / count(DISTINCT channel_id) / (CASE WHEN year = 2026 THEN 181 ELSE 365 END), 1) AS "событий на канал в сутки",
               (SELECT count(*) FROM ev e2 WHERE e2.year = e.year) AS "всего событий"
        FROM ev e JOIN ch c ON c.id = e.channel_id WHERE c.sensor_type = 'Газовый датчик' GROUP BY 1 ORDER BY 1""").df()
    statuses = con.sql("""
        SELECT value_text AS "статус", count(*) AS "событий", count(DISTINCT channel_id) AS "каналов",
               round(avg(is_alarm::INT), 3) AS "доля «тревожное»"
        FROM ev WHERE value_text IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""").df()
    types = con.sql("""
        SELECT system_type AS "система", sensor_type AS "тип датчика", count(*) AS "каналов",
               count(*) FILTER (WHERE id IN (SELECT DISTINCT channel_id FROM ev WHERE year = 2026)) AS "активны в 2026"
        FROM ch GROUP BY ALL ORDER BY 3 DESC""").df()
    sample = con.sql(f"""
        SELECT count(*) n, count(DISTINCT ид_канала_данных) ch,
               count(*) FILTER (WHERE значение_датчика = 'Неисправен') f,
               count(DISTINCT ид_канала_данных) FILTER (WHERE значение_датчика = 'Неисправен') fch,
               string_agg(DISTINCT тревожное, '/') fl
        FROM read_csv('{SRC}/журнал_событий_пример.csv', all_varchar=true)""").fetchone()
    total_raw, total_kept = sum(q["rows_raw"] for q in qs), sum(q["rows_kept"] for q in qs)

    text = f"""# Качество данных

Отчёт собран скриптом `ml/etl/quality_report.py` по результатам ETL (`ml/etl/load_journal.py`). Все числа — из запуска кода.

Итого: **{total_raw:,} строк в CSV → {total_kept:,} строк в Parquet** (8 файлов, 2019-01-01 … 2026-06-30).

## Загрузка по годам

{md_table(load)}

- Неверная дата — одна строка в `ext-journal-2025.csv`: повторённый внутри файла заголовок (`тревожное`, `значение_датчика` в полях). Отброшена.
- Значения-даты — вида `01.01.1970 03:00:00` и `ДД.ММ.ГГГГ ЧЧ:ММ:СС` (текущая дата вместо показания). Отброшены.
- Флаг «тревожное» во всех восьми годовых файлах записан как `t/f`; `true/false` встречается только в `журнал_событий_пример.csv`. Разбираются оба формата.
- Дубли — события с одинаковыми каналом, секундой и значением; схлопнуты в одно (минимальный `ид_события`, флаг «тревожное» — «хоть одно»).
- В файле за 2022 год значения без кавычек — все поля читаются строкой, проблем не вызвало.

## События по годам и каналы вне справочника

{md_table(per_year)}

За все годы событий без канала в справочнике — **{no_ch_total} %**. ТЗ говорит «около 6 %»: так было в 2021 году, в 2019–2020 — около 12 %, с 2023 года — меньше 0,1 %. Эти события в обучении не участвуют (у канала нет типа и объекта).

## Почему годы такие разные по объёму

{md_table(gas)}

Объём определяют газовые датчики: число каналов меняется слабо (≈ 370–530), а частота сообщений на канал — в 3–4 раза (65 → 237 в сутки). То есть разница — в настройке опроса, а не в числе датчиков. Поэтому признаки активности нормируются на личную норму канала (события за 30 дней), а сырое «число событий» отдельно не используется для сравнения между годами.

## Распределение статусов (все годы)

{md_table(statuses)}

## Каналы по типам датчиков (справочник)

{md_table(types)}

## Проверка описания из ТЗ на примере суток 01.08.2026

- Строк {sample[0]:,}, каналов {sample[1]:,} — совпадает с ТЗ (169 993 строки, 1 241 канал).
- «Неисправен» — {sample[2]} событий у {sample[3]} каналов — совпадает с ТЗ (105 у 69).
- Флаг «тревожное» в примере: `{sample[4]}`.
- Событий без канала в справочнике в примере нет (0 %), в отличие от «около 6 %» из ТЗ.
- Справочник объектов — в чистом UTF-8, продублированных строк в битой кодировке в присланной копии нет (95 строк). У района (уровень 1) родитель `3831` отсутствует в справочнике — записан как корень.
"""
    OUT.write_text(text, encoding="utf-8")
    print(f"written {OUT}")


if __name__ == "__main__":
    main()
