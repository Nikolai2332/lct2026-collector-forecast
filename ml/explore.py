"""Разведка по всему журналу (Parquet). Аргумент — номер запроса или 'all'."""
import sys
import duckdb
from ml_paths import EVENTS, DIM
con = duckdb.connect(); con.execute("SET threads=28")
con.execute(f"CREATE VIEW ev AS SELECT * FROM read_parquet('{EVENTS.as_posix()}/**/*.parquet', hive_partitioning=true)")
con.execute(f"CREATE VIEW ch AS SELECT * FROM '{(DIM/'channels.parquet').as_posix()}'")
Q = {
 "statuses": """SELECT value_text, count(*) n, count(DISTINCT channel_id) chans, round(avg(is_alarm::int),3) alarm_share
   FROM ev WHERE value_text IS NOT NULL GROUP BY 1 ORDER BY n DESC""",
 "type_status": """SELECT coalesce(c.sensor_type,'<нет в справочнике>') st, e.value_text, count(*) n, count(DISTINCT e.channel_id) chans, round(avg(is_alarm::int),2) al
   FROM ev e LEFT JOIN ch c ON c.id=e.channel_id WHERE year>=2023 AND value_text IS NOT NULL GROUP BY ALL HAVING n>=200 ORDER BY st, n DESC""",
 "per_year": """SELECT year, count(*) n, count(DISTINCT channel_id) chans, count(*) FILTER (WHERE c.id IS NULL)*1.0/count(*) no_ch_share,
   count(DISTINCT channel_id) FILTER (WHERE c.id IS NULL) no_ch_chans, count(*) FILTER (WHERE value_num IS NOT NULL) num_n,
   count(*) FILTER (WHERE value_text IS NOT NULL) txt_n FROM ev e LEFT JOIN ch c ON c.id=e.channel_id GROUP BY 1 ORDER BY 1""",
 "per_year_type": """SELECT year, coalesce(c.sensor_type,'<нет>') st, count(*) n, count(DISTINCT channel_id) chans, round(count(*)/count(DISTINCT channel_id)/365.0,1) per_ch_day
   FROM ev e LEFT JOIN ch c ON c.id=e.channel_id GROUP BY ALL HAVING n > 300000 ORDER BY st, year""",
}
keys = Q if sys.argv[1] == "all" else sys.argv[1:]
for k in keys:
    print("==", k); print(con.sql(Q[k]).fetchdf().to_string(max_rows=400))
