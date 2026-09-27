"""Справочники → Parquet (data/parquet/dim/): objects, channels (тег разложен на уровни), states.

Тег «847-1.1.131.2.» → уровни 847 / 1 / 1 / 131 / 2 (как в бэкенде: часть до дефиса — отдельный уровень).
Из названия датчика извлекаются метки: пикет ПК, насосная АНС, щит, вентшахта ВШ.

Запуск: python -m etl.load_dims
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import DIM, SRC  # noqa: E402


def main() -> None:
    con = duckdb.connect()
    con.execute(f"""
        COPY (
            SELECT ид_объект::INT AS id, иерархия_уровень::SMALLINT AS level,
                   -- родитель района (3831) в справочнике отсутствует → корень дерева
                   CASE WHEN родитель::INT IN (SELECT ид_объект::INT FROM read_csv('{SRC}/справочник_объектов_диспетчер.csv', all_varchar=true))
                        THEN родитель::INT END AS parent_id,
                   вид_объекта AS kind, trim(диспетчерское_название_объекта) AS name
            FROM read_csv('{SRC}/справочник_объектов_диспетчер.csv', all_varchar=true, header=true)
        ) TO '{(DIM / "objects.parquet").as_posix()}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            WITH c AS (
                SELECT ид_канала_данных::BIGINT AS id, ид_объект::INT AS object_id,
                       trim(тип_инж_системы) AS system_type, trim(тип_датчика) AS sensor_type,
                       trim(тег_инженерной_системы) AS tag, trim(название_датчика) AS name,
                       string_split(regexp_replace(trim(тег_инженерной_системы), '\\.$', ''), '.') AS parts
                FROM read_csv('{SRC}/справочник_каналов_датчиков.csv', all_varchar=true, header=true)
            ), t AS (
                SELECT *, list_concat(string_split(parts[1], '-'), parts[2:]) AS lv FROM c
            )
            SELECT id, object_id, system_type, sensor_type, tag,
                   lv[1] AS tag_l1, lv[2] AS tag_l2, lv[3] AS tag_l3, lv[4] AS tag_l4, lv[5] AS tag_l5,
                   len(lv) AS tag_depth, name,
                   regexp_matches(name, 'ПК\\s*\\d') AS has_pk,
                   regexp_matches(name, 'АНС') AS has_ans,
                   regexp_matches(lower(name), 'щит|шкаф|щ\\.|щр') AS has_shield,
                   regexp_matches(name, 'ВШ') AS has_vsh
            FROM t
        ) TO '{(DIM / "channels.parquet").as_posix()}' (FORMAT PARQUET)
    """)
    con.execute(f"""
        COPY (
            SELECT trim(тип_датчика) AS sensor_type, ид_набор_состояний::INT AS state_set,
                   trim(название_состояния) AS state, lower(тревожное) IN ('t','true') AS is_alarm
            FROM read_csv('{SRC}/справочник_состояний.csv', all_varchar=true, header=true)
        ) TO '{(DIM / "states.parquet").as_posix()}' (FORMAT PARQUET)
    """)
    for t in ("objects", "channels", "states"):
        n = con.execute(f"SELECT count(*) FROM '{(DIM / (t + '.parquet')).as_posix()}'").fetchone()[0]
        print(t, n)
    print(con.sql(f"SELECT tag, tag_l1, tag_l2, tag_l3, tag_l4, tag_l5, tag_depth, name, has_pk, has_ans, has_shield, has_vsh "
                  f"FROM '{(DIM / 'channels.parquet').as_posix()}' USING SAMPLE 6 ROWS"))
    print(con.sql(f"SELECT tag_depth, count(*) FROM '{(DIM / 'channels.parquet').as_posix()}' GROUP BY 1 ORDER BY 1"))


if __name__ == "__main__":
    main()
