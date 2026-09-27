"""Варианты метки для одних и тех же срезов «канал × сутки» (сравнение на валидации, docs/ML_V3_PLAN.md).

Все варианты строятся из data/base_v3/faults.parquet по одним правилам: y = 1, если в (T, T + 24 ч] есть отказ;
eligible = нет отказа в (T − 72 ч, T] («только новые отказы») и горизонт закрыт в данных.

  a  — виды v2 («Неисправен», «Отключено устройство», «Пропадание связи») + исправления данных v3:
       без часов массового сбоя (≥ 100 каналов), групповой тишины и дней без данных;
  b  — a + «Сбой значения»: значение «01.01.1970 …» и аномальный газ (вне [0; 100] % или ≥ 5 % без флага
       «тревожное») — ответ заказчика 23.09: «01.01.1970 — сбой, считать неисправностью; отрицательные и
       аномальные значения газа — неисправность»;
  c  — b + служебные коды (-127, -3276, 255, …) с флагом «тревожное» — таблица экспертов 17–18.09:
       «исключить, только если нет признака "тревожное"».
Метка v2 (старая) берётся как есть из data/features/daily (y, eligible модели lgbm-2026.09-v2).

Запуск: python -m features.labels  → data/features_v3/labels_daily.parquet
"""

import sys
import time
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import BASE, DATA, FEAT, FEAT_V2  # noqa: E402

DATA_END = "2026-07-01 00:00:00"
HORIZON_H, EXCLUDE_H = 24, 72
BASE_KINDS = "kind IN ('Неисправен', 'Отключено устройство', 'Пропадание связи')"
VARIANTS = {
    "a": f"{BASE_KINDS}",
    "b": f"({BASE_KINDS} OR (kind = 'Сбой значения' AND sub IN ('date_1970', 'gas_anomaly')))",
    "c": f"({BASE_KINDS} OR kind = 'Сбой значения')",
}
CLEAN = "NOT mass AND NOT grp AND NOT in_outage"


def fault_filter(variant: str) -> str:
    return f"{VARIANTS[variant]} AND {CLEAN}"


def outage_slices_sql() -> str:
    """Срезы, у которых окно (T − 24 ч, T + 24 ч] задевает «день без данных»: ни признаки, ни исход не наблюдаемы."""
    return f"""SELECT DISTINCT s.ch, s.T FROM s JOIN '{(BASE / 'outages.parquet').as_posix()}' o
               ON o.start < s.T + INTERVAL {HORIZON_H} HOUR AND o."end" > s.T - INTERVAL 24 HOUR"""


def label_sql(variant: str, slices: str = "s") -> str:
    f = f"(SELECT ch, ts, kind FROM '{(BASE / 'faults.parquet').as_posix()}' WHERE {fault_filter(variant)})"
    return f"""
        WITH nx AS (SELECT s.ch, s.T, x.ts AS nts, x.kind AS nkind FROM {slices} s ASOF LEFT JOIN {f} x ON x.ch = s.ch AND s.T < x.ts),
             lx AS (SELECT s.ch, s.T, x.ts AS lts FROM {slices} s ASOF LEFT JOIN {f} x ON x.ch = s.ch AND s.T >= x.ts)
        SELECT nx.ch, nx.T,
               CASE WHEN nx.T + INTERVAL {HORIZON_H} HOUR <= TIMESTAMP '{DATA_END}'
                    THEN (nts IS NOT NULL AND nts <= nx.T + INTERVAL {HORIZON_H} HOUR)::INT END AS y_{variant},
               CASE WHEN nts <= nx.T + INTERVAL {HORIZON_H} HOUR THEN nkind END AS kind_{variant},
               CASE WHEN nts <= nx.T + INTERVAL {HORIZON_H} HOUR THEN nts END AS at_{variant},
               (lts IS NULL OR lts <= nx.T - INTERVAL {EXCLUDE_H} HOUR) AS elig_{variant}
        FROM nx JOIN lx USING (ch, T)"""


def main() -> None:
    t0 = time.time()
    con = duckdb.connect()
    con.execute("SET threads=28")
    con.execute("SET memory_limit='20GB'")
    con.execute(f"SET temp_directory='{(DATA / 'duckdb_tmp').as_posix()}'")
    # срезы v3 ∪ v2 (у v3 активных каналов чуть больше: значения-даты теперь тоже события)
    con.execute(f"""CREATE TABLE s AS
        SELECT ch, T FROM read_parquet('{(FEAT / 'daily').as_posix()}/*.parquet')
        UNION SELECT ch, T FROM read_parquet('{(FEAT_V2 / 'daily').as_posix()}/*.parquet')""")
    con.execute(f"CREATE TABLE outs AS {outage_slices_sql()}")
    parts = []
    for v in VARIANTS:
        con.execute(f"CREATE TABLE l_{v} AS {label_sql(v)}")
        parts.append(f"l_{v}")
    joins = " ".join(f"LEFT JOIN {p} USING (ch, T)" for p in parts[1:])
    out = FEAT / "labels_daily.parquet"
    con.execute(f"""
        COPY (
            SELECT {parts[0]}.*, {", ".join(f"{p}.* EXCLUDE (ch, T)" for p in parts[1:])},
                   v2.y AS y_v2, v2.eligible AS elig_v2, v2.fault_kind AS kind_v2, v2.fault_at AS at_v2,
                   (o.ch IS NOT NULL) AS outage_slice
            FROM {parts[0]} {joins}
            LEFT JOIN read_parquet('{(FEAT_V2 / 'daily').as_posix()}/*.parquet') v2 USING (ch, T)
            LEFT JOIN outs o USING (ch, T)
        ) TO '{out.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """)
    print(f"{out}: {time.time() - t0:.0f} s")
    print(con.sql(f"""
        SELECT year(T) y, (month(T) > 6)::INT + 1 AS half, count(*) n,
               {", ".join(f"sum(y_{v}) FILTER (WHERE elig_{v} AND NOT outage_slice) pos_{v}" for v in list(VARIANTS) + ['v2'])}
        FROM '{out.as_posix()}' WHERE T < '2026-01-01' GROUP BY ALL ORDER BY 1, 2""").df().to_string())


if __name__ == "__main__":
    main()
