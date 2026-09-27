"""Замена прогнозов модели в PostgreSQL (db.load_postgres.replace_model): решения, заявки, пользователи, журнал аудита
и настройки не теряются; прогнозы, на которые ссылаются решения и заявки, остаются как были.

Нужна тестовая база (НЕ основная): PG_TEST_DSN=postgresql://collector:change-me@127.0.0.1:5432/collector_test
(127.0.0.1, а не localhost: на Windows «localhost» сначала пробует IPv6 и ждёт ≈ 2 мин на подключение).
Схема создаётся миграциями бэкенда (alembic upgrade head), таблицы очищаются в начале теста.
Запуск: cd ml; $env:PG_TEST_DSN="..."; .venv/Scripts/python -m pytest -q tests/test_load_postgres.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

psycopg = pytest.importorskip("psycopg")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.load_postgres import replace_model  # noqa: E402

DSN = os.environ.get("PG_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="PG_TEST_DSN не задан")
BACKEND = Path(__file__).resolve().parents[2] / "backend"

METRICS = {
    "test_period": ["2026-01-01", "2026-06-29"],
    "overall": {"precision": 0.7, "recall": 0.57, "f1": 0.63, "pr_auc": 0.62, "support": 10},
    "baseline": {"precision": 0.1, "recall": 0.2, "f1": 0.13, "support": 10},
    "by_sensor_type": [{"sensor_type": "Газовый датчик", "precision": 0.6, "recall": 0.3, "f1": 0.4, "pr_auc": 0.5, "support": 3}],
    "precision_at_k": {"20": 0.9}, "median_lead_time_h": 9.7,
    "recall_by_fault_kind": [{"fault_kind": "Сбой значения", "support": 2, "recall": 0.5}],
    "by_group": {"газовые": {"precision": 0.6, "recall": 0.3, "f1": 0.4, "pr_auc": 0.5, "support": 3, "alerts_per_day": 5.0}},
    "compare": {"b": {"v2": {"precision": 0.46, "recall": 0.63, "f1": 0.54, "pr_auc": 0.5, "support": 10, "alerts_per_day": 200},
                      "v3": {"precision": 0.7, "recall": 0.57, "f1": 0.63, "pr_auc": 0.62, "support": 10, "alerts_per_day": 120}}},
    "thresholds": [{"threshold": 0.45, "precision": 0.7, "recall": 0.57, "alerts_per_day": 120, "tp_per_day": 80,
                    "fp_per_day": 40, "is_selected": True}],
}


@pytest.fixture()
def pg():
    env = dict(os.environ, DATABASE_URL=DSN.replace("postgresql://", "postgresql+psycopg://"))
    py = BACKEND / ".venv" / "Scripts" / "python.exe"
    subprocess.run([str(py if py.exists() else sys.executable), "-m", "alembic", "upgrade", "head"], cwd=BACKEND,
                   env=env, check=True, capture_output=True)
    with psycopg.connect(DSN, autocommit=False) as conn, conn.cursor() as cur:
        cur.execute("""TRUNCATE decisions, work_orders, prediction_outcomes, predictions, channel_faults, model_metrics,
                       model_thresholds, audit_log, app_settings, users, reasons, channels, objects RESTART IDENTITY CASCADE""")
        cur.execute("INSERT INTO objects (id, level, kind, name) VALUES (1, 3, 'подобъект', 'объект Тест')")
        cur.execute("""INSERT INTO channels (id, object_id, system_type, sensor_type, tag, name) VALUES
                       (10, 1, 'Газовая охрана', 'Газовый датчик', '1-1.1.', 'Газ 1'),
                       (11, 1, 'Газовая охрана', 'Газовый датчик', '1-1.2.', 'Газ 2')""")
        cur.execute("""INSERT INTO predictions (id, channel_id, at, horizon_h, prob, health, risk_level, top_factors, model_version)
                       VALUES (1, 10, '2026-06-15 12:00', 24, 0.9, 10, 'critical', '[]', 'lgbm-2026.09-v2'),
                              (2, 11, '2026-06-15 12:00', 24, 0.2, 80, 'normal', '[]', 'lgbm-2026.09-v2'),
                              (3, 11, '2026-06-15 13:00', 24, 0.3, 70, 'attention', '[]', 'lgbm-2026.09-v2')""")
        cur.execute("SELECT setval(pg_get_serial_sequence('predictions', 'id'), 3)")
        cur.execute("INSERT INTO prediction_outcomes (prediction_id, happened) VALUES (1, true), (2, false), (3, false)")
        cur.execute("""INSERT INTO reasons (id, code, name, sort_order, is_active) VALUES (1, 'x', 'Причина', 1, true)""")
        cur.execute("""INSERT INTO users (username, full_name, role, auth_source, is_active)
                       VALUES ('disp', 'Диспетчер', 'dispatcher', 'local', true)""")
        cur.execute("""INSERT INTO decisions (prediction_id, decision_type, reason_id, created_at)
                       VALUES (1, 'dispatch', 1, '2026-06-15 12:05')""")
        cur.execute("""INSERT INTO work_orders (number, channel_id, object_id, status, priority, created_at, updated_at, prediction_id)
                       VALUES ('ЗН-2026-000001', 10, 1, 'draft', 'high', '2026-06-15 12:06', '2026-06-15 12:06', 1)""")
        cur.execute("INSERT INTO audit_log (ts, action) VALUES ('2026-06-15 12:05', 'decision')")
        cur.execute("INSERT INTO app_settings (key, value, updated_at) VALUES ('risk_bounds', '{}', now())")
        cur.execute("""INSERT INTO model_thresholds (model_version, threshold, precision, recall, alerts_per_day, tp_per_day,
                       fp_per_day, is_selected) VALUES ('lgbm-2026.09-v2', 0.345, 0.62, 0.70, 199, 124, 76, true)""")
        conn.commit()
        yield conn, cur


def test_replace_model_preserves_decisions_and_orders(pg):
    conn, cur = pg
    con = duckdb.connect()
    kp = json.dumps({"link": 0.8, "fault": 0.05, "disconnected": 0.0, "value": 0.0})
    pred_sql = f"""
        SELECT * FROM (VALUES
          (10, TIMESTAMP '2026-06-15 12:00', 24, 0.85, 15, 'critical', '[]', '{kp}', 'lgbm-2026.09-v3', 1, TIMESTAMP '2026-06-15 20:00', 'Пропадание связи'),
          (11, TIMESTAMP '2026-06-15 12:00', 24, 0.10, 90, 'normal',   '[]', NULL,   'lgbm-2026.09-v3', 0, NULL, NULL),
          (11, TIMESTAMP '2026-06-15 13:00', 24, 0.12, 88, 'normal',   '[]', NULL,   'lgbm-2026.09-v3', NULL, NULL, NULL)
        ) t(channel_id, "at", horizon_h, prob, health, risk_level, top_factors, kind_probs, model_version, happened, fault_at, fault_kind)"""
    res = replace_model(con, cur, "lgbm-2026.09-v3", METRICS, pred_sql, "2025-10-01", label=None, with_faults=False)
    conn.commit()
    # решение и заявка по-прежнему ссылаются на прогноз 1 — он не изменился (решение принималось по нему)
    cur.execute("SELECT id, prob, model_version FROM predictions WHERE id = 1")
    assert cur.fetchone() == (1, 0.9, "lgbm-2026.09-v2")
    cur.execute("SELECT prediction_id FROM decisions")
    assert cur.fetchall() == [(1,)]
    cur.execute("SELECT prediction_id, number FROM work_orders")
    assert cur.fetchall() == [(1, "ЗН-2026-000001")]
    assert res["kept_predictions"] == 1
    # остальные — v3, без дубля для (канал 10, 12:00)
    cur.execute("SELECT channel_id, at, model_version, kind_probs FROM predictions WHERE id <> 1 ORDER BY channel_id, at")
    rows = cur.fetchall()
    assert [(r[0], r[2]) for r in rows] == [(11, "lgbm-2026.09-v3"), (11, "lgbm-2026.09-v3")]
    assert res["predictions"] == 2 and res["outcomes"] == 1
    for t in ("decisions", "work_orders", "users", "audit_log", "app_settings"):
        assert res["before"][t] == res["after"][t]
    cur.execute("SELECT count(*) FROM model_metrics WHERE model_version = 'lgbm-2026.09-v3' AND scope IN ('compare', 'sensor_group', 'fault_kind')")
    assert cur.fetchone()[0] == 4
    cur.execute("SELECT DISTINCT model_version FROM model_thresholds WHERE is_selected")
    assert cur.fetchall() == [("lgbm-2026.09-v3",)]
    # повторный запуск идемпотентен
    res2 = replace_model(con, cur, "lgbm-2026.09-v3", METRICS, pred_sql, "2025-10-01", label=None, with_faults=False)
    conn.commit()
    assert res2["after"]["predictions"] == res["after"]["predictions"]


def test_rollback_to_v2_predictions_without_kind_probs(pg):
    """Откат на v2: у её прогнозов нет столбца kind_probs — загружается NULL."""
    conn, cur = pg
    con = duckdb.connect()
    pred_sql = """SELECT * FROM (VALUES (11, TIMESTAMP '2026-06-16 12:00', 24, 0.3, 70, 'attention', '[]', 'lgbm-2026.09-v2',
                                          0, NULL::TIMESTAMP, NULL::VARCHAR))
                  t(channel_id, "at", horizon_h, prob, health, risk_level, top_factors, model_version, happened, fault_at, fault_kind)"""
    res = replace_model(con, cur, "lgbm-2026.09-v2", METRICS, pred_sql, "2025-10-01", label=None, with_faults=False)
    conn.commit()
    assert res["predictions"] == 1
    cur.execute("SELECT kind_probs FROM predictions WHERE channel_id = 11")
    assert cur.fetchall() == [(None,)]
