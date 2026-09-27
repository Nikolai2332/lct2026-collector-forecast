"""Правило групповой тишины: подбор параметров по обучающему периоду и внешняя проверка по датам графика ППР 2026.

  python -m features.group_check tune    # распределение доли молчащих однотипных соседей (2023-01 … 2025-06)
  python -m features.group_check ppr     # эпизоды групповой тишины газовых датчиков в 2026 vs окна графика ППР

Даты 2026 года используются ТОЛЬКО для проверки правила разметки (совпадают ли окна групповой тишины с окнами
демонтажа датчиков метана по графику ППР), без расчёта каких-либо метрик модели и без подстройки параметров.
Результат — data/experiments/group_*.json и таблицы в docs/ML_V3_PLAN.md.
"""

import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.build_base import GROUP_MIN_CH, GROUP_MIN_SHARE  # noqa: E402
from ml_paths import BASE, DATA, DIM  # noqa: E402

EXP = DATA / "experiments"
EXP.mkdir(parents=True, exist_ok=True)
# Файл заказчика (вне git); путь — переменная окружения PPR_XLSX
PPR_XLSX = Path(os.environ.get("PPR_XLSX", DATA / "customer" / "График ППР АКМ на 2026г. РЭК.xlsx"))
# Окна первого полугодия 2026 из графика ППР (демонтаж датчиков метана → вывоз из ОМ), «Объект N», шт.
PPR_2026 = [
    ("Объекты 1–2", 56 + 13, "2026-01-12", "2026-01-22"),
    ("Объект 3", 62, "2026-01-29", "2026-02-09"),
    ("Объект 4", 47, "2026-02-13", "2026-02-24"),
    ("Объект 5", 102, "2026-03-02", "2026-03-13"),
    ("Объекты 6–7", 34 + 12, "2026-03-23", "2026-04-03"),
    ("Объект 8", 101, "2026-04-09", "2026-04-17"),
    ("Объект 9", 40, "2026-04-23", "2026-05-07"),
    ("Объекты 10–13", 6 + 8 + 21 + 21, "2026-05-15", "2026-05-26"),
    ("Объект 14", 33, "2026-06-04", "2026-06-18"),
]


def con_() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET threads=28")
    con.execute(f"CREATE VIEW f AS SELECT * FROM '{(BASE / 'faults.parquet').as_posix()}'")
    con.execute(f"CREATE VIEW chn AS SELECT * FROM '{(DIM / 'channels.parquet').as_posix()}'")
    return con


def tune() -> None:
    con = con_()
    q = """
        SELECT CASE WHEN c.system_type = 'Газовая охрана' THEN 'газ' ELSE 'прочие' END AS grp_type, f.kind,
               CASE WHEN n_act_obj IS NULL OR n_act_obj = 0 THEN 'нет' ELSE
                    CASE WHEN n_sil_obj / n_act_obj < 0.1 THEN '0–10 %' WHEN n_sil_obj / n_act_obj < 0.25 THEN '10–25 %'
                         WHEN n_sil_obj / n_act_obj < 0.5 THEN '25–50 %' WHEN n_sil_obj / n_act_obj < 0.75 THEN '50–75 %'
                         ELSE '75–100 %' END END AS share_obj,
               count(*) n, count(*) FILTER (WHERE n_sil_obj >= 5) n_ge5
        FROM f JOIN chn c ON c.id = f.ch
        WHERE f.ts >= '2023-01-01' AND f.ts < '2025-06-30' AND NOT f.mass AND f.kind IN ('Пропадание связи', 'Неисправен')
        GROUP BY ALL ORDER BY 1, 2, 3"""
    df = con.sql(q).df()
    print(df.to_string())
    # Отказы, отнесённые к групповой тишине, по годам и видам (обучающий период + валидация)
    by = con.sql("""
        SELECT year(ts) y, kind, count(*) n, count(*) FILTER (WHERE grp) grp, round(avg(grp::INT), 4) share
        FROM f WHERE ts >= '2023-01-01' AND ts < '2026-01-01' AND NOT mass GROUP BY ALL ORDER BY 2, 1""").df()
    print(by.to_string())
    (EXP / "group_tune.json").write_text(json.dumps({"share_hist": df.to_dict("records"), "by_year": by.to_dict("records"),
                                                     "min_share": GROUP_MIN_SHARE, "min_ch": GROUP_MIN_CH},
                                                    ensure_ascii=False, indent=1, default=float), encoding="utf-8")


def ppr() -> None:
    """Окна групповой тишины газовых датчиков 2026 года по объектам (только даты и число каналов) против графика ППР.
    День объекта «молчит», если в этот день нет ни одного сообщения у ≥ GROUP_MIN_SHARE его газовых каналов, активных
    за 30 дней до дня (и таких каналов ≥ GROUP_MIN_CH). Подряд идущие такие дни — одно окно."""
    con = con_()
    con.execute(f"CREATE VIEW dn AS SELECT * FROM '{(BASE / 'daily_norm.parquet').as_posix()}'")
    win = con.sql(f"""
        WITH d AS (
            SELECT c.object_id, n.day, count(*) FILTER (WHERE n.n_ev_30d > 0) AS n_act,
                   count(*) FILTER (WHERE n.n_ev_30d > 0 AND n.n_ev = 0) AS n_sil
            FROM dn n JOIN chn c ON c.id = n.ch
            WHERE c.sensor_type = 'Газовый датчик' AND n.day >= DATE '2026-01-01' AND n.day < DATE '2026-07-01'
              AND n.day <> DATE '2026-06-01'   -- день без данных во всём журнале
            GROUP BY ALL
        ), s AS (
            SELECT *, day - to_days(row_number() OVER (PARTITION BY object_id ORDER BY day)::INT) AS run
            FROM d WHERE n_sil >= {GROUP_MIN_CH} AND n_sil >= {GROUP_MIN_SHARE} * n_act
        )
        SELECT object_id, min(day) AS first_day, max(day) AS last_day, count(*) AS days,
               max(n_sil) AS silent, max(n_act) AS active
        FROM s GROUP BY object_id, run HAVING count(*) >= 2 ORDER BY first_day""").df()
    grp = con.sql("""
        SELECT c.object_id, f.ts::DATE AS day, count(*) AS n FROM f JOIN chn c ON c.id = f.ch
        WHERE f.grp AND c.sensor_type = 'Газовый датчик' AND f.ts >= '2026-01-01' AND f.ts < '2026-07-01' GROUP BY ALL""").df()
    rows = []
    for _, e in win.iterrows():
        a, b = pd.Timestamp(e.first_day), pd.Timestamp(e.last_day)
        g = grp[(grp.object_id == e.object_id) & (pd.to_datetime(grp.day) >= a - pd.Timedelta(days=2))
                & (pd.to_datetime(grp.day) <= b)]
        match = []
        for name, n, pa, pb in PPR_2026:
            pa, pb = pd.Timestamp(pa), pd.Timestamp(pb)
            if a <= pb and b >= pa:
                match.append(f"{name} ({n} шт., {pa:%d.%m}–{pb:%d.%m}): начало {(a - pa).days:+d} дн., конец {(b - pb).days:+d} дн.")
        rows.append({"object_id": int(e.object_id), "from": f"{a:%d.%m}", "to": f"{b:%d.%m}", "days": int(e.days),
                     "silent_of_active": f"{int(e.silent)} из {int(e.active)}", "group_faults": int(g.n.sum()),
                     "ppr_match": "; ".join(match) or "—"})
    res = pd.DataFrame(rows)
    print(res.to_string())
    covered = []
    for name, n, pa, pb in PPR_2026:
        hit = res[[name in r for r in res.ppr_match]]
        covered.append({"ppr": name, "sensors": n, "from": pa, "to": pb,
                        "silent_windows": "; ".join(f"{r.object_id}: {r['from']}–{r['to']} ({r.silent_of_active})"
                                                    for _, r in hit.iterrows()) or "—"})
    cov = pd.DataFrame(covered)
    print(cov.to_string())
    (EXP / "group_ppr2026.json").write_text(json.dumps({"windows": rows, "ppr": covered}, ensure_ascii=False, indent=1,
                                                       default=str), encoding="utf-8")


if __name__ == "__main__":
    {"tune": tune, "ppr": ppr}[sys.argv[1]]()
