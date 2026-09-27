"""Загрузка реальных данных в PostgreSQL бэкенда (COPY, без построчных INSERT).

Что грузится (с флагом --replace предварительно очищаются демо-данные этих таблиц; пользователи,
причины, рекомендации и журнал аудита не трогаются; демо-решения и заявки удаляются — они ссылались на демо-прогнозы):
  objects, channels                  — из справочников (data/parquet/dim)
  channel_daily                      — суточные агрегаты по каналам (из data/base/hourly) за --daily-from … конец данных
  events_recent                      — события за последние --recent-days дней данных (из Parquet-журнала)
  channel_faults                     — отказы разметки ML (Неисправен, Отключено устройство, Пропадание связи) с --daily-from
  predictions, prediction_outcomes   — из data/predictions/*.parquet: каждый час с --hourly-from, раньше — раз в --step-hours
  model_metrics, model_thresholds    — из data/models/<версия>/metrics.json

Запуск: python -m db.load_postgres --replace [--dsn postgresql://collector:change-me@localhost:5432/collector]
Только отказы (без перезаливки остального): python -m db.load_postgres --only faults

Замена модели (v3) без потери решений, заявок, пользователей, журнала аудита и настроек:
  python -m db.load_postgres --only model --version lgbm-2026.09-v3
Меняются только прогнозы, исходы, отказы разметки (channel_faults) и метрики модели. Прогнозы, на которые ссылаются
решения или заявки, не удаляются и не меняются (решение принималось по ним); v3 для того же (канал, момент) тогда не
пишется. Счётчики решений, заявок, пользователей, аудита и настроек до и после сверяются — при расхождении откат.
Перед запуском — резервная копия базы (deploy/backup.sh или deploy/backup.ps1).
"""

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features.labels import fault_filter  # noqa: E402
from ml_paths import BASE, DATA, DIM, EVENTS, MODELS, PRED  # noqa: E402

PRESERVED = ("decisions", "work_orders", "users", "audit_log", "app_settings")
DEFAULT_DSN = os.environ.get("PG_DSN", "postgresql://collector:change-me@127.0.0.1:5432/collector")  # 127.0.0.1: «localhost» на Windows ждёт IPv6 ≈ 2 мин
DATA_END = "2026-07-01"


def copy_query(con: duckdb.DuckDBPyConnection, cur: psycopg.Cursor, table: str, cols: list[str], sql: str) -> int:
    """DuckDB-запрос → временный CSV → COPY в PostgreSQL."""
    with tempfile.TemporaryDirectory(dir=DATA) as tmp:
        path = Path(tmp) / f"{table}.csv"
        con.execute(f"COPY ({sql}) TO '{path.as_posix()}' (FORMAT CSV, HEADER false, NULL '\\N')")
        with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN (FORMAT csv, NULL '\\N')") as cp, open(path, "rb") as f:
            while chunk := f.read(1 << 20):
                cp.write(chunk)
    return cur.rowcount


def load_faults(con: duckdb.DuckDBPyConnection, cur: psycopg.Cursor, d_from: str, label: str | None = None) -> int:
    """Отказы из разметки ML → channel_faults (для отметок на графике карточки датчика). label — вариант метки v3:
    тогда только отказы метки (без массовых сбоев, групповой тишины и дней без данных)."""
    cur.execute("DELETE FROM channel_faults")
    where = f"AND {fault_filter(label)}" if label else ""
    return copy_query(con, cur, "channel_faults", ["channel_id", "ts", "kind"],
                      f"""SELECT DISTINCT ch, ts, kind FROM '{(BASE / 'faults.parquet').as_posix()}'
                          WHERE ts >= DATE '{d_from}' AND ts < DATE '{DATA_END}' {where}
                            AND ch IN (SELECT id FROM '{(DIM / 'channels.parquet').as_posix()}')""")


def counts(cur: psycopg.Cursor, tables=PRESERVED + ("predictions", "prediction_outcomes", "channel_faults")) -> dict:
    out = {}
    for t in tables:
        cur.execute(f"SELECT count(*) FROM {t}")
        out[t] = cur.fetchone()[0]
    return out


def replace_model(con: duckdb.DuckDBPyConnection, cur: psycopg.Cursor, version: str, metrics: dict, pred_sql: str,
                  daily_from: str, label: str | None = "b", with_faults: bool = True) -> dict:
    """Замена прогнозов модели: всё в одной транзакции вызывающего кода. Возвращает счётчики до и после."""
    before = counts(cur)
    # прогнозы, на которые ссылаются решения и заявки, остаются как есть
    cur.execute("""
        CREATE TEMP TABLE keep_pred ON COMMIT DROP AS
        SELECT prediction_id AS id FROM decisions UNION SELECT prediction_id FROM work_orders WHERE prediction_id IS NOT NULL""")
    cur.execute("SELECT count(*) FROM keep_pred")
    kept = cur.fetchone()[0]
    cur.execute("DELETE FROM prediction_outcomes WHERE prediction_id NOT IN (SELECT id FROM keep_pred)")
    cur.execute("DELETE FROM predictions WHERE id NOT IN (SELECT id FROM keep_pred)")
    cur.execute("SELECT channel_id, at, horizon_h FROM predictions")
    kept_keys = pd.DataFrame(cur.fetchall(), columns=["channel_id", "at", "horizon_h"])
    con.register("kept_keys", kept_keys)
    cur.execute("SELECT coalesce(max(id), 0) FROM predictions")
    id0 = cur.fetchone()[0]
    con.execute(f"""
        CREATE OR REPLACE TABLE p AS
        SELECT {id0} + row_number() OVER (ORDER BY "at", channel_id) AS id, x.*
        FROM ({pred_sql}) x
        WHERE NOT EXISTS (SELECT 1 FROM kept_keys k WHERE k.channel_id = x.channel_id AND k."at" = x."at"
                                                     AND k.horizon_h = x.horizon_h)
    """)
    # вторичные индексы снимаются на время COPY и строятся заново (уникальное ограничение остаётся)
    cur.execute("""SELECT 'DROP INDEX ' || quote_ident(indexname), indexdef FROM pg_indexes
                   WHERE tablename = 'predictions' AND indexname LIKE 'ix_%'""")
    idx = cur.fetchall()
    for drop, _ in idx:
        cur.execute(drop)
    if "kind_probs" not in [r[0] for r in con.execute("DESCRIBE p").fetchall()]:
        con.execute("ALTER TABLE p ADD COLUMN kind_probs VARCHAR")  # прогнозы v2 (откат) — без видов отказа
    n = copy_query(con, cur, "predictions",
                   ["id", "channel_id", "at", "horizon_h", "prob", "health", "risk_level", "top_factors", "kind_probs",
                    "model_version"],
                   'SELECT id, channel_id, "at", horizon_h, prob, health, risk_level, top_factors, kind_probs, model_version FROM p')
    n2 = copy_query(con, cur, "prediction_outcomes", ["prediction_id", "happened", "fault_at", "fault_kind", "labeled_at"],
                    'SELECT id, happened = 1, fault_at, fault_kind, "at" + INTERVAL 24 HOUR FROM p WHERE happened IS NOT NULL')
    cur.execute("SELECT setval(pg_get_serial_sequence('predictions', 'id'), (SELECT coalesce(max(id), 1) FROM predictions))")
    for _, create in idx:
        cur.execute(create)
    nf = load_faults(con, cur, daily_from, label) if with_faults else None
    cur.execute("DELETE FROM model_metrics WHERE model_version = %s", (version,))
    cur.execute("DELETE FROM model_thresholds WHERE model_version = %s", (version,))
    load_metrics(cur, metrics, version)
    # рабочая версия — одна: у прежних версий метрики и пороги остаются для истории, но не «выбраны»
    cur.execute("UPDATE model_thresholds SET is_selected = false WHERE model_version <> %s", (version,))
    after = counts(cur)
    changed = {t: (before[t], after[t]) for t in PRESERVED if before[t] != after[t]}
    if changed:
        raise RuntimeError(f"Изменились таблицы, которые должны сохраниться: {changed} — откат")
    return {"before": before, "after": after, "kept_predictions": kept, "predictions": n, "outcomes": n2, "faults": nf}


def load_metrics(cur: psycopg.Cursor, metrics: dict, v: str) -> None:
    tf, tt = metrics["test_period"]
    rows = []
    o, b = metrics["overall"], metrics["baseline"]
    rows.append(("overall", "", o["precision"], o["recall"], o["f1"], o["pr_auc"], o["support"], None))
    rows.append(("baseline", "", b["precision"], b["recall"], b["f1"], None, b["support"], None))
    for m in metrics["by_sensor_type"]:
        rows.append(("sensor_type", m["sensor_type"], m["precision"], m["recall"], m["f1"], m["pr_auc"], m["support"], None))
    for k, val in metrics["precision_at_k"].items():
        rows.append(("precision_at_k", str(k), None, None, None, None, None, val))
    rows.append(("lead_time", "", None, None, None, None, None, metrics["median_lead_time_h"]))
    # v3: Recall по видам отказа, метрики по группам заказчика, сравнение с v2 на той же метке
    for m in metrics.get("recall_by_fault_kind", []):
        rows.append(("fault_kind", m["fault_kind"], None, m["recall"], None, None, m["support"], None))
    for g, m in (metrics.get("by_group") or {}).items():
        rows.append(("sensor_group", g, m["precision"], m["recall"], m["f1"], m["pr_auc"], m["support"], m.get("alerts_per_day")))
    for label, res in (metrics.get("compare") or {}).items():
        for model in ("v2", "v3"):
            m = res[model]
            rows.append(("compare", f"{model}@{label}", m["precision"], m["recall"], m["f1"], m["pr_auc"], m["support"],
                         m.get("alerts_per_day")))
    for k in ("episodes_warned_1_24h", "alerts_per_day", "fp_per_day"):
        if metrics.get(k) is not None:
            rows.append(("dispatcher", k, None, None, None, None, None, metrics[k]))
    cur.executemany(
        "INSERT INTO model_metrics (model_version, scope, key, precision, recall, f1, pr_auc, support, value, period_from, period_to) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        [(v, *r, date.fromisoformat(tf), date.fromisoformat(tt)) for r in rows])
    cur.executemany(
        "INSERT INTO model_thresholds (model_version, threshold, precision, recall, alerts_per_day, tp_per_day, fp_per_day, is_selected) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [(v, r["threshold"], r["precision"], r["recall"], r["alerts_per_day"], r["tp_per_day"], r["fp_per_day"], bool(r["is_selected"]))
         for r in metrics["thresholds"]])


def pred_select(args) -> str:
    return f"""
        SELECT * FROM read_parquet('{PRED.as_posix()}/2*.parquet', union_by_name=true)
        WHERE "at" >= TIMESTAMP '{args.pred_from}' AND "at" < TIMESTAMP '{args.pred_to}'
          AND ("at" >= TIMESTAMP '{args.hourly_from}' OR hour("at") % {args.step_hours} = 0)"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--version", default="lgbm-2026.09-v3")
    ap.add_argument("--replace", action="store_true", help="очистить демо-данные перед загрузкой")
    ap.add_argument("--pred-from", default="2026-01-01")
    ap.add_argument("--pred-to", default=DATA_END)
    ap.add_argument("--hourly-from", default="2026-06-01", help="с этой даты — каждый час, раньше — шаг --step-hours")
    ap.add_argument("--step-hours", type=int, default=6)
    ap.add_argument("--daily-from", default="2025-10-01")
    ap.add_argument("--recent-days", type=int, default=30)
    ap.add_argument("--only", choices=["faults", "model"],
                    help="faults — только отказы; model — замена прогнозов, исходов, отказов и метрик модели "
                         "без потери решений, заявок, пользователей, аудита и настроек")
    args = ap.parse_args()

    t_all = time.time()
    con = duckdb.connect()
    con.execute("SET threads=8")
    # память DuckDB ограничена: рядом работают PostgreSQL и Docker (без лимита DuckDB берёт до 80 % ОЗУ, и 26.09
    # загрузку v3 остановила нехватка памяти); лишнее уходит во временные файлы на диск
    con.execute(f"SET memory_limit='{os.environ.get('LOAD_DUCKDB_MEMORY', '6GB')}'")
    con.execute(f"SET temp_directory='{(DATA / 'duckdb_tmp').as_posix()}'")
    con.execute("SET preserve_insertion_order=false")
    metrics = json.loads((MODELS / args.version / "metrics.json").read_text(encoding="utf-8"))

    with psycopg.connect(args.dsn, autocommit=False) as pg, pg.cursor() as cur:
        if args.only == "faults":
            t = time.time()
            n = load_faults(con, cur, args.daily_from, "b" if args.version.endswith("v3") else None)
            pg.commit()
            print(f"channel_faults {n:,}: {time.time() - t:.0f} s")
            return
        if args.only == "model":
            t = time.time()
            res = replace_model(con, cur, args.version, metrics, pred_select(args), args.daily_from,
                                "b" if args.version.endswith("v3") else None)
            pg.commit()
            cur.execute("ANALYZE predictions; ANALYZE prediction_outcomes; ANALYZE channel_faults")
            print(json.dumps(res, ensure_ascii=False, default=str, indent=1))
            print(f"готово за {time.time() - t:.0f} s")
            return
        if args.replace:
            cur.execute("""
                DELETE FROM decisions; DELETE FROM work_orders; DELETE FROM prediction_outcomes; DELETE FROM predictions;
                DELETE FROM events_recent; DELETE FROM channel_daily; DELETE FROM channel_faults; DELETE FROM model_metrics; DELETE FROM model_thresholds;
                DELETE FROM channels; DELETE FROM objects;
            """)
            print("демо-данные очищены", flush=True)

        t = time.time()
        # objects: сначала уровни 1, 2, 3 — из-за внешнего ключа на родителя
        n = copy_query(con, cur, "objects", ["id", "level", "parent_id", "kind", "name"],
                       f"SELECT id, level, parent_id, kind, name FROM '{(DIM / 'objects.parquet').as_posix()}' ORDER BY level")
        n2 = copy_query(con, cur, "channels",
                        ["id", "object_id", "system_type", "sensor_type", "tag", "tag_l1", "tag_l2", "tag_l3", "tag_l4", "tag_l5", "name"],
                        f"""SELECT id, object_id, system_type, sensor_type, tag, tag_l1, tag_l2, tag_l3, tag_l4, tag_l5, name
                            FROM '{(DIM / 'channels.parquet').as_posix()}'""")
        print(f"objects {n}, channels {n2}: {time.time() - t:.0f} s", flush=True)
        load_metrics(cur, metrics, args.version)
        print("model_metrics, model_thresholds", flush=True)

        t = time.time()
        n = copy_query(con, cur, "channel_daily",
                       ["channel_id", "day", "events_count", "alarm_count", "fault_count", "uncertain_count", "power_off_count",
                        "status_changes", "value_avg", "value_min", "value_max", "max_gap_min"],
                       f"""SELECT ch, hour::DATE AS day, sum(n_ev), sum(n_alarm), sum(n_fault + n_disc), sum(n_unc), sum(n_off),
                                  sum(n_chg), round(sum(s_v) / nullif(sum(n_num), 0), 4), min(v_min), max(v_max),
                                  (max(max_gap_s) / 60)::INT
                           FROM read_parquet('{(BASE / 'hourly').as_posix()}/*.parquet')
                           WHERE hour >= DATE '{args.daily_from}' AND hour < DATE '{DATA_END}'
                             AND ch IN (SELECT id FROM '{(DIM / 'channels.parquet').as_posix()}')
                           GROUP BY ALL""")
        print(f"channel_daily {n:,}: {time.time() - t:.0f} s", flush=True)

        t = time.time()
        n = copy_query(con, cur, "events_recent",
                       ["source_event_id", "channel_id", "ts", "is_alarm", "value_raw", "value_num", "value_text"],
                       f"""SELECT event_id, channel_id, ts, is_alarm, left(value_raw, 128), value_num, left(value_text, 128)
                           FROM read_parquet('{EVENTS.as_posix()}/**/*.parquet', hive_partitioning=true)
                           WHERE ts >= DATE '{DATA_END}' - INTERVAL {args.recent_days} DAY AND ts < DATE '{DATA_END}'
                             AND channel_id IN (SELECT id FROM '{(DIM / 'channels.parquet').as_posix()}')""")
        print(f"events_recent {n:,}: {time.time() - t:.0f} s", flush=True)

        t = time.time()
        n = load_faults(con, cur, args.daily_from)
        print(f"channel_faults {n:,}: {time.time() - t:.0f} s", flush=True)

        t = time.time()
        # вторичные индексы и уникальное ограничение снимаются на время COPY и строятся заново (в разы быстрее)
        cur.execute("""
            SELECT 'DROP INDEX ' || quote_ident(indexname), indexdef FROM pg_indexes
            WHERE tablename = 'predictions' AND indexname LIKE 'ix_%'""")
        idx = cur.fetchall()
        cur.execute("""
            SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
            WHERE conrelid = 'predictions'::regclass AND contype = 'u'""")
        uniq = cur.fetchall()
        for drop, _ in idx:
            cur.execute(drop)
        for name, _ in uniq:
            cur.execute(f"ALTER TABLE predictions DROP CONSTRAINT {name}")
        # прогнозы: явные id, чтобы исходы ссылались на них без обратного чтения
        con.execute(f"""
            CREATE TABLE p AS
            SELECT row_number() OVER (ORDER BY "at", channel_id) AS id, *
            FROM read_parquet('{PRED.as_posix()}/2*.parquet', union_by_name=true)
            WHERE "at" >= TIMESTAMP '{args.pred_from}' AND "at" < TIMESTAMP '{args.pred_to}'
              AND ("at" >= TIMESTAMP '{args.hourly_from}' OR hour("at") % {args.step_hours} = 0)
        """)
        n = copy_query(con, cur, "predictions",
                       ["id", "channel_id", "at", "horizon_h", "prob", "health", "risk_level", "top_factors", "model_version"],
                       'SELECT id, channel_id, "at", horizon_h, prob, health, risk_level, top_factors, model_version FROM p')
        n2 = copy_query(con, cur, "prediction_outcomes", ["prediction_id", "happened", "fault_at", "fault_kind", "labeled_at"],
                        'SELECT id, happened = 1, fault_at, fault_kind, "at" + INTERVAL 24 HOUR FROM p WHERE happened IS NOT NULL')
        cur.execute("SELECT setval(pg_get_serial_sequence('predictions', 'id'), (SELECT coalesce(max(id), 1) FROM predictions))")
        for name, definition in uniq:
            cur.execute(f"ALTER TABLE predictions ADD CONSTRAINT {name} {definition}")
        for _, create in idx:
            cur.execute(create)
        print(f"predictions {n:,}, outcomes {n2:,}: {time.time() - t:.0f} s", flush=True)

        pg.commit()
        cur.execute("ANALYZE")
    print(f"готово за {time.time() - t_all:.0f} s")


if __name__ == "__main__":
    main()
