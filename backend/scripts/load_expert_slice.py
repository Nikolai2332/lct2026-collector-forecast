"""Загружает срез реальных данных для экспертов вместо синтетического демо (docs/EXPERT_SLICE.md).

    docker compose cp expert_slice_20260601_20260630.zip api:/tmp/slice.zip
    docker compose exec api python -m scripts.load_expert_slice /tmp/slice.zip

Что делает: проверяет manifest (формат, версия схемы, контрольные суммы), очищает демо-данные, которые срез
заменяет (справочники, прогнозы, события, отказы, метрики, демо-решения и демо-заявки), и загружает таблицы
COPY-ом. Пользователи, их роли и журнал действий остаются; демо-пользователям заново назначаются области
по новому дереву объектов (техник — «объект Альфа»). Без среза сервис работает на синтетическом демо, как раньше.
В production (APP_ENV=production) и если в базе есть рабочие (не демо) решения, заявки или области видимости —
только с --force; что будет удалено, печатается заранее. Состав manifest.json проверяется по белому списку
(scripts/expert_slice.py), загрузка пишется в audit_log (`expert_slice_load`).
"""

import argparse
import gzip
import hashlib
import json
import sys
import time
import zipfile
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.expert_slice import CLEAR, COMPATIBLE_DB_REVISIONS, LOAD_ORDER, manifest_problems  # noqa: E402

# Что загрузка удалит, кроме демо-данных: решения и заявки не демо-пользователей, области видимости не демо-учёток
NON_DEMO_SQL = {
    "решений не демо-пользователей": "SELECT count(*) FROM decisions d LEFT JOIN users u ON u.id = d.user_id "
                                     "WHERE u.username IS NULL OR NOT (u.username = ANY(%s))",
    "заявок не демо-пользователей": "SELECT count(*) FROM work_orders w LEFT JOIN users u ON u.id = w.created_by "
                                    "WHERE u.username IS NULL OR NOT (u.username = ANY(%s))",
    "областей видимости не демо-учёток (роли с областью увидят пустой список)":
        "SELECT count(*) FROM user_scopes s JOIN users u ON u.id = s.user_id WHERE NOT (u.username = ANY(%s))",
}


def non_demo_losses(conn, demo: list[str]) -> dict[str, int]:
    return {what: conn.execute(sql, (demo,)).fetchone()[0] for what, sql in NON_DEMO_SQL.items()}


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows (cp1251)
    ap = argparse.ArgumentParser(description="Загрузка среза реальных данных для экспертов")
    ap.add_argument("path", type=Path, help="zip-архив среза")
    ap.add_argument("--force", action="store_true", help="разрешить в production и замену рабочих (не демо) решений, заявок и областей")
    args = ap.parse_args()

    from app.config import get_settings

    s = get_settings()
    if s.is_production and not args.force:
        print("APP_ENV=production: срез заменяет данные — запустите с --force, если это действительно нужно",
              file=sys.stderr)
        return 1
    if not s.database_url.startswith("postgresql"):
        print("Загрузчик среза работает только с PostgreSQL", file=sys.stderr)
        return 1
    zf = zipfile.ZipFile(args.path)
    manifest = json.loads(zf.read("manifest.json"))
    problems = manifest_problems(manifest)
    if problems:
        print("Архив не похож на срез этой версии сервиса: " + "; ".join(problems), file=sys.stderr)
        return 1
    for table, meta in manifest["tables"].items():
        if hashlib.sha256(zf.read(f"{table}.csv.gz")).hexdigest() != meta["sha256"]:
            print(f"Контрольная сумма {table} не совпала — архив повреждён", file=sys.stderr)
            return 1
    print(f"Срез {manifest['period']['from']} — {manifest['period']['to']}, модель {manifest.get('model_version')}")

    url = s.database_url.replace("postgresql+psycopg://", "postgresql://")
    t_all = time.time()
    with psycopg.connect(url) as conn:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if rev not in COMPATIBLE_DB_REVISIONS:
            print(f"Схема базы {rev}, срез — для {', '.join(COMPATIBLE_DB_REVISIONS)}: обновите сервис (alembic upgrade head)", file=sys.stderr)
            return 1
        # Не молча: что пропадёт, кроме демо-данных. Пользователи и журнал действий срез не трогает
        from app.seed import DEMO_USERS

        losses = {k: v for k, v in non_demo_losses(conn, [u[0] for u in DEMO_USERS]).items() if v}
        for what, n in losses.items():
            print(f"  будет удалено {what}: {n}", file=sys.stderr)
        if losses and not args.force:
            print("В базе есть рабочие данные (не демо) — срез их удалит. Сделайте резервную копию "
                  "(deploy/backup.sh) и запустите с --force", file=sys.stderr)
            return 1
        # Одна транзакция: либо весь срез, либо прежние данные
        conn.execute("TRUNCATE " + ", ".join(CLEAR) + " RESTART IDENTITY")
        for table in LOAD_ORDER:
            meta = manifest["tables"][table]
            t0 = time.time()
            cols = ", ".join(meta["columns"])
            with conn.cursor() as cur, cur.copy(f"COPY {table} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)") as cp, \
                    gzip.open(zf.open(f"{table}.csv.gz")) as src:
                while chunk := src.read(1 << 20):
                    cp.write(chunk)
            got = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if got != meta["rows"]:
                raise SystemExit(f"{table}: загружено {got}, в манифесте {meta['rows']}")
            print(f"  {table:<20} {got:>10,} строк  {time.time() - t0:6.1f} с", flush=True)
        # Счётчики id после загрузки с явными id
        for table in LOAD_ORDER:
            if "id" in manifest["tables"][table]["columns"]:
                conn.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                             f"coalesce((SELECT max(id) FROM {table}), 0) + 1, false) "
                             f"WHERE pg_get_serial_sequence('{table}', 'id') IS NOT NULL")
        conn.execute(
            "INSERT INTO audit_log (ts, username, action, entity, details) "
            "VALUES (now() AT TIME ZONE 'Europe/Moscow', NULL, 'expert_slice_load', 'expert_slice', %s::jsonb)",
            (json.dumps({"period": manifest["period"], "model_version": manifest.get("model_version"),
                         "file": Path(args.path).name[:128], "removed_non_demo": losses}, ensure_ascii=False),))
        conn.commit()
    # Области демо-пользователей — по новому дереву; справочник рекомендаций — из rules.yaml
    from app.db import SessionLocal
    from app.recommendations import engine
    from app.seed import apply_demo_access

    with SessionLocal() as db:
        apply_demo_access(db)
        engine.sync_dictionary(db)
        db.commit()
    print(f"Готово за {time.time() - t_all:.0f} с. Перезапустите API, чтобы сбросить кэш: docker compose restart api")
    return 0


if __name__ == "__main__":
    sys.exit(main())
