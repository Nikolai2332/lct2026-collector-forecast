"""Срезы «канал × момент T», признаки и метка.

Признаки считаются только по событиям строго до T: почасовые агрегаты (часы < T, то есть события < T при
T в начале часа) → кумулятивные суммы по каналу → окно [T − w, T) = cum(T) − cum(T − w) через ASOF JOIN.
Без циклов по каналам.

Метка y (v3, вариант ML_LABEL, по умолчанию «b» — см. features/labels.py и docs/ML_V3_PLAN.md): у канала есть отказ
(«Неисправен», «Отключено устройство», «Пропадание связи», «Сбой значения») в интервале (T, T + 24 ч]; без часов
массового сбоя, групповой тишины (плановые работы) и дней без данных.
Исключаются (в поле eligible = false, для обучения и метрик): отказ в последние 72 ч до T (включая T),
каналы без событий за 30 дней до T, срезы, чьё окно (T − 24 ч, T + 24 ч] задевает день без данных.
В прогнозы для интерфейса попадают все активные каналы.

Запуск:
  python -m features.build_features daily                   # срезы 00:00, 2023-01-01 … 2026-06-29
  python -m features.build_features hourly --from 2026-01-01 --to 2026-07-01   # почасовые, по месяцам
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.labels import fault_filter  # noqa: E402
from ml_paths import BASE, DATA, DIM, FEAT  # noqa: E402

LABEL = os.environ.get("ML_LABEL", "b")
DATA_END = datetime(2026, 7, 1)
HORIZON_H = 24
EXCLUDE_H = 72

# Окна в часах
WIN = {"1h": 1, "6h": 6, "24h": 24, "7d": 168, "30d": 720, "90d": 2160}
CUM_COLS = ["ev", "fault", "disc", "unc", "off", "alarm", "txt", "chg", "num", "sv", "sv2", "same",
            "sent", "date", "d1970", "g1wk", "g1off", "g5", "gdet"]
CUM_SRC = ["n_ev", "n_fault", "n_disc", "n_unc", "n_off", "n_alarm", "n_txt", "n_chg", "n_num",
           "coalesce(s_v, 0)", "coalesce(s_v2, 0)", "n_same",
           "n_sent", "n_date", "n_date1970", "n_gas1_wk", "n_gas1_off", "n_gas5", "n_gasdet"]
KINDS = {"lnk": "Пропадание связи", "flt": "Неисправен", "dsc": "Отключено устройство", "vf": "Сбой значения"}


WORK_DB = DATA / "work" / "features_v3.duckdb"


def connect(path: Path | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(path) if path else ":memory:")
    con.execute("SET threads=28")
    con.execute("SET memory_limit='22GB'")
    con.execute(f"SET temp_directory='{(DATA / 'duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    return con


def prepare(con: duckdb.DuckDBPyConnection) -> None:
    """Кумулятивные таблицы по каналам и объектам (в памяти DuckDB)."""
    t0 = time.time()
    con.execute(f"CREATE OR REPLACE TABLE chn AS SELECT * FROM '{(DIM / 'channels.parquet').as_posix()}'")
    con.execute(f"CREATE OR REPLACE TABLE obj AS SELECT * FROM '{(DIM / 'objects.parquet').as_posix()}'")
    con.execute(f"CREATE OR REPLACE TABLE dn AS SELECT * FROM '{(BASE / 'daily_norm.parquet').as_posix()}'")
    con.execute(f"CREATE OR REPLACE TABLE outg AS SELECT * FROM '{(BASE / 'outages.parquet').as_posix()}'")
    con.execute(f"""
        CREATE OR REPLACE TABLE cum AS
        SELECT ch, hour, last_ts,
               {", ".join(f"sum({src}) OVER w AS c_{c}" for c, src in zip(CUM_COLS, CUM_SRC))}
        FROM read_parquet('{(BASE / 'hourly').as_posix()}/b*.parquet')
        WHERE hour >= TIMESTAMP '2022-07-01'
        WINDOW w AS (PARTITION BY ch ORDER BY hour ROWS UNBOUNDED PRECEDING)
    """)
    # Отказы по метке (без массовых сбоев, групповой тишины и дней без данных): кумулятивные счётчики по видам
    con.execute(f"""
        CREATE OR REPLACE TABLE flt AS
        SELECT ch, ts, kind,
               count(*) OVER w AS c_all,
               {", ".join(f"sum((kind = '{k}')::INT) OVER w AS c_{a}" for a, k in KINDS.items())}
        FROM '{(BASE / 'faults.parquet').as_posix()}'
        WHERE ch IN (SELECT id FROM chn) AND {fault_filter(LABEL)}
        WINDOW w AS (PARTITION BY ch ORDER BY ts RANGE UNBOUNDED PRECEDING)
    """)
    # Эпизоды групповой тишины канала (плановые работы / отключение объекта) — отдельный счётчик, не отказ
    con.execute(f"""
        CREATE OR REPLACE TABLE gflt AS
        SELECT ch, ts, count(*) OVER (PARTITION BY ch ORDER BY ts RANGE UNBOUNDED PRECEDING) AS c_grp
        FROM '{(BASE / 'faults.parquet').as_posix()}' WHERE ch IN (SELECT id FROM chn) AND grp
    """)
    # «История ремонтов» из журнала: восстановление связи после пропадания (от момента отказа до следующего
    # сообщения). Известно только в момент восстановления, поэтому индексируется по времени восстановления.
    con.execute(f"""
        CREATE OR REPLACE TABLE rec AS
        SELECT ch, ts_end AS ts,
               count(*) OVER w AS c_rec, sum(epoch(ts_end - ts) / 3600.0) OVER w AS c_rec_h
        FROM (SELECT l.ch, l.ts, g.ts AS ts_end
              FROM '{(BASE / 'faults.parquet').as_posix()}' l
              JOIN '{(BASE / 'gaps_eff.parquet').as_posix()}' g ON g.ch = l.ch AND l.ts > g.prev_ts AND l.ts < g.ts_end
              WHERE l.kind = 'Пропадание связи' AND {fault_filter(LABEL)} AND g.ts IS NOT NULL)
        WINDOW w AS (PARTITION BY ch ORDER BY ts_end RANGE UNBOUNDED PRECEDING)
    """)
    # Парк: отказы по типу датчика (все объекты) — для нормировки на текущее поведение парка/типа
    con.execute("""
        CREATE OR REPLACE TABLE tflt AS
        SELECT c.sensor_type AS st, f.ts,
               count(*) OVER w AS c_type, sum((f.kind = 'Неисправен')::INT) OVER w AS c_type_flt
        FROM flt f JOIN chn c ON c.id = f.ch
        WINDOW w AS (PARTITION BY c.sensor_type ORDER BY f.ts RANGE UNBOUNDED PRECEDING)
    """)
    # Соседи: отказы по объекту и по родительскому объекту; обесточивание фаз объекта
    con.execute("""
        CREATE OR REPLACE TABLE oflt AS
        SELECT c.object_id AS oid, f.ts, count(*) OVER (PARTITION BY c.object_id ORDER BY f.ts RANGE UNBOUNDED PRECEDING) AS c_obj
        FROM flt f JOIN chn c ON c.id = f.ch
    """)
    con.execute("""
        CREATE OR REPLACE TABLE pflt AS
        SELECT o.parent_id AS pid, f.ts, count(*) OVER (PARTITION BY o.parent_id ORDER BY f.ts RANGE UNBOUNDED PRECEDING) AS c_par
        FROM flt f JOIN chn c ON c.id = f.ch JOIN obj o ON o.id = c.object_id
    """)
    con.execute("""
        CREATE OR REPLACE TABLE ophase AS
        SELECT oid, hour, sum(n_off) OVER w AS c_off, sum(n_ev) OVER w AS c_ev FROM (
            SELECT c.object_id AS oid, h.hour, sum(h.c_off_h) AS n_off, sum(h.n_ev) AS n_ev
            FROM (SELECT ch, hour, c_off - coalesce(lag(c_off) OVER (PARTITION BY ch ORDER BY hour), 0) AS c_off_h,
                         c_ev - coalesce(lag(c_ev) OVER (PARTITION BY ch ORDER BY hour), 0) AS n_ev FROM cum) h
            JOIN chn c ON c.id = h.ch WHERE c.sensor_type = 'Состояние фазы' GROUP BY ALL)
        WINDOW w AS (PARTITION BY oid ORDER BY hour ROWS UNBOUNDED PRECEDING)
    """)
    # Суточная макс. пауза: скользящий максимум за 7 и 30 дней (по полным дням до дня T)
    # «Пульс» канала по полным суткам до дня T: дней без сообщений, стабильность суточного числа сообщений,
    # длина истории (у каналов с короткой историей личная норма ненадёжна)
    con.execute("""
        CREATE OR REPLACE TABLE dnw AS
        SELECT ch, day, norm_s, n_ev_30d,
               max(max_gap_s) OVER w7 AS max_gap_7d,
               max(max_gap_s) OVER w30 AS max_gap_30d,
               sum((n_ev = 0)::INT) OVER w30 AS zero_days_30d,
               stddev_pop(n_ev) OVER w30 / nullif(avg(n_ev) OVER w30, 0) AS daily_cv_30d,
               least(count(*) OVER (PARTITION BY ch ORDER BY day ROWS UNBOUNDED PRECEDING) - 1, 365) AS history_days
        FROM dn
        WINDOW w7 AS (PARTITION BY ch ORDER BY day ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING),
               w30 AS (PARTITION BY ch ORDER BY day ROWS BETWEEN 30 PRECEDING AND 1 PRECEDING)
    """)
    con.execute("""
        CREATE OR REPLACE TABLE tact AS
        SELECT d.day, c.sensor_type AS st, count(*) AS n_act FROM dnw d JOIN chn c ON c.id = d.ch
        WHERE d.n_ev_30d > 0 GROUP BY ALL
    """)
    print(f"prepare: {time.time() - t0:.0f} s, cum rows {con.execute('SELECT count(*) FROM cum').fetchone()[0]:,}", flush=True)


def slices_sql(mode: str, d_from: date, d_to: date, at: datetime | None = None) -> str:
    """Срезы: каналы из справочника, у которых были события за 30 дней до дня T.
    mode: daily — 00:00 каждого дня; hourly — каждый час; at — один момент `at` (начало часа)."""
    if mode == "at":
        return f"""
            SELECT d.ch, TIMESTAMP '{at:%Y-%m-%d %H:00:00}' AS T FROM dnw d
            WHERE d.day = DATE '{at:%Y-%m-%d}' AND d.n_ev_30d > 0 AND d.ch IN (SELECT id FROM chn)
        """
    hours = "range(0, 1)" if mode == "daily" else "range(0, 24)"
    return f"""
        SELECT d.ch, d.day::TIMESTAMP + to_hours(h.hh) AS T
        FROM dnw d, (SELECT unnest({hours}) AS hh) h
        WHERE d.day >= DATE '{d_from}' AND d.day < DATE '{d_to}' AND d.n_ev_30d > 0
          AND d.ch IN (SELECT id FROM chn)
    """


def build(con: duckdb.DuckDBPyConnection, mode: str, d_from: date | None, d_to: date | None, out: Path,
          at: datetime | None = None) -> int:
    t0 = time.time()
    con.execute(f"CREATE OR REPLACE TEMP TABLE s AS {slices_sql(mode, d_from, d_to, at)}")
    # Кумулятивы на T и на T − w: одна ASOF JOIN по «длинной» таблице (срез × окно), затем разворот.
    # Цепочка из отдельных ASOF JOIN на каждое окно в DuckDB в десятки раз медленнее.
    hs = [0] + list(WIN.values())
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE q AS
        SELECT s.ch, s.T, w.h, s.T - to_hours(w.h) AS tq FROM s, (SELECT unnest({hs}) AS h) w
    """)
    # ASOF отдельно от агрегатов: в одном запросе с разворотом DuckDB строит медленный план
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE r AS
        SELECT q.ch, q.T, q.h, c.last_ts, {", ".join(f"c.c_{c}" for c in CUM_COLS)}
        FROM q ASOF LEFT JOIN cum c ON c.ch = q.ch AND q.tq > c.hour
    """)
    # Разворот соединениями по окнам (агрегаты с FILTER в DuckDB здесь на порядок медленнее)
    wins = [("0", 0)] + list(WIN.items())
    sel = ", ".join(f"coalesce(r{n}.c_{c}, 0) AS {c}_{n}" for n, _ in wins for c in CUM_COLS)
    joins = " ".join(f"JOIN r r{n} ON r{n}.ch = r0.ch AND r{n}.T = r0.T AND r{n}.h = {h}" for n, h in wins[1:])
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE sc AS
        SELECT r0.ch, r0.T, r0.last_ts, {sel} FROM r r0 {joins} WHERE r0.h = 0
    """)
    con.execute("DROP TABLE r")

    def w(col: str, win: str) -> str:
        return f"({col}_0 - {col}_{win})"

    feats = []
    ev30 = f"greatest({w('ev', '30d')}, 1)"
    # Активность: число событий и отношение к норме за 30 дней
    for win, h in [("1h", 1), ("6h", 6), ("24h", 24), ("7d", 168), ("30d", 720)]:
        feats.append(f"{w('ev', win)} AS ev_{win}")
        if win != "30d":
            feats.append(f"{w('ev', win)} / ({ev30} * {h} / 720.0) AS ev_ratio_{win}")
    feats.append(f"{w('ev', '90d')} AS ev_90d")
    # Статусы: сообщения «Неисправен» / «Отключено устройство», доли «Неопределен», «Обесточен», тревожных; дребезг
    for win in ["1h", "6h", "24h", "7d", "30d", "90d"]:
        feats.append(f"{w('fault', win)} AS fault_msgs_{win}")
    for win in ["24h", "7d", "30d", "90d"]:
        feats.append(f"{w('disc', win)} AS disc_msgs_{win}")
    for win in ["24h", "7d", "30d", "90d"]:
        den = f"greatest({w('ev', win)}, 1)"
        feats.append(f"{w('unc', win)} / {den} AS unc_share_{win}")
        feats.append(f"{w('off', win)} / {den} AS off_share_{win}")
        feats.append(f"{w('alarm', win)} / {den} AS alarm_share_{win}")
        feats.append(f"{w('chg', win)} AS status_changes_{win}")
        feats.append(f"{w('txt', win)} AS txt_{win}")
    # Числовые: среднее, разброс, тренд, залипание, выход за обычный диапазон
    for win in ["24h", "7d", "30d"]:
        n = f"nullif({w('num', win)}, 0)"
        feats.append(f"{w('num', win)} AS num_{win}")
        feats.append(f"{w('sv', win)} / {n} AS v_mean_{win}")
        feats.append(f"sqrt(greatest({w('sv2', win)} / {n} - pow({w('sv', win)} / {n}, 2), 0)) AS v_std_{win}")
        feats.append(f"{w('same', win)} / {n} AS stuck_share_{win}")
    # v3: служебные значения и значения-даты (ранний сигнал сбоя), газ ≥ 1 % (в рабочее время будней 9–14 — обычно
    # плановые проверки баллонами — и вне его), ≥ 5 %, «Обнаружен газ»
    for win in ["24h", "7d", "30d"]:
        feats.append(f"{w('sent', win)} AS sentinel_{win}")
        feats.append(f"{w('d1970', win)} AS date1970_{win}")
    feats.append(f"{w('sent', '7d')} / greatest({w('ev', '7d')}, 1) AS sentinel_share_7d")
    feats.append(f"{w('date', '7d')} - {w('d1970', '7d')} AS date_current_7d")
    for win in ["24h", "7d"]:
        feats.append(f"{w('g1wk', win)} AS gas1_work_{win}")
        feats.append(f"{w('g1off', win)} AS gas1_offhours_{win}")
        feats.append(f"{w('gdet', win)} AS gas_detected_{win}")
    for win in ["7d", "30d"]:
        feats.append(f"{w('g5', win)} AS gas5_{win}")
    # Время с последнего события (без часов «дней без данных» — это тишина журнала, а не датчика)
    outage = " + ".join(f"greatest(epoch(least(s.T, TIMESTAMP '{e}') - greatest(s.last_ts, TIMESTAMP '{st}')), 0)"
                        for st, e in con.execute('SELECT start, "end" FROM outg').fetchall()) or "0"
    feats.append(f"(epoch(s.T - s.last_ts) - ({outage})) / 3600.0 AS hours_since_event")
    base = f"SELECT s.ch, s.T, {', '.join(feats)} FROM sc AS s"
    con.execute(f"CREATE OR REPLACE TEMP TABLE f1 AS {base}")
    con.execute("DROP TABLE sc")

    # Отказы канала (все виды), соседи по объекту и родителю, фазы объекта — по длинной таблице окон
    fw = [("0", 0), ("24h", 24), ("7d", 168), ("30d", 720), ("90d", 2160)]
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE qf AS
        SELECT f1.ch, f1.T, c.object_id AS oid, ob.parent_id AS pid, w.h, f1.T - to_hours(w.h) AS tq
        FROM f1 JOIN chn c ON c.id = f1.ch JOIN obj ob ON ob.id = c.object_id,
             (SELECT unnest({[h for _, h in fw]}) AS h) w
    """)
    kcols = ["all"] + list(KINDS)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE fa AS
        SELECT q.ch, q.T,
               {", ".join(f"coalesce(max(f.c_{k}) FILTER (WHERE q.h = {h}), 0) AS f{k}_{n}" for n, h in fw for k in kcols)},
               max(f.ts) FILTER (WHERE q.h = 0) AS last_fault_ts
        FROM qf q ASOF LEFT JOIN flt f ON f.ch = q.ch AND q.tq >= f.ts GROUP BY q.ch, q.T
    """)
    for tbl, cols in (("gflt", ["c_grp"]), ("rec", ["c_rec", "c_rec_h"])):
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE x_{tbl} AS
            SELECT q.ch, q.T, {", ".join(f"coalesce(max(x.{c}) FILTER (WHERE q.h = {h}), 0) AS {c}_{n}"
                                         for n, h in [("0", 0), ("30d", 720), ("90d", 2160)] for c in cols)}
            FROM (SELECT * FROM qf WHERE h IN (0, 720, 2160)) q ASOF LEFT JOIN {tbl} x ON x.ch = q.ch AND q.tq >= x.ts
            GROUP BY q.ch, q.T
        """)
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE tf AS
        SELECT q.ch, q.T, {", ".join(f"coalesce(max(x.c_type) FILTER (WHERE q.h = {h}), 0) AS ty_{n}, "
                                     f"coalesce(max(x.c_type_flt) FILTER (WHERE q.h = {h}), 0) AS tyf_{n}"
                                     for n, h in [("0", 0), ("24h", 24), ("7d", 168), ("90d", 2160)])}
        FROM (SELECT qf.*, c.sensor_type AS st FROM qf JOIN chn c ON c.id = qf.ch WHERE h IN (0, 24, 168, 2160)) q
        ASOF LEFT JOIN tflt x ON x.st = q.st AND q.tq >= x.ts GROUP BY q.ch, q.T
    """)
    ow = [("0", 0), ("24h", 24), ("7d", 168)]
    for tbl, key, col, alias, cmp_, tcol in [("oflt", "oid", "c_obj", "ob", ">=", "ts"), ("pflt", "pid", "c_par", "pa", ">=", "ts"),
                                             ("ophase", "oid", "c_off", "po", ">", "hour"), ("ophase", "oid", "c_ev", "pe", ">", "hour")]:
        con.execute(f"""
            CREATE OR REPLACE TEMP TABLE {alias} AS
            SELECT q.ch, q.T, {", ".join(f"coalesce(max(x.{col}) FILTER (WHERE q.h = {h}), 0) AS {alias}_{n}" for n, h in ow)}
            FROM (SELECT * FROM qf WHERE h IN {tuple(h for _, h in ow)}) q
            ASOF LEFT JOIN {tbl} x ON x.{key} = q.{key} AND q.tq {cmp_} x.{tcol} GROUP BY q.ch, q.T
        """)
    con.execute("""
        CREATE OR REPLACE TEMP TABLE nx AS
        SELECT f1.ch, f1.T, x.ts AS next_fault_ts, x.kind AS next_fault_kind
        FROM f1 ASOF LEFT JOIN flt x ON x.ch = f1.ch AND f1.T < x.ts
    """)
    fsel = []
    for n, _ in fw[1:]:
        fsel.append(f"fa.fall_0 - fa.fall_{n} AS faults_{n}")
        fsel.append(f"fa.flnk_0 - fa.flnk_{n} AS link_losses_{n}")
    for n in ("30d", "90d"):
        fsel.append(f"fa.fflt_0 - fa.fflt_{n} AS fault_episodes_{n}")
        fsel.append(f"fa.fdsc_0 - fa.fdsc_{n} AS disc_episodes_{n}")
        fsel.append(f"fa.fvf_0 - fa.fvf_{n} AS value_failures_{n}")
        fsel.append(f"xg.c_grp_0 - xg.c_grp_{n} AS group_silences_{n}")
    fsel.append("xr.c_rec_0 - xr.c_rec_90d AS recoveries_90d")
    fsel.append("(xr.c_rec_h_0 - xr.c_rec_h_90d) / nullif(xr.c_rec_0 - xr.c_rec_90d, 0) AS recovery_h_90d")
    for n in ("24h", "7d"):
        fsel.append(f"(tf.ty_0 - tf.ty_{n}) / greatest(ta.n_act, 1) AS type_fault_rate_{n}")
    fsel.append("(tf.tyf_0 - tf.tyf_7d) / greatest(ta.n_act, 1) AS type_fault_msg_rate_7d")
    fsel.append("((tf.ty_0 - tf.ty_7d) / 7.0) / greatest((tf.ty_0 - tf.ty_90d) / 90.0, 1) AS type_fault_trend_7d_90d")
    for n, _ in ow[1:]:
        fsel.append(f"(ob.ob_0 - ob.ob_{n}) - (fa.fall_0 - fa.fall_{n}) AS obj_faults_{n}")
        fsel.append(f"pa.pa_0 - pa.pa_{n} AS parent_faults_{n}")
        fsel.append(f"(po.po_0 - po.po_{n}) / greatest(pe.pe_0 - pe.pe_{n}, 1) AS obj_phase_off_share_{n}")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE f2 AS
        SELECT f1.*,
               {", ".join(fsel)},
               epoch(f1.T - fa.last_fault_ts) / 86400.0 AS days_since_fault,
               fa.last_fault_ts, nx.next_fault_ts, nx.next_fault_kind,
               d.norm_s / 3600.0 AS norm_gap_h,
               f1.hours_since_event / (d.norm_s / 3600.0) AS gap_to_norm,
               -- норма на день последнего события (та же, что в разметке пропадания связи; считана до этого дня)
               dl.norm_s / 3600.0 AS norm_last_h,
               dl.norm_s / 3600.0 - f1.hours_since_event AS hours_to_norm,
               d.max_gap_7d / 3600.0 AS max_gap_7d_h, d.max_gap_30d / 3600.0 AS max_gap_30d_h,
               d.zero_days_30d, d.daily_cv_30d, d.history_days,
               c.sensor_type, c.system_type, c.object_id, ob2.parent_id,
               c.tag_depth, try_cast(c.tag_l2 AS INT) AS tag_l2, try_cast(c.tag_l3 AS INT) AS tag_l3,
               c.has_pk::INT AS has_pk, c.has_ans::INT AS has_ans, c.has_shield::INT AS has_shield, c.has_vsh::INT AS has_vsh,
               hour(f1.T) AS cal_hour, isodow(f1.T) AS cal_dow, month(f1.T) AS cal_month, (isodow(f1.T) >= 6)::INT AS cal_weekend
        FROM f1
        JOIN chn c ON c.id = f1.ch
        JOIN obj ob2 ON ob2.id = c.object_id
        JOIN dnw d ON d.ch = f1.ch AND d.day = f1.T::DATE
        LEFT JOIN dnw dl ON dl.ch = f1.ch AND dl.day = (f1.T - to_microseconds((f1.hours_since_event * 3600e6)::BIGINT))::DATE
        JOIN fa ON fa.ch = f1.ch AND fa.T = f1.T
        JOIN ob ON ob.ch = f1.ch AND ob.T = f1.T
        JOIN pa ON pa.ch = f1.ch AND pa.T = f1.T
        JOIN po ON po.ch = f1.ch AND po.T = f1.T
        JOIN pe ON pe.ch = f1.ch AND pe.T = f1.T
        JOIN nx ON nx.ch = f1.ch AND nx.T = f1.T
        JOIN x_gflt xg ON xg.ch = f1.ch AND xg.T = f1.T
        JOIN x_rec xr ON xr.ch = f1.ch AND xr.T = f1.T
        JOIN tf ON tf.ch = f1.ch AND tf.T = f1.T
        LEFT JOIN tact ta ON ta.day = f1.T::DATE AND ta.st = c.sensor_type
    """)
    for t in ("qf", "fa", "ob", "pa", "po", "pe", "nx", "q", "x_gflt", "x_rec", "tf"):
        con.execute(f"DROP TABLE {t}")
    con.execute("DROP TABLE f1")
    # Отклонение от медианы однотипных соседей по объекту (в тот же момент T); метка; исключения
    con.execute(f"""
        COPY (
            SELECT *,
                   ev_ratio_24h - median(ev_ratio_24h) OVER nb AS nb_dev_ev_ratio_24h,
                   v_mean_24h - median(v_mean_24h) OVER nb AS nb_dev_v_mean_24h,
                   unc_share_24h - median(unc_share_24h) OVER nb AS nb_dev_unc_share_24h,
                   count(*) OVER nb - 1 AS nb_same_type,
                   -- доля однотипных соседей по объекту, которые уже молчат дольше нормы (групповая тишина)
                   (sum((hours_to_norm < 0)::INT) OVER nb - coalesce((hours_to_norm < 0)::INT, 0))
                     / greatest(count(*) OVER nb - 1, 1) AS nb_silent_share,
                   CASE WHEN T + INTERVAL {HORIZON_H} HOUR <= TIMESTAMP '{DATA_END}'
                        THEN (next_fault_ts IS NOT NULL AND next_fault_ts <= T + INTERVAL {HORIZON_H} HOUR)::INT END AS y,
                   CASE WHEN next_fault_ts <= T + INTERVAL {HORIZON_H} HOUR THEN next_fault_ts END AS fault_at,
                   CASE WHEN next_fault_ts <= T + INTERVAL {HORIZON_H} HOUR THEN next_fault_kind END AS fault_kind,
                   (last_fault_ts IS NULL OR last_fault_ts <= T - INTERVAL {EXCLUDE_H} HOUR)
                     AND NOT EXISTS (SELECT 1 FROM outg o WHERE o.start < f2.T + INTERVAL {HORIZON_H} HOUR
                                                            AND o."end" > f2.T - INTERVAL 24 HOUR) AS eligible
            FROM f2
            WINDOW nb AS (PARTITION BY object_id, sensor_type, T)
        ) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    n = con.execute(f"SELECT count(*) FROM '{out.as_posix()}'").fetchone()[0]
    con.execute("DROP TABLE f2; DROP TABLE s")
    print(f"{out.name}: {n:,} rows, {time.time() - t0:.0f} s", flush=True)
    return n


def month_ranges(d_from: date, d_to: date):
    d = d_from
    while d < d_to:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield d, min(nxt, d_to)
        d = nxt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["daily", "hourly", "chunk"])
    ap.add_argument("--from", dest="d_from", default=None)
    ap.add_argument("--to", dest="d_to", default=None)
    ap.add_argument("--skip-existing", action="store_true", help="не пересчитывать готовые куски")
    args = ap.parse_args()
    if args.mode == "daily":
        con = connect()
        prepare(con)
        d_from = date.fromisoformat(args.d_from or "2023-01-01")
        d_to = date.fromisoformat(args.d_to or "2026-06-30")
        out_dir = FEAT / "daily"
        out_dir.mkdir(parents=True, exist_ok=True)
        # помесячно: годовые куски уходят в спилл и считаются в 2–3 раза дольше
        for a, b in month_ranges(d_from, d_to):
            build(con, "daily", a, b, out_dir / f"{a:%Y-%m}.parquet")
    elif args.mode == "chunk":  # один почасовой кусок; запускается подпроцессом из режима hourly
        con = connect(WORK_DB)
        a, b = date.fromisoformat(args.d_from), date.fromisoformat(args.d_to)
        build(con, "hourly", a, b, FEAT / "hourly" / f"{a:%Y-%m-%d}.parquet")
    else:
        d_from = date.fromisoformat(args.d_from or "2026-01-01")
        d_to = date.fromisoformat(args.d_to or "2026-07-01")
        (FEAT / "hourly").mkdir(parents=True, exist_ok=True)
        # Куски по 4 дня, каждый — в отдельном процессе над общим файлом DuckDB с подготовленными таблицами:
        # в одном процессе каждый следующий кусок считается всё дольше (69 → 145 → 191 → 265 с), даже с новым соединением.
        WORK_DB.parent.mkdir(parents=True, exist_ok=True)
        WORK_DB.unlink(missing_ok=True)
        con = connect(WORK_DB)
        prepare(con)
        con.close()
        a = d_from
        while a < d_to:
            b = min(a + timedelta(days=4), date(a.year + (a.month == 12), a.month % 12 + 1, 1), d_to)
            if not (args.skip_existing and (FEAT / "hourly" / f"{a:%Y-%m-%d}.parquet").exists()):
                subprocess.run([sys.executable, "-m", "features.build_features", "chunk", "--from", str(a), "--to", str(b)],
                               check=True, cwd=Path(__file__).resolve().parents[1])
            a = b


if __name__ == "__main__":
    main()
