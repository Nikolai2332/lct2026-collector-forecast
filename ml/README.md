# ML: прогноз отказов датчиков на 24 часа

Скрипты подготовки данных, разметки, признаков, обучения, оценки, прогноза и загрузки в PostgreSQL.
Постановка, метрики и ограничения — в [`docs/ML_REPORT.md`](../docs/ML_REPORT.md), решения — в [`docs/ML_DECISIONS.md`](../docs/ML_DECISIONS.md), качество данных — в [`docs/DATA_QUALITY.md`](../docs/DATA_QUALITY.md).

Данные и всё производное лежат вне git: исходные CSV — в `DATASET_DIR` (по умолчанию `<репозиторий>/data/dataset`), Parquet, признаки, модели и прогнозы — в `DATA_DIR` (по умолчанию `<репозиторий>/data`). Пути задаются переменными окружения (см. `ml_paths.py`). Если CSV лежат в другом месте, задайте путь: `export DATASET_DIR=/path/to/dataset` (PowerShell: `$env:DATASET_DIR="D:\dataset"`).

## Окружение

Python 3.12 или новее (проверено на 3.14 под Windows; все пакеты ставятся из колёс).

```bash
cd ml
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # Linux/macOS: .venv/bin/python
```

Машина, на которой замерено время: 32 логических ядра, 32 ГБ RAM, NVMe, Windows 11. DuckDB использует 28 потоков и до 22 ГБ памяти.

## Запуск с нуля

Все команды — из папки `ml/`, по порядку. Архивы `.7z` к моменту запуска должны быть распакованы в `DATASET_DIR` (8 файлов `ext-journal-2019…2026.csv` и 4 справочника).

| # | Команда | Что делает | Время |
|---|---|---|---|
| 1 | `python -m etl.load_journal` | 8 CSV (313,5 млн строк) → `data/parquet/events/year=/month=/` (чистка, дедупликация, сортировка) | ≈ 15 мин |
| 2 | `python -m etl.load_dims` | справочники → `data/parquet/dim/` (тег по уровням, метки ПК/АНС/щит/ВШ) | < 5 с |
| 3 | `python -m etl.quality_report` | `docs/DATA_QUALITY.md` | ≈ 1 мин |
| 4 | `python -m features.build_base` | почасовые агрегаты, паузы, личная норма, события-отказы → `data/base/` | ≈ 27 мин |
| 5 | `python -m features.build_features daily` | срезы «канал × сутки» 2023-01-01 … 2026-06-29, признаки и метка → `data/features/daily/` | ≈ 50 мин |
| 6 | `python -m model.train` | базовая линия, LightGBM, калибровка, порог по валидации → `data/models/lgbm-2026.09-v2/` | ≈ 2,5 мин |
| 7 | `python -m model.evaluate` | **один раз**: метрики на тесте 2026 → `metrics.json`, графики `docs/ml_*.png` | ≈ 1 мин |
| 8 | `python -m features.build_features hourly` | почасовые срезы 01.01–30.06.2026 (46 кусков по 4 дня, каждый в отдельном процессе) → `data/features/hourly/` | ≈ 1 ч |
| 9 | `python -m model.predict --hourly` | почасовые прогнозы (38,8 млн) с тремя причинами SHAP для уровней «внимание» и выше → `data/predictions/<месяц>.parquet` | ≈ 9–15 мин на месяц, ≈ 1,5 ч |
| 10 | `python -m db.load_postgres --replace` | справочники, 0,5 млн суточных агрегатов, 5,1 млн событий за 30 дней, 0,26 млн отказов разметки (`channel_faults`, отдельно: `--only faults`, 9 с), 11,0 млн прогнозов и 10,8 млн исходов, метрики → PostgreSQL (COPY) | ≈ 23 мин |

Время шагов 8–9 замерено при параллельной работе двух процессов; по отдельности быстрее.

Вспомогательное: `python recon.py` — разведка на `журнал_событий_пример.csv`; `python explore.py all` — статусы по типам датчиков и объёмы по годам (на основе этих запросов выбрана метка).

### predict(at)

```bash
python -m model.predict --at 2026-06-15T12:00
```

Строит признаки на момент `at` (только события до `at`) для всех каналов, активных за 30 дней, и пишет `data/predictions/at_20260615T12.parquet` в формате бэкенда: `channel_id, at, horizon_h, prob, health, risk_level, top_factors, model_version` (+ исход, если горизонт закрыт в данных). По всем 7 745 каналам, активным на 15.06.2026 12:00, — 34 с, из них 21 с — подготовка кумулятивных таблиц.

Из Python: `from model.predict import predict_at; df = predict_at(datetime(2026, 6, 15, 12))`.

### Загрузка в PostgreSQL

```bash
docker compose up -d db api                       # из корня репозитория
python -m db.load_postgres --replace              # DSN: --dsn или PG_DSN, по умолчанию postgresql://collector:change-me@localhost:5432/collector
```

По умолчанию грузятся прогнозы каждый час за июнь 2026 и каждые 6 часов за январь–май (`--hourly-from`, `--step-hours`, `--pred-from`, `--pred-to`). Пользователи, причины, рекомендации сохраняются; демо-прогнозы, демо-справочники, демо-решения и демо-заявки удаляются. Вернуть демо: `docker compose exec api python -m app.seed --force`.

Проверка: `GET /api/predictions?at=2026-06-15T12:00:00` (журнал 2026 года заканчивается 30.06.2026, поэтому «машина времени» работает в пределах 01.01–30.06.2026).

## Артефакты модели

`data/models/lgbm-2026.09-v2/`: `model.txt` (LightGBM), `meta.json` (список признаков, категории, порог, калибровка, границы зон риска, метрики валидации), `feature_norms.parquet` (нормы для фраз), `feature_importance.csv`, `metrics.json` (тест), `TEST_OPENED` (метка однократного открытия теста). Копия метрик — `ml/artifacts/metrics.json`.

## Версия 3 (`lgbm-2026.09-v3`) — воспроизведение с нуля

Каталоги v3 отдельные (`ML_PIPELINE=v3` — по умолчанию): `data/parquet/events_v3`, `data/base_v3`, `data/features_v3`, `data/predictions_v3`. Данные и модель v2 не трогаются; код v2 — тег git `ml-v2`. План, эксперименты и правило выбора — [`docs/ML_V3_PLAN.md`](../docs/ML_V3_PLAN.md), результаты — раздел «Версия 3» в [`docs/ML_REPORT.md`](../docs/ML_REPORT.md).

| # | Команда | Что делает | Время |
|---|---|---|---|
| 1 | `python -m etl.load_dims` | справочники (нужны ETL v3 для физических диапазонов по типу датчика) | < 5 с |
| 2 | `python -m etl.load_journal` | 2022–2026 → Parquet v3: `value_kind` (number / status / date_value / sentinel), значения-даты и служебные коды сохраняются; 2019–2021 не загружаются | ≈ 15 мин |
| 3 | `python -m features.build_base` | почасовые агрегаты (служебные значения, газ ≥ 1 % / ≥ 5 %), паузы без дней без данных, нормы, отказы с видами и флагами (массовый сбой, групповая тишина, простой) | ≈ 16 мин |
| 4 | `python -m features.group_check tune` / `ppr` | распределение групповой тишины на обучении; проверка по датам графика ППР 2026 (нужен `openpyxl` и файлы заказчика) | < 1 мин |
| 5 | `python -m features.build_features daily` | срезы «канал × сутки» 2023-01-01 … 2026-06-29, 126 признаков, метка (b) | ≈ 2 ч 40 мин |
| 6 | `python -m features.labels` | варианты метки a / b / c и метка v2 на тех же срезах | ≈ 15 с |
| 7 | `python -m model.experiments labels` · `compare` · `variants` · `heads` · `precursors` | эксперименты на валидации (по одному прогону; перед каждым — проверка свободной памяти) | ≈ 2,5 ч |
| 8 | `python -m model.train_v3` | итоговая модель: метка b, веса свежих данных, голова видов (`kind_model.txt`) | ≈ 3 мин |
| 9 | `python -m model.evaluate_v3 --dry-valid`, затем `python -m model.evaluate_v3` | проверка кода на валидации; **один раз** — тест 2026 для v3 и переоценка v2 на обеих метках → `metrics.json`, `ml/artifacts/metrics_v3.json`, `docs/ml_v3_*.png` | ≈ 3 мин |
| 10 | `python -m features.build_features hourly` | почасовые срезы 01.01–30.06.2026 (47 кусков по 4 дня) | ≈ 50 мин |
| 11 | `python -m model.predict --hourly` | 38,8 млн почасовых прогнозов; для p ≥ 0,2 — три причины SHAP и `kind_probs` | ≈ 1 ч 32 мин |
| 12 | `python -m db.load_postgres --only model` | замена прогнозов, исходов, отказов и метрик модели в PostgreSQL без потери решений, заявок, пользователей, аудита и настроек (перед этим — резервная копия и `alembic upgrade head` для колонки `kind_probs`) | ≈ 25 мин (оценка) |

`predict(at)` для v3: `python -m model.predict --at 2026-06-15T12:00` — 7 745 каналов за 72 с (38 с — подготовка кумулятивных таблиц, 31 с — признаки на момент, 3 с — модель). Цель ТЗ «меньше минуты» для холодного запуска v3 не выполнена (v2 — 34 с): добавились таблицы групповой тишины, восстановлений и парка. Интерфейс берёт заранее посчитанные почасовые прогнозы, поэтому на скорость экранов это не влияет.

Тест загрузки на PostgreSQL (тестовая база, не основная): `$env:PG_TEST_DSN="postgresql://collector:change-me@127.0.0.1:5432/collector_test"; .venv/Scripts/python -m pytest -q tests/test_load_postgres.py`.

Откат на v2: `$env:ML_PIPELINE="v2"; python -m db.load_postgres --only model --version lgbm-2026.09-v2` (прогнозы v2 — в `data/predictions/`, `kind_probs` будет пустым) или восстановление резервной копии (`deploy/restore.sh`).
