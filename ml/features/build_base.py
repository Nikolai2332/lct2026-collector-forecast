"""Базовые таблицы из Parquet-журнала v3 (data/base_v3/):

- hourly/   — агрегаты «канал × час» (только часы с событиями): счётчики статусов, смены статуса,
              суммы для среднего и разброса (только value_kind = number — служебные значения никогда не идут
              в показания), «залипание», служебные значения и значения-даты, газ ≥ 1 % и ≥ 5 % (в рабочее время
              будней 9–14 и вне его), последняя метка.
- gaps/     — паузы ≥ 1 ч между соседними событиями канала (и открытая пауза в конце данных); gap_eff_s — пауза
              без часов «дней без данных» (сбой выгрузки, а не тишина датчика).
- outages.parquet — окна без данных во всём журнале (07–08.04.2024, 01.06.2026): часы, в которые журнал пуст.
- daily_norm.parquet — плотная сетка «канал × день»: событий за день, личная норма паузы
              (99-й перцентиль пауз за 30 дней до начала дня, но не меньше 6 ч), макс. пауза за день.
- faults.parquet — все события-кандидаты в отказ с видом и флагами (метку собирает features/labels.py):
    kind: «Неисправен», «Отключено устройство», «Пропадание связи», «Сбой значения»;
    sub:  для «Сбой значения» — date_1970 (значение «01.01.1970 …»), gas_anomaly (газ вне [0; 100] % или ≥ 5 %
          без флага «тревожное»), sentinel_alarm (служебный код с флагом «тревожное»);
    mass  — час, когда связь теряют ≥ 100 каналов (сбой сбора данных, как в v2);
    grp   — групповая многодневная тишина: одновременно молчит большая доля однотипных каналов объекта или
            родительского объекта (плановые работы / отключение объекта, не отказ датчика).

2021 год и раньше не читаются (рекомендация организаторов; модель берёт историю с 01.07.2022).

Запуск: python -m features.build_base [--norms-only]
"""

import shutil
import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import BASE, DATA, DIM, EVENTS  # noqa: E402

BUCKETS = 8
YEAR_FROM = 2022  # 2021 исключён явно (организаторы: лавина неснятых тревог при переходе на новую систему)
GRID_FROM = "2022-07-01"  # плотная сетка норм: хватает на 90-дневные окна до 2023-01-01
DATA_END = "2026-07-01 00:00:00"  # журнал заканчивается 30.06.2026 23:59:59
MIN_NORM_S = 6 * 3600
MASS_OUTAGE_CHANNELS = 100  # столько каналов теряют связь в один час → сбой сбора данных, не отказ датчика
FAULT_TEXTS = ("Неисправен", "Отключено устройство")
GAS = "Газовый датчик"
GAS_ALARM, GAS_EXPLOSIVE = 1.0, 5.0  # % объёма метана: тревога и взрывоопасно (ответ заказчика 23.09)
# Час журнала считается «без данных», если событий в нём меньше этой доли от медианы часа за 7 дней до него
OUTAGE_SHARE = 0.01
# Групповая тишина (параметры выбраны по обучающему периоду, см. docs/ML_V3_PLAN.md)
GROUP_MIN_H = 24        # тишина канала длиннее нормы и не короче GROUP_MIN_H часов
GROUP_MIN_SHARE = 0.8   # доля однотипных активных каналов объекта (или родителя), молчащих одновременно
GROUP_MIN_CH = 5        # и не меньше стольких каналов
GROUP_DAILY_MIN_EV = 5  # для условия «объект замолк целиком»: каналы, шлющие обычно ≥ 5 сообщений в сутки
GROUP_TOL_H = 12        # тишина соседей началась не дальше стольких часов от начала тишины канала


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET threads=28")
    con.execute("SET memory_limit='22GB'")
    con.execute(f"SET temp_directory='{(DATA / 'duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"""CREATE VIEW ev AS SELECT * FROM read_parquet('{EVENTS.as_posix()}/**/*.parquet', hive_partitioning=true)
                    WHERE year >= {YEAR_FROM}""")
    con.execute(f"CREATE TABLE chn AS SELECT * FROM '{(DIM / 'channels.parquet').as_posix()}'")
    con.execute(f"CREATE TABLE obj AS SELECT * FROM '{(DIM / 'objects.parquet').as_posix()}'")
    return con


def build_hourly_and_gaps(con: duckdb.DuckDBPyConnection) -> None:
    for sub in ("hourly", "gaps"):
        if (BASE / sub).exists():
            shutil.rmtree(BASE / sub)
        (BASE / sub).mkdir(parents=True)
    for b in range(BUCKETS):
        t0 = time.time()
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE e AS
            WITH b AS (SELECT ev.*, (c.sensor_type = '{GAS}') AS is_gas FROM ev LEFT JOIN chn c ON c.id = ev.channel_id
                       WHERE channel_id % {BUCKETS} = {b}),
            tx AS (  -- предыдущий статус считается только среди текстовых событий
                SELECT channel_id, ts, value_raw, lag(value_text) OVER (PARTITION BY channel_id ORDER BY ts, value_raw) AS prev_t
                FROM b WHERE value_text IS NOT NULL
            ), nm AS (  -- предыдущее показание — только среди настоящих числовых показаний (без служебных кодов)
                SELECT channel_id, ts, value_raw, lag(value_num) OVER (PARTITION BY channel_id ORDER BY ts, value_raw) AS prev_v
                FROM b WHERE value_num IS NOT NULL
            )
            SELECT b.channel_id AS ch, b.ts, b.is_alarm, b.value_num AS v, b.value_text AS t, b.value_kind AS k,
                   b.value_raw AS raw, coalesce(b.is_gas, false) AS is_gas,
                   (isodow(b.ts) <= 5 AND hour(b.ts) BETWEEN 9 AND 13) AS workhours,
                   lag(b.ts) OVER (PARTITION BY b.channel_id ORDER BY b.ts, b.value_raw) AS prev_ts,
                   nm.prev_v, tx.prev_t
            FROM b LEFT JOIN tx USING (channel_id, ts, value_raw) LEFT JOIN nm USING (channel_id, ts, value_raw)
        """)
        con.execute(f"""
            COPY (
                SELECT ch, date_trunc('hour', ts) AS hour,
                       count(*)::INT AS n_ev,
                       count(*) FILTER (WHERE t = 'Неисправен')::INT AS n_fault,
                       count(*) FILTER (WHERE t = 'Отключено устройство')::INT AS n_disc,
                       count(*) FILTER (WHERE t IN ('Неопределен', 'Не определено'))::INT AS n_unc,
                       count(*) FILTER (WHERE t = 'Обесточен')::INT AS n_off,
                       count(*) FILTER (WHERE is_alarm)::INT AS n_alarm,
                       count(t)::INT AS n_txt,
                       count(*) FILTER (WHERE t IS NOT NULL AND prev_t IS NOT NULL AND t <> prev_t)::INT AS n_chg,
                       count(v)::INT AS n_num,
                       sum(v) AS s_v, sum(v * v) AS s_v2,
                       count(*) FILTER (WHERE v IS NOT NULL AND v = prev_v)::INT AS n_same,
                       min(v) AS v_min, max(v) AS v_max,
                       -- v3: служебные значения и значения-даты (как ранний сигнал сбоя), газ
                       count(*) FILTER (WHERE k = 'sentinel')::INT AS n_sent,
                       count(*) FILTER (WHERE k = 'date_value')::INT AS n_date,
                       count(*) FILTER (WHERE k = 'date_value' AND raw LIKE '01.01.1970%')::INT AS n_date1970,
                       count(*) FILTER (WHERE is_gas AND v >= {GAS_ALARM} AND workhours)::INT AS n_gas1_wk,
                       count(*) FILTER (WHERE is_gas AND v >= {GAS_ALARM} AND NOT workhours)::INT AS n_gas1_off,
                       count(*) FILTER (WHERE is_gas AND v >= {GAS_EXPLOSIVE})::INT AS n_gas5,
                       count(*) FILTER (WHERE t = 'Обнаружен газ')::INT AS n_gasdet,
                       max(ts) AS last_ts,
                       max(epoch(ts - prev_ts))::INT AS max_gap_s
                FROM e GROUP BY ALL
            ) TO '{(BASE / 'hourly' / f'b{b}.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        con.execute(f"""
            COPY (
                SELECT ch, prev_ts, ts, epoch(ts - prev_ts)::BIGINT AS gap_s FROM e
                WHERE prev_ts IS NOT NULL AND ts - prev_ts >= INTERVAL 1 HOUR
                UNION ALL
                -- открытая пауза: последнее событие канала → конец данных
                SELECT ch, max(ts), NULL, NULL FROM e GROUP BY ch
            ) TO '{(BASE / 'gaps' / f'b{b}.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        # «Сбой значения» и служебные коды — отдельные события для разметки
        con.execute(f"""
            COPY (
                SELECT ch, ts, is_alarm,
                       CASE WHEN k = 'date_value' AND raw LIKE '01.01.1970%' THEN 'date_1970'
                            WHEN k = 'date_value' THEN 'date_current'
                            WHEN is_gas AND k = 'sentinel' THEN 'gas_anomaly'
                            WHEN is_gas AND v >= {GAS_EXPLOSIVE} AND NOT is_alarm THEN 'gas_anomaly'
                            WHEN k = 'sentinel' AND is_alarm THEN 'sentinel_alarm'
                            ELSE 'sentinel' END AS sub,
                       raw
                FROM e WHERE k IN ('sentinel', 'date_value') OR (is_gas AND v >= {GAS_EXPLOSIVE} AND NOT is_alarm)
            ) TO '{(BASE / 'gaps' / f'vf{b}.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """)
        con.execute("DROP TABLE e")
        print(f"bucket {b}: {time.time() - t0:.0f} s", flush=True)


def build_outages(con: duckdb.DuckDBPyConnection) -> None:
    """Часы, в которые весь журнал почти пуст (сбой выгрузки/системы): < 1 % медианы часа за предыдущие 7 дней."""
    hourly = f"read_parquet('{(BASE / 'hourly').as_posix()}/b*.parquet')"
    con.execute(f"""
        COPY (
            WITH hrs AS (SELECT unnest(range(TIMESTAMP '{YEAR_FROM}-01-08', TIMESTAMP '{DATA_END}', INTERVAL 1 HOUR)) AS hour),
            tot AS (SELECT hour, sum(n_ev) AS n FROM {hourly} GROUP BY 1),
            h AS (SELECT hrs.hour, coalesce(tot.n, 0) AS n FROM hrs LEFT JOIN tot USING (hour)),
            m AS (SELECT hour, n, median(n) OVER (ORDER BY hour ROWS BETWEEN 168 PRECEDING AND 1 PRECEDING) AS med FROM h),
            o AS (SELECT hour, n, med FROM m WHERE n < {OUTAGE_SHARE} * med),
            g AS (SELECT *, hour - to_hours(row_number() OVER (ORDER BY hour)) AS grp FROM o)
            SELECT min(hour) AS start, max(hour) + INTERVAL 1 HOUR AS "end", count(*) AS hours, sum(n)::BIGINT AS events
            FROM g GROUP BY grp ORDER BY start
        ) TO '{(BASE / 'outages.parquet').as_posix()}' (FORMAT PARQUET)
    """)
    print(con.sql(f"SELECT * FROM '{(BASE / 'outages.parquet').as_posix()}'"))


def build_norms_and_faults(con: duckdb.DuckDBPyConnection) -> None:
    t0 = time.time()
    hourly = f"read_parquet('{(BASE / 'hourly').as_posix()}/b*.parquet')"
    con.execute(f"CREATE OR REPLACE TABLE outg AS SELECT * FROM '{(BASE / 'outages.parquet').as_posix()}'")
    # Паузы без часов «дней без данных»: тишина всего журнала — не тишина датчика.
    # (least/greatest в DuckDB пропускают NULL, поэтому строки без совпадения с окном простоя отсекаются FILTER)
    con.execute(f"""
        CREATE OR REPLACE TABLE gp AS
        SELECT g.ch, g.prev_ts, g.ts, coalesce(g.ts, TIMESTAMP '{DATA_END}') AS ts_end,
               epoch(coalesce(g.ts, TIMESTAMP '{DATA_END}') - g.prev_ts)::BIGINT
                 - coalesce(sum(epoch(least(coalesce(g.ts, TIMESTAMP '{DATA_END}'), o."end") - greatest(g.prev_ts, o.start))) FILTER (WHERE o.start IS NOT NULL), 0)::BIGINT AS gap_eff_s,
               coalesce(sum(epoch(least(coalesce(g.ts, TIMESTAMP '{DATA_END}'), o."end") - greatest(g.prev_ts, o.start))) FILTER (WHERE o.start IS NOT NULL), 0)::BIGINT AS outage_s
        FROM read_parquet('{(BASE / 'gaps').as_posix()}/b*.parquet') g
        LEFT JOIN outg o ON o.start < coalesce(g.ts, TIMESTAMP '{DATA_END}') AND o."end" > g.prev_ts
        GROUP BY g.ch, g.prev_ts, g.ts
    """)
    con.execute(f"COPY gp TO '{(BASE / 'gaps_eff.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE d_ev AS
        SELECT ch, hour::DATE AS day, sum(n_ev)::INT AS n_ev, max(max_gap_s) AS max_gap_s
        FROM {hourly} WHERE hour >= DATE '{GRID_FROM}' - INTERVAL 31 DAY GROUP BY ALL
    """)
    # Крупные паузы (≥ 6 ч, без часов простоя журнала) по дню окончания паузы — до 100 самых длинных за день
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE d_gap AS
        SELECT ch, ts::DATE AS day, list(gap_eff_s ORDER BY gap_eff_s DESC)[1:100] AS gl
        FROM gp WHERE ts IS NOT NULL AND gap_eff_s >= {MIN_NORM_S} AND ts >= DATE '{GRID_FROM}' - INTERVAL 31 DAY
        GROUP BY ALL
    """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE grid AS
        WITH span AS (SELECT ch, min(day) AS d0 FROM d_ev GROUP BY ch)
        SELECT ch, unnest(range(d0::TIMESTAMP, TIMESTAMP '{DATA_END}', INTERVAL 1 DAY))::DATE AS day FROM span
    """)
    con.execute(f"""
        COPY (
            WITH g AS (
                SELECT grid.ch, grid.day, coalesce(d_ev.n_ev, 0) AS n_ev, d_ev.max_gap_s, d_gap.gl
                FROM grid LEFT JOIN d_ev USING (ch, day) LEFT JOIN d_gap USING (ch, day)
            ), w AS (
                SELECT ch, day, n_ev, max_gap_s,
                       sum(n_ev) OVER w30 AS n_ev_30d,
                       list_sort(flatten(list(coalesce(gl, [])) OVER w30), 'DESC') AS gl30
                FROM g
                WINDOW w30 AS (PARTITION BY ch ORDER BY day ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING)
            )
            SELECT ch, day, n_ev, max_gap_s, coalesce(n_ev_30d, 0)::INT AS n_ev_30d,
                   greatest({MIN_NORM_S}, coalesce(gl30[(floor(0.01 * coalesce(n_ev_30d, 0)) + 1)::INT], 0))::BIGINT AS norm_s
            FROM w WHERE day >= DATE '{GRID_FROM}'
        ) TO '{(BASE / 'daily_norm.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    print(f"daily_norm: {time.time() - t0:.0f} s", flush=True)
    norm = f"read_parquet('{(BASE / 'daily_norm.parquet').as_posix()}')"
    # Пропадание связи: пауза (без часов простоя журнала) длиннее нормы. Момент отказа — последнее событие + норма,
    # сдвинутое на простой журнала, если он попал между ними.
    con.execute(f"""
        CREATE OR REPLACE TABLE link AS
        WITH l AS (
            SELECT g.ch, g.prev_ts, g.ts_end, g.gap_eff_s, n.norm_s,
                   g.prev_ts + to_seconds(n.norm_s) AS ts0
            FROM gp g JOIN {norm} n ON n.ch = g.ch AND n.day = g.prev_ts::DATE
            WHERE n.n_ev_30d > 0 AND g.gap_eff_s > n.norm_s
        )
        SELECT l.ch, l.prev_ts, l.ts_end, l.gap_eff_s, l.norm_s,
               l.ts0 + to_seconds(coalesce(sum(epoch(least(o."end", l.ts_end) - greatest(o.start, l.prev_ts)))
                                            FILTER (WHERE o.start IS NOT NULL AND o.start < l.ts0), 0)::BIGINT) AS ts
        FROM l LEFT JOIN outg o ON o.start < l.ts_end AND o."end" > l.prev_ts
        GROUP BY l.ch, l.prev_ts, l.ts_end, l.gap_eff_s, l.norm_s, l.ts0
    """)
    con.execute(f"DELETE FROM link WHERE ts >= TIMESTAMP '{DATA_END}'")
    con.execute(f"""
        CREATE OR REPLACE TABLE fcand AS
        SELECT row_number() OVER () AS fid, * FROM (
        WITH mass AS (
            SELECT date_trunc('hour', ts) AS hr FROM link GROUP BY 1 HAVING count(DISTINCT ch) >= {MASS_OUTAGE_CHANNELS}
        )
        SELECT channel_id AS ch, ts, value_text AS kind, NULL::VARCHAR AS sub, is_alarm, false AS mass, ts AS silent_from
        FROM ev WHERE value_text IN {FAULT_TEXTS}
        UNION ALL
        SELECT ch, ts, 'Пропадание связи', NULL, NULL, date_trunc('hour', ts) IN (SELECT hr FROM mass), prev_ts FROM link
        UNION ALL
        SELECT ch, ts, 'Сбой значения', sub, is_alarm, false, ts
        FROM read_parquet('{(BASE / 'gaps').as_posix()}/vf*.parquet') WHERE sub IN ('date_1970', 'gas_anomaly', 'sentinel_alarm'))
    """)
    print(f"fault candidates: {time.time() - t0:.0f} s", flush=True)
    mark_group_silence(con)
    con.execute(f"""
        COPY (SELECT f.*, grp_onset OR grp_day AS grp, (o.start IS NOT NULL) AS in_outage FROM fcand f
              LEFT JOIN outg o ON f.ts >= o.start AND f.ts < o."end")
        TO '{(BASE / 'faults.parquet').as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    print(f"faults: {time.time() - t0:.0f} s", flush=True)
    print(con.sql(f"""SELECT kind, sub, year(ts) y, count(*) n, count(*) FILTER (WHERE mass) mass,
                             count(*) FILTER (WHERE grp) grp, count(DISTINCT ch) chans
                      FROM '{(BASE / 'faults.parquet').as_posix()}' WHERE ts >= '{GRID_FROM}' AND ts < '2026-01-01'
                      GROUP BY ALL ORDER BY kind, sub, y""").df().to_string())


def mark_group_silence(con: duckdb.DuckDBPyConnection) -> None:
    """Групповая многодневная тишина. «Долгая тишина» канала — пауза длиннее его нормы и не короче GROUP_MIN_H.
    Отказ канала c (любого вида) в момент x относится к групповому эпизоду, если в том же объекте (или в родительском
    объекте) долгую тишину, начавшуюся не дальше GROUP_TOL_H от начала тишины канала c (для статусов — от момента
    сообщения), держат ≥ GROUP_MIN_CH каналов того же типа
    и это ≥ GROUP_MIN_SHARE активных однотипных каналов (с событиями за 30 дней)."""
    norm = f"read_parquet('{(BASE / 'daily_norm.parquet').as_posix()}')"
    con.execute(f"""
        CREATE OR REPLACE TABLE ls AS
        SELECT l.ch, l.prev_ts, l.ts_end, c.object_id AS oid, o.parent_id AS pid, c.sensor_type AS st
        FROM link l JOIN chn c ON c.id = l.ch JOIN obj o ON o.id = c.object_id
        WHERE l.gap_eff_s >= {GROUP_MIN_H} * 3600
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE act AS
        SELECT n.day, c.object_id AS oid, o.parent_id AS pid, c.sensor_type AS st, count(*) AS n_act
        FROM {norm} n JOIN chn c ON c.id = n.ch JOIN obj o ON o.id = c.object_id
        WHERE n.n_ev_30d > 0 GROUP BY GROUPING SETS ((n.day, c.object_id, o.parent_id, c.sensor_type), (n.day, o.parent_id, c.sensor_type))
    """)
    con.execute("""
        CREATE OR REPLACE TABLE fx AS
        SELECT f.fid, f.ch, f.silent_from AS ts, c.object_id AS oid, o.parent_id AS pid, c.sensor_type AS st
        FROM fcand f JOIN chn c ON c.id = f.ch JOIN obj o ON o.id = c.object_id
    """)
    for lvl, key in (("o", "oid"), ("p", "pid")):
        con.execute(f"""
            CREATE OR REPLACE TABLE g_{lvl} AS
            SELECT fx.fid, count(DISTINCT ls.ch) AS n_sil
            FROM fx JOIN ls ON ls.{key} = fx.{key} AND ls.st = fx.st
                 AND ls.prev_ts BETWEEN fx.ts - INTERVAL {GROUP_TOL_H} HOUR AND fx.ts + INTERVAL {GROUP_TOL_H} HOUR
            GROUP BY fx.fid
        """)
    con.execute(f"""
        CREATE OR REPLACE TABLE fgrp AS
        SELECT fx.fid, coalesce(go.n_sil, 0) AS n_sil_obj, ao.n_act AS n_act_obj,
               coalesce(gpp.n_sil, 0) AS n_sil_par, ap.n_act AS n_act_par
        FROM fx
        LEFT JOIN g_o go ON go.fid = fx.fid
        LEFT JOIN g_p gpp ON gpp.fid = fx.fid
        LEFT JOIN act ao ON ao.day = fx.ts::DATE AND ao.oid = fx.oid AND ao.st = fx.st
        LEFT JOIN act ap ON ap.day = fx.ts::DATE AND ap.oid IS NULL AND ap.pid = fx.pid AND ap.st = fx.st
    """)
    # Второе условие — «объект замолк целиком»: в день отказа ≥ GROUP_MIN_SHARE активных однотипных каналов объекта
    # (или родителя), но не меньше GROUP_MIN_CH, не прислали ни одного сообщения, и таких дней подряд ≥ 2
    # (многодневная тишина). Ловит постепенный демонтаж (начала тишины разнесены на несколько дней). Отказ относится к
    # эпизоду, если такой день — день отказа или следующий (в день начала тишины каналы ещё успевают прислать сообщения).
    con.execute(f"""
        CREATE OR REPLACE TABLE zday AS
        WITH d AS (
            SELECT n.day, c.object_id AS oid, o.parent_id AS pid, c.sensor_type AS st,
                   -- только каналы, которые обычно шлют ≥ GROUP_DAILY_MIN_EV сообщений в сутки: у редко шлющих (дым,
                   -- извещатели) день без сообщений — норма, а не тишина
                   count(*) FILTER (WHERE n.n_ev_30d >= 30 * {GROUP_DAILY_MIN_EV}) AS n_act,
                   count(*) FILTER (WHERE n.n_ev_30d >= 30 * {GROUP_DAILY_MIN_EV} AND n.n_ev = 0) AS n_zero
            FROM {norm} n JOIN chn c ON c.id = n.ch JOIN obj o ON o.id = c.object_id
            WHERE n.day NOT IN (SELECT DISTINCT unnest(range(start::DATE, "end"::DATE + 1, INTERVAL 1 DAY))::DATE FROM outg
                                WHERE epoch("end" - start) >= 12 * 3600)
            GROUP BY GROUPING SETS ((n.day, c.object_id, o.parent_id, c.sensor_type), (n.day, o.parent_id, c.sensor_type))
        ), z AS (SELECT * FROM d WHERE n_zero >= {GROUP_MIN_CH} AND n_zero >= {GROUP_MIN_SHARE} * n_act)
        SELECT z.* FROM z WHERE EXISTS (SELECT 1 FROM z z2 WHERE z2.oid IS NOT DISTINCT FROM z.oid AND z2.pid = z.pid
                                         AND z2.st = z.st AND abs(z2.day - z.day) = 1)
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE fcand AS
        SELECT f.* EXCLUDE (fid), g.n_sil_obj, g.n_act_obj, g.n_sil_par, g.n_act_par,
               coalesce((g.n_sil_obj >= {GROUP_MIN_CH} AND g.n_sil_obj >= {GROUP_MIN_SHARE} * g.n_act_obj)
                     OR (g.n_sil_par >= {GROUP_MIN_CH} AND g.n_sil_par >= {GROUP_MIN_SHARE} * g.n_act_par), false) AS grp_onset,
               EXISTS (SELECT 1 FROM zday z WHERE z.day IN (f.ts::DATE, f.ts::DATE + 1) AND z.st = fx.st
                       AND ((z.oid = fx.oid) OR (z.oid IS NULL AND z.pid = fx.pid))) AS grp_day
        FROM fcand f JOIN fx USING (fid) LEFT JOIN fgrp g USING (fid)
    """)


def main() -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    con = connect()
    if "--norms-only" not in sys.argv:
        build_hourly_and_gaps(con)
    build_outages(con)
    build_norms_and_faults(con)


if __name__ == "__main__":
    main()
