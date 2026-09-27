"""Разведка: пример суток 01.08.2026 и справочники. Печатает факты для docs/ML_DECISIONS.md."""
import duckdb
from ml_paths import SRC

con = duckdb.connect()
s = f"read_csv('{SRC}/журнал_событий_пример.csv', all_varchar=true, header=true)"
ch = f"read_csv('{SRC}/справочник_каналов_датчиков.csv', all_varchar=true, header=true)"
q = lambda sql: print(con.sql(sql))
q(f"select count(*) n, count(distinct ид_канала_данных) ch, min(дата), max(дата), count(distinct тревожное) from {s}")
q(f"select тревожное, count(*) from {s} group by 1")
q(f"select значение_датчика v, count(*) n, count(distinct ид_канала_данных) ch from {s} where try_cast(значение_датчика as double) is null group by 1 order by 2 desc")
q(f"""select c.тип_датчика, count(*) n, count(distinct e.ид_канала_данных) ch from {s} e left join {ch} c using(ид_канала_данных)
      where значение_датчика='Неисправен' group by 1 order by 2 desc""")
q(f"""select c.тип_инж_системы, c.тип_датчика, count(*) n, count(distinct e.ид_канала_данных) ch,
      sum(try_cast(значение_датчика as double) is not null)::int num from {s} e left join {ch} c using(ид_канала_данных) group by all order by n desc""")
q(f"select count(*) filter (where c.ид_канала_данных is null) * 1.0 / count(*) no_ch from {s} e left join {ch} c using(ид_канала_данных)")
q(f"select count(*), count(distinct ид_канала_данных), count(distinct тип_датчика), count(distinct тип_инж_системы) from {ch}")
q(f"select тип_инж_системы, тип_датчика, count(*) from {ch} group by all order by 3 desc")
