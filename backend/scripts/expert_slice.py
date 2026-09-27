"""Состав среза реальных данных для экспертов — общий для сборщика и загрузчика (docs/EXPERT_SLICE.md).

Только таблицы данных и справочников: пользователей, паролей, журнала действий, решений и заявок в срезе нет.
Период — прогнозы (с исходами), события и отказы; справочники и метрики модели — целиком.
"""

FORMAT_VERSION = 1
SCHEMA_REVISION = "0007"  # версия схемы в manifest: колонки таблиц среза — как в ревизии 0007
# Ревизии базы, с которыми срез совместим: 0008 добавила только decisions.source, решений в срезе нет
COMPATIBLE_DB_REVISIONS = ("0007", "0008")

# Таблица → (колонки, условие отбора; {date_from}/{date_to} — границы периода, {hist_from} — начало истории)
TABLES: dict[str, tuple[list[str], str]] = {
    "objects": (["id", "level", "parent_id", "kind", "name"], "true"),
    "channels": (["id", "object_id", "system_type", "sensor_type", "tag", "tag_l1", "tag_l2", "tag_l3", "tag_l4",
                  "tag_l5", "name"], "true"),
    "reasons": (["id", "code", "name", "decision_type", "sort_order", "is_active"], "true"),
    "recommendations": (["id", "code", "sensor_type", "text", "is_active"], "true"),
    "model_metrics": (["id", "model_version", "scope", "key", "precision", "recall", "f1", "pr_auc", "support", "value",
                       "period_from", "period_to", "computed_at"], "true"),
    "model_thresholds": (["id", "model_version", "threshold", "precision", "recall", "alerts_per_day", "tp_per_day",
                          "fp_per_day", "is_selected"], "true"),
    # График на карточке — суточные агрегаты за 30 дней до начала периода и сам период
    "channel_daily": (["channel_id", "day", "events_count", "alarm_count", "fault_count", "uncertain_count",
                       "power_off_count", "status_changes", "value_avg", "value_min", "value_max", "max_gap_min"],
                      "day >= '{hist_from}'::date AND day <= '{date_to}'::date"),
    # Рекомендации ТО смотрят историю отказов за 90 дней до среза
    "channel_faults": (["id", "channel_id", "ts", "kind"], "ts >= '{faults_from}' AND ts <= '{date_to_end}'"),
    "events_recent": (["id", "source_event_id", "channel_id", "ts", "is_alarm", "value_raw", "value_num", "value_text"],
                      "ts >= '{date_from}' AND ts <= '{date_to_end}'"),
    "predictions": (["id", "channel_id", "at", "horizon_h", "prob", "health", "risk_level", "top_factors", "kind_probs",
                     "model_version", "created_at"], "at >= '{date_from}' AND at <= '{date_to_end}'"),
    "prediction_outcomes": (["prediction_id", "happened", "fault_at", "fault_kind", "labeled_at"],
                            "prediction_id IN (SELECT id FROM predictions WHERE at >= '{date_from}' AND at <= '{date_to_end}')"),
}
# Порядок загрузки (внешние ключи) и очистки (обратный)
LOAD_ORDER = ["objects", "channels", "reasons", "recommendations", "model_metrics", "model_thresholds", "channel_daily",
              "channel_faults", "events_recent", "predictions", "prediction_outcomes"]
# Очищаются перед загрузкой: данные демо-сида, которые срез заменяет (решения и заявки демо ссылаются на них)
CLEAR = ["prediction_outcomes", "decisions", "work_orders", "predictions", "events_recent", "channel_faults",
         "channel_daily", "model_thresholds", "model_metrics", "user_scopes", "channels", "objects", "recommendations",
         "reasons"]


def manifest_problems(manifest: dict) -> list[str]:
    """Проверка manifest.json до загрузки. Имена таблиц и колонок подставляются в COPY — поэтому они должны
    в точности совпадать с составом TABLES: подменённый архив не может передать в SQL ничего своего."""
    problems = []
    if manifest.get("format") != FORMAT_VERSION or manifest.get("schema_revision") != SCHEMA_REVISION:
        problems.append(f"срез формата {manifest.get('format')} для схемы {manifest.get('schema_revision')}, "
                        f"ожидается {FORMAT_VERSION}/{SCHEMA_REVISION}")
    tables = manifest.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(TABLES):
        return problems + ["состав таблиц в manifest.json не совпадает с ожидаемым"]
    for table, meta in tables.items():
        if not isinstance(meta, dict) or meta.get("columns") != TABLES[table][0]:
            problems.append(f"{table}: колонки в manifest.json не совпадают с ожидаемыми")
            continue
        if not isinstance(meta.get("rows"), int) or meta["rows"] < 0:
            problems.append(f"{table}: неверное число строк")
        sha = meta.get("sha256")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            problems.append(f"{table}: неверная контрольная сумма")
    return problems
