"""Собирает срез реальных данных для экспертов из основной базы в zip (docs/EXPERT_SLICE.md).

    python -m scripts.build_expert_slice --from 2026-06-01 --to 2026-06-30 --out ../dist
    (DATABASE_URL — основная база; на Windows с хоста — 127.0.0.1, не localhost)

Каждая таблица — CSV (COPY … TO STDOUT) со сжатием gzip прямо в zip; manifest.json — период, число строк,
контрольные суммы, версия схемы. Пользователей, паролей, журнала действий, решений и заявок в срезе нет.
Срез — обезличенные данные заказчика: в git не кладётся (dist/ в .gitignore), распространяется по ссылке команды.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.expert_slice import COMPATIBLE_DB_REVISIONS, FORMAT_VERSION, SCHEMA_REVISION, TABLES  # noqa: E402


def pg_url(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # консоль Windows (cp1251)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="date_from", type=date.fromisoformat, default=date(2026, 6, 1))
    ap.add_argument("--to", dest="date_to", type=date.fromisoformat, default=date(2026, 6, 30))
    ap.add_argument("--out", type=Path, default=Path("../dist"))
    args = ap.parse_args()
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Задайте DATABASE_URL основной базы", file=sys.stderr)
        return 1
    params = {
        "date_from": f"{args.date_from} 00:00:00",
        "date_to": args.date_to.isoformat(),
        "date_to_end": f"{args.date_to} 23:59:59",
        "hist_from": (args.date_from - timedelta(days=30)).isoformat(),
        "faults_from": f"{args.date_from - timedelta(days=90)} 00:00:00",
    }
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / f"expert_slice_{args.date_from:%Y%m%d}_{args.date_to:%Y%m%d}.zip"
    manifest = {"format": FORMAT_VERSION, "schema_revision": SCHEMA_REVISION, "created_at": datetime.now().isoformat(timespec="seconds"),
                "period": {"from": args.date_from.isoformat(), "to": args.date_to.isoformat()},
                "note": "Срез обезличенных данных заказчика (АО «Москоллектор») для проверки экспертами. Не публиковать.",
                "tables": {}}
    with psycopg.connect(pg_url(url)) as conn, zipfile.ZipFile(target, "w", zipfile.ZIP_STORED) as zf:
        rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        if rev not in COMPATIBLE_DB_REVISIONS:
            print(f"Схема базы {rev}, срез рассчитан на {SCHEMA_REVISION}", file=sys.stderr)
            return 1
        manifest["model_version"] = conn.execute(
            "SELECT model_version FROM model_thresholds WHERE is_selected LIMIT 1").fetchone()[0]
        for table, (cols, where) in TABLES.items():
            t0 = time.time()
            sql = f"COPY (SELECT {', '.join(cols)} FROM {table} WHERE {where.format(**params)}) TO STDOUT WITH (FORMAT csv, HEADER true)"
            raw, packed = 0, io.BytesIO()
            sha = hashlib.sha256()
            with gzip.GzipFile(fileobj=packed, mode="wb", compresslevel=6, mtime=0) as gz, conn.cursor() as cur:
                with cur.copy(sql) as cp:
                    for chunk in cp:
                        gz.write(chunk)
                        raw += len(chunk)
            data = packed.getvalue()
            sha.update(data)
            rows = conn.execute(f"SELECT count(*) FROM {table} WHERE {where.format(**params)}").fetchone()[0]
            zf.writestr(f"{table}.csv.gz", data)
            manifest["tables"][table] = {"rows": rows, "columns": cols, "bytes": len(data), "sha256": sha.hexdigest()}
            print(f"{table:<20} {rows:>10,} строк  {raw / 1e6:8.1f} МБ → {len(data) / 1e6:7.1f} МБ  {time.time() - t0:5.1f} с",
                  flush=True)
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"Готово: {target} ({target.stat().st_size / 1e6:.1f} МБ)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
