"""Загрузка сырых журналов заказчика.

ETL сделан вне сервиса, в ml/etl (DuckDB → Parquet, справочники, суточные агрегаты) и ml/db/load_postgres.py
(загрузка в PostgreSQL через COPY); порядок — ml/README.md. Ручной импорт CSV/XLSX через API — app.importer,
приём потока событий — POST /api/ingest/events. Этот пакет оставлен как точка для переноса ETL в сервис.
"""
