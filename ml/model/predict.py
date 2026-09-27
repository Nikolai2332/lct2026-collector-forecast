"""Прогнозы в формате бэкенда: channel_id, at, horizon_h, prob, health, risk_level, top_factors, model_version,
kind_probs (v3: вероятности по видам отказа, JSON {link, fault, disconnected, value}; сумма = prob)
(+ исход: happened, fault_at, fault_kind — по реальным событиям за следующие 24 ч, если горизонт закрыт в данных).

Режимы:
  python -m model.predict --at 2026-06-15T12:00            # predict(at): все активные каналы на момент at
  python -m model.predict --hourly --from 2026-01-01 --to 2026-07-01   # из data/features/hourly/*.parquet

Результат: data/predictions/<YYYY-MM>.parquet (почасовые) или data/predictions/at_<...>.parquet.
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import build_features as bf  # noqa: E402
from ml_paths import FEAT, MODELS, PRED  # noqa: E402
from model.evaluate import calibrate, prepare_frame  # noqa: E402
from model.factors import top_factors  # noqa: E402

DEFAULT_VERSION = "lgbm-2026.09-v3"
CHUNK = 500_000
# SHAP (TreeSHAP по 369 глубоким деревьям) стоит ≈ 1–4 мс на строку, поэтому причины считаются только для
# прогнозов уровня «внимание» и выше (health < 80); у «нормы» top_factors = [].
SHAP_MIN_PROB = 0.2
# Вид отказа модели → код вида в бэкенде (FaultKind). kind_probs = prob × P(вид | отказ); пишутся для тех же строк,
# что и причины (p ≥ SHAP_MIN_PROB): «Вероятнее всего: …» нужен диспетчеру у рискованных прогнозов.
KIND_CODES = {"Пропадание связи": "link", "Неисправен": "fault", "Отключено устройство": "disconnected",
              "Сбой значения": "value"}


def risk_level(health: np.ndarray) -> np.ndarray:
    """Как в ТЗ: ≥ 80 норма, 50–79 внимание, 20–49 риск, < 20 критично."""
    return np.select([health >= 80, health >= 50, health >= 20], ["normal", "attention", "risk"], "critical")


class Model:
    def __init__(self, version: str = DEFAULT_VERSION):
        self.dir = MODELS / version
        self.meta = json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))
        self.booster = lgb.Booster(model_file=str(self.dir / "model.txt"))
        self.norms = pd.read_parquet(self.dir / "feature_norms.parquet")
        self.version = version
        km = self.dir / "kind_model.txt"
        self.kind_booster = lgb.Booster(model_file=str(km)) if km.exists() else None

    def predict_frame(self, df: pd.DataFrame, with_factors: bool = True) -> pd.DataFrame:
        feats = self.meta["features"]
        parts = []
        for s in range(0, len(df), CHUNK):
            X = prepare_frame(df.iloc[s:s + CHUNK].copy(), self.meta)
            raw = self.booster.predict(X[feats], num_threads=30)
            prob = np.clip(calibrate(self.meta, raw), 0, 1)
            health = np.round(100 * (1 - prob)).astype(int)
            out = pd.DataFrame({
                "channel_id": X["ch"].values, "at": X["T"].values, "horizon_h": 24,
                "prob": np.round(prob, 4), "health": health, "risk_level": risk_level(health),
                "model_version": self.version,
            })
            out["top_factors"] = "[]"
            out["kind_probs"] = None
            idx = np.where(prob >= SHAP_MIN_PROB)[0] if with_factors else np.array([], dtype=int)
            if len(idx) and self.kind_booster is not None:
                pk = self.kind_booster.predict(X.iloc[idx][feats], num_threads=30)
                codes = [KIND_CODES[k] for k in self.meta["kinds"]]
                out.loc[idx, "kind_probs"] = [json.dumps({c: round(float(v), 4) for c, v in zip(codes, row * pr)})
                                              for row, pr in zip(pk, prob[idx])]
            if len(idx):
                Xs = X.iloc[idx]
                contrib = self.booster.predict(Xs[feats], pred_contrib=True, num_threads=30)
                out.loc[idx, "top_factors"] = [json.dumps(f, ensure_ascii=False)
                                               for f in top_factors(Xs, contrib, feats, self.norms)]
            # исход: был ли отказ в (at, at + 24 ч]; y = NULL, если горизонт не закрылся в данных
            out["happened"] = X["y"].values
            out["fault_at"] = X["fault_at"].values
            out["fault_kind"] = X["fault_kind"].values
            out["eligible"] = X["eligible"].values
            parts.append(out)
        return pd.concat(parts, ignore_index=True)


def predict_at(at: datetime, version: str = DEFAULT_VERSION) -> pd.DataFrame:
    """predict(at): признаки на момент at (только события до at) → прогноз по всем активным каналам."""
    t0 = time.time()
    con = bf.connect()
    bf.prepare(con)
    tmp = FEAT / f"at_{at:%Y%m%dT%H}.parquet"
    bf.build(con, "at", None, None, tmp, at=at)
    df = pd.read_parquet(tmp)
    res = Model(version).predict_frame(df)
    print(f"predict({at}): {len(res):,} каналов за {time.time() - t0:.0f} с", flush=True)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", type=datetime.fromisoformat)
    ap.add_argument("--hourly", action="store_true")
    ap.add_argument("--from", dest="d_from", default="2026-01-01")
    ap.add_argument("--to", dest="d_to", default="2026-07-01")
    ap.add_argument("--version", default=DEFAULT_VERSION)
    args = ap.parse_args()
    if args.at:
        at = args.at.replace(minute=0, second=0, microsecond=0)
        res = predict_at(at, args.version)
        out = PRED / f"at_{at:%Y%m%dT%H}.parquet"
        res.to_parquet(out, index=False)
        print(res.sort_values("prob", ascending=False).head(10)[["channel_id", "at", "prob", "health", "risk_level"]])
        print(json.loads(res.sort_values("prob", ascending=False).iloc[0]["top_factors"]))
        return
    model = Model(args.version)
    d = date.fromisoformat(args.d_from)
    end = date.fromisoformat(args.d_to)
    while d < end:
        t0 = time.time()
        files = sorted((FEAT / "hourly").glob(f"{d:%Y-%m}-*.parquet"))
        # по одному файлу (4 дня): месяц целиком в памяти — ≈ 6 млн строк × 150 признаков
        res = pd.concat([model.predict_frame(pd.read_parquet(f)) for f in files], ignore_index=True)
        res.to_parquet(PRED / f"{d:%Y-%m}.parquet", index=False)
        print(f"{d:%Y-%m}: {len(res):,} прогнозов, {time.time() - t0:.0f} с; уровни: "
              f"{res.risk_level.value_counts().to_dict()}", flush=True)
        d = date(d.year + (d.month == 12), d.month % 12 + 1, 1)


if __name__ == "__main__":
    main()
