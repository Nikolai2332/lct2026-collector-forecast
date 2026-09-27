"""ETL журналов: ext-journal-*.csv → Parquet (data/parquet/events/year=YYYY/month=M/).

Чистка по ТЗ:
- все поля читаются строкой (в 2022 значения без кавычек, в 2025 внутри файла повторён заголовок);
- «тревожное» f/t и false/true → bool;
- ts = дата + время; строки с неразбираемой датой (повтор заголовка) выбрасываются;
- значение → вид значения value_kind (v3, по официальным ответам заказчика 17–18.09 и 23.09):
    number     — числовое показание в физически возможном диапазоне типа датчика → value_num;
    status     — текстовый статус («Норма», «Неисправен», …) → value_text;
    date_value — дата вместо показания («01.01.1970 03:00:00» или текущая дата) — НЕ выбрасывается (в v2 выбрасывалась);
    sentinel   — служебный код производителя (-127, -3276,8, 255, -100, 327,68, …) или выход за физический диапазон
                 типа датчика (температура вне [-55; 125] °C, газ вне [0; 100] % объёма). value_num = NULL:
                 служебные значения никогда не используются как показания;
  исходная строка значения всегда остаётся в value_raw;
- дубли по связке «канал, время, значение» схлопываются (event_id — минимальный, alarm — любой true);
- внутри файла Parquet строки отсортированы по времени.

Статистика качества пишется в data/quality/journal_<год>.json (из неё собирается docs/DATA_QUALITY.md).

2019–2021 годы в v3 не загружаются: 2021 год организаторы рекомендовали исключить (переход на новую систему
мониторинга, лавина неснятых тревог), 2019–2020 модель не использует (признаки и нормы — с 01.07.2022).

Запуск: python -m etl.load_journal [--years 2023 2024 ...] [--threads 24]
Требует справочник каналов (python -m etl.load_dims) — физический диапазон зависит от типа датчика.
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import DATA, DIM, EVENTS, SRC  # noqa: E402

YEARS = list(range(2022, 2027))  # 2021 исключён по рекомендации организаторов; 2019–2020 не используются
EXCLUDED_YEARS = (2019, 2020, 2021)
QUALITY = DATA / "quality"
DATE_VALUE_RE = r"^\s*\d{2}\.\d{2}\.\d{4}( \d{1,2}:\d{2}(:\d{2})?)?\s*$"
# Физически возможный диапазон показаний по типу датчика (вне его — sentinel). Температура — диапазон измерения
# типового цифрового термодатчика (-55…+125 °C; -127 — его код обрыва); газ и ИБП — проценты (0…100).
# Частоты по данным 2022–2025: -127 — 4 587 раз на 351 канале, ≤ -1000 (-3276…) — 130, 128 — 102 на 13 каналах,
# 255 — 2; газ 327,68 — 3. Список кодов ниже ловит их у любых типов, диапазон — выход за физику.
PHYS_RANGE = {"Датчик температуры": (-55.0, 125.0), "Газовый датчик": (0.0, 100.0), "ИБП": (0.0, 100.0)}
SENTINEL_CODES = (-127.0, -100.0, 255.0, 327.68, -3276.8, -3276.0, 3276.7, 32767.0, -32768.0, 65535.0)


def connect(threads: int) -> duckdb.DuckDBPyConnection:
    tmp = DATA / "duckdb_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET threads={threads}")
    con.execute("SET memory_limit='20GB'")
    con.execute(f"SET temp_directory='{tmp.as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def csv_source(path: str) -> str:
    cols = ("{'ид_события':'VARCHAR','ид_канала_данных':'VARCHAR','дата':'VARCHAR','время':'VARCHAR',"
            "'тревожное':'VARCHAR','значение_датчика':'VARCHAR'}")
    return (f"read_csv('{path}', header=true, delim=',', quote='\"', escape='\"', columns={cols}, "
            f"auto_detect=false, parallel=true, null_padding=true, ignore_errors=false)")


def load_year(con: duckdb.DuckDBPyConnection, year: int) -> dict:
    t0 = time.time()
    path = f"{SRC}/ext-journal-{year}.csv"
    # 1) разбор: всё в типизированную временную таблицу
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE raw AS
        SELECT
            try_cast(ид_события AS BIGINT) AS event_id,
            try_cast(ид_канала_данных AS BIGINT) AS channel_id,
            try_strptime(trim(дата) || ' ' || trim(время), '%Y-%m-%d %H:%M:%S') AS ts,
            lower(trim(тревожное)) AS alarm_raw,
            trim(значение_датчика) AS value_raw
        FROM {csv_source(path)}
    """)
    t_read = time.time() - t0
    st = con.execute(f"""
        SELECT count(*) AS rows_raw,
               count(*) FILTER (WHERE ts IS NULL) AS bad_ts,
               count(*) FILTER (WHERE channel_id IS NULL) AS bad_channel,
               count(*) FILTER (WHERE ts IS NOT NULL AND regexp_matches(value_raw, '{DATE_VALUE_RE}')) AS date_values,
               count(*) FILTER (WHERE ts IS NOT NULL AND (value_raw IS NULL OR value_raw = '')) AS empty_values,
               count(*) FILTER (WHERE alarm_raw IN ('t','f')) AS alarm_tf,
               count(*) FILTER (WHERE alarm_raw IN ('true','false')) AS alarm_truefalse,
               count(*) FILTER (WHERE alarm_raw NOT IN ('t','f','true','false') OR alarm_raw IS NULL) AS alarm_other,
               min(ts) AS ts_min, max(ts) AS ts_max
        FROM raw
    """).fetchone()
    names = ["rows_raw", "bad_ts", "bad_channel", "date_values", "empty_values",
             "alarm_tf", "alarm_truefalse", "alarm_other", "ts_min", "ts_max"]
    q = dict(zip(names, st))
    q["ts_min"], q["ts_max"] = str(q["ts_min"]), str(q["ts_max"])
    q["bad_ts_samples"] = [list(r) for r in con.execute(
        "SELECT event_id, channel_id, alarm_raw, value_raw FROM raw WHERE ts IS NULL LIMIT 5").fetchall()]
    q["date_value_samples"] = [r[0] for r in con.execute(
        f"SELECT value_raw FROM raw WHERE regexp_matches(value_raw, '{DATE_VALUE_RE}') LIMIT 5").fetchall()]

    # 2) чистка + дедупликация + запись Parquet, отсортированного по времени
    out = EVENTS / f"year={year}"
    if out.exists():
        shutil.rmtree(out)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE clean AS
        SELECT min(event_id) AS event_id, channel_id, ts,
               bool_or(alarm_raw IN ('t','true')) AS is_alarm,
               value_raw,
               count(*) AS dup_n
        FROM raw
        WHERE ts IS NOT NULL AND channel_id IS NOT NULL
          AND value_raw IS NOT NULL AND value_raw <> ''
        GROUP BY channel_id, ts, value_raw
    """)
    con.execute("DROP TABLE raw")
    q["rows_kept"], q["dup_removed"] = con.execute("SELECT count(*), sum(dup_n) - count(*) FROM clean").fetchone()
    ranges = " ".join(f"WHEN '{t}' THEN x < {lo} OR x > {hi}" for t, (lo, hi) in PHYS_RANGE.items())
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE typed AS
        WITH n AS (
            SELECT c.*, try_cast(replace(c.value_raw, ',', '.') AS DOUBLE) AS x, d.sensor_type
            FROM clean c LEFT JOIN '{(DIM / 'channels.parquet').as_posix()}' d ON d.id = c.channel_id
        )
        SELECT event_id, channel_id, ts, is_alarm, value_raw,
               CASE WHEN regexp_matches(value_raw, '{DATE_VALUE_RE}') THEN 'date_value'
                    WHEN x IS NULL THEN 'status'
                    WHEN x IN {SENTINEL_CODES} OR abs(x) >= 1000 OR coalesce(CASE sensor_type {ranges} END, false) THEN 'sentinel'
                    ELSE 'number' END AS value_kind,
               x, sensor_type
        FROM n
    """)
    con.execute("DROP TABLE clean")
    q["value_kinds"] = {k: n for k, n in con.execute("SELECT value_kind, count(*) FROM typed GROUP BY 1").fetchall()}
    q["sentinels"] = [list(r) for r in con.execute("""
        SELECT coalesce(sensor_type, '(нет в справочнике)'), x, count(*) n, count(DISTINCT channel_id) ch,
               count(*) FILTER (WHERE is_alarm) alarm
        FROM typed WHERE value_kind = 'sentinel' GROUP BY ALL ORDER BY n DESC LIMIT 25""").fetchall()]
    q["date_values_by_type"] = [list(r) for r in con.execute("""
        SELECT coalesce(sensor_type, '(нет в справочнике)'), (value_raw LIKE '01.01.1970%') AS epoch_1970,
               count(*) n, count(DISTINCT channel_id) ch, count(*) FILTER (WHERE is_alarm) alarm
        FROM typed WHERE value_kind = 'date_value' GROUP BY ALL ORDER BY n DESC""").fetchall()]
    con.execute(f"""
        COPY (
            SELECT event_id, channel_id, ts, is_alarm, value_raw, value_kind,
                   CASE WHEN value_kind = 'number' THEN x END AS value_num,
                   CASE WHEN value_kind = 'status' THEN value_raw END AS value_text,
                   year(ts) AS year, month(ts) AS month
            FROM typed ORDER BY ts, channel_id
        ) TO '{EVENTS.as_posix()}' (FORMAT PARQUET, PARTITION_BY (year, month), COMPRESSION ZSTD,
                                    ROW_GROUP_SIZE 1000000, OVERWRITE_OR_IGNORE)
    """)
    con.execute("DROP TABLE typed")
    q["seconds_read"] = round(t_read, 1)
    q["seconds_total"] = round(time.time() - t0, 1)
    return q


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="*", default=YEARS)
    ap.add_argument("--threads", type=int, default=28)
    args = ap.parse_args()
    QUALITY.mkdir(parents=True, exist_ok=True)
    con = connect(args.threads)
    for y in args.years:
        q = load_year(con, y)
        (QUALITY / f"journal_v3_{y}.json").write_text(json.dumps(q, ensure_ascii=False, indent=1, default=str),
                                                    encoding="utf-8")
        print(f"[{y}] {json.dumps(q, ensure_ascii=False, default=str)}", flush=True)


if __name__ == "__main__":
    main()
