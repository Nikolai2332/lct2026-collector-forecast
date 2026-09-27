"""Единственное место с ветвлением по диалекту БД.

Основная цель — PostgreSQL 16. Ветка SQLite нужна только тестам (tests/conftest.py).
"""

from collections.abc import Iterable, Sequence

from sqlalchemy import Table, func
from sqlalchemy.orm import Session


def _insert_for(db: Session):
    # DIALECT: ON CONFLICT есть и в PostgreSQL, и в SQLite, но конструкции insert у них разные.
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def upsert(
    db: Session,
    table: Table,
    rows: Sequence[dict],
    conflict_cols: Iterable[str],
    update_cols: Iterable[str] | None = None,
    batch_size: int = 1000,
) -> int:
    """INSERT ... ON CONFLICT: обновляет update_cols, а без них пропускает дубли. Возвращает число вставленных/обновлённых строк."""
    if not rows:
        return 0
    insert = _insert_for(db)
    conflict_cols = list(conflict_cols)
    update_cols = list(update_cols or [])
    affected = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        stmt = insert(table).values(chunk)
        if update_cols:
            stmt = stmt.on_conflict_do_update(
                index_elements=conflict_cols,
                set_={c: stmt.excluded[c] for c in update_cols},
            )
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=conflict_cols)
        # DIALECT: rowcount у INSERT … ON CONFLICT через psycopg приходит как -1 (проверено на PG 16),
        # поэтому считаем строки через RETURNING: DO NOTHING возвращает только вставленные,
        # DO UPDATE — вставленные и обновлённые. SQLite поддерживает RETURNING с 3.35.
        stmt = stmt.returning(table.c[conflict_cols[0]])
        affected += len(db.execute(stmt).all())
    return affected


def day_of(column):
    """Дата из timestamp для GROUP BY по дням.

    DIALECT: date(ts) есть и в PostgreSQL, и в SQLite. CAST(ts AS DATE) в SQLite даёт число, поэтому не он.
    PostgreSQL возвращает date, SQLite — строку 'YYYY-MM-DD'; вызывающий код приводит результат через as_date().
    Фильтр по периоду всё равно идёт по самому at, так что индекс ix_predictions_at_* используется.
    """
    return func.date(column)


def as_date(value):
    from datetime import date

    return value if isinstance(value, date) else date.fromisoformat(str(value))


def reset_sequences(db: Session, tables: Iterable[Table]) -> None:
    """После вставки строк с явными id сдвигает последовательности PostgreSQL. В SQLite не требуется."""
    # DIALECT: setval/pg_get_serial_sequence — только PostgreSQL.
    if db.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import text

    for table in tables:
        pk = list(table.primary_key.columns)
        if len(pk) != 1 or not pk[0].autoincrement:
            continue
        db.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', '{pk[0].name}'), "
                f"COALESCE((SELECT MAX({pk[0].name}) FROM {table.name}), 0) + 1, false)"
            )
        )
