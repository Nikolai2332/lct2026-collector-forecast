"""Срез для экспертов: manifest.json из архива не может передать в SQL загрузчика свои таблицы и колонки
(аудит 2, S-31). Загрузка целиком проверяется на PostgreSQL вручную (docs/EXPERT_SLICE.md)."""

import copy

from scripts.expert_slice import FORMAT_VERSION, SCHEMA_REVISION, TABLES, manifest_problems


def _manifest() -> dict:
    return {"format": FORMAT_VERSION, "schema_revision": SCHEMA_REVISION, "period": {"from": "2026-06-01", "to": "2026-06-30"},
            "tables": {t: {"rows": 1, "columns": list(cols), "sha256": "0" * 64} for t, (cols, _) in TABLES.items()}}


def test_valid_manifest_passes():
    assert manifest_problems(_manifest()) == []


def test_injected_column_is_rejected():
    m = _manifest()
    m["tables"]["objects"]["columns"] = ["id) FROM STDIN; DROP TABLE users; --"]
    assert any("objects: колонки" in p for p in manifest_problems(m))


def test_extra_or_missing_table_is_rejected():
    m = copy.deepcopy(_manifest())
    m["tables"]["users"] = {"rows": 1, "columns": ["id", "password_hash"], "sha256": "0" * 64}
    assert manifest_problems(m) == ["состав таблиц в manifest.json не совпадает с ожидаемым"]
    m = _manifest()
    del m["tables"]["objects"]
    assert manifest_problems(m)


def test_wrong_format_rows_and_checksum_are_rejected():
    m = _manifest()
    m["format"] = 99
    m["tables"]["channels"]["rows"] = -1
    m["tables"]["reasons"]["sha256"] = "zz"
    problems = manifest_problems(m)
    assert len(problems) == 3


def test_loader_does_not_take_sql_from_manifest():
    """COPY в загрузчике строится из имён, прошедших manifest_problems, и таблиц LOAD_ORDER из кода."""
    src = open("scripts/load_expert_slice.py", encoding="utf-8").read()
    assert "manifest_problems(manifest)" in src
    assert src.index("manifest_problems(manifest)") < src.index("cur.copy(")
