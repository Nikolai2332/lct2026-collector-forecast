"""Пути к исходникам и производным данным. Переопределяются переменными окружения.

Версия конвейера (ML_PIPELINE, по умолчанию v3) выбирает каталоги производных данных:
  v2 — data/parquet/events, data/base, data/features (данные модели lgbm-2026.09-v2, код — тег git `ml-v2`);
  v3 — data/parquet/events_v3, data/base_v3, data/features_v3 (служебные значения, групповая тишина, дни без данных).
Данные v2 не удаляются: по ним оценивается v2 на обеих метках.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("DATASET_DIR", ROOT / "data" / "dataset")).as_posix()
DATA = Path(os.environ.get("DATA_DIR", ROOT / "data"))
PIPELINE = os.environ.get("ML_PIPELINE", "v3")
PARQUET = DATA / "parquet"
DIM = PARQUET / "dim"
MODELS = DATA / "models"
# v2 (модель lgbm-2026.09-v2) — всегда доступны для сравнения
EVENTS_V2 = PARQUET / "events"
BASE_V2 = DATA / "base"
FEAT_V2 = DATA / "features"
if PIPELINE == "v2":
    EVENTS, BASE, FEAT, PRED = EVENTS_V2, BASE_V2, FEAT_V2, DATA / "predictions"
else:
    EVENTS, BASE, FEAT, PRED = PARQUET / "events_v3", DATA / "base_v3", DATA / "features_v3", DATA / "predictions_v3"
for p in (EVENTS, DIM, FEAT, MODELS, PRED):
    p.mkdir(parents=True, exist_ok=True)
