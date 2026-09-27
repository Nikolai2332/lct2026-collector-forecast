"""Обучение: базовая линия, LightGBM с весами классов и ранней остановкой по PR-AUC на валидации,
изотоническая калибровка на валидации, порог = максимальный Recall при Precision ≥ 0,72 на валидации.

Тест (2026) здесь НЕ читается — только в model/evaluate.py, один раз.

Запуск: python -m model.train [--version lgbm-2026.09-v1]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
import pyarrow.dataset as ds
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import average_precision_score, precision_recall_curve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import FEAT, MODELS  # noqa: E402

TRAIN = ("2023-01-01", "2025-06-29")  # последний срез 29.06 00:00 → горизонт закрыт к 30.06; зазор — сутки
VALID = ("2025-07-01", "2025-12-30")
TARGET_PRECISION = 0.72

ID_COLS = ["ch", "T", "y", "fault_at", "fault_kind", "eligible", "last_fault_ts", "next_fault_ts", "next_fault_kind",
           "object_id"]
CAT_COLS = ["sensor_type", "system_type", "parent_id"]


def load(d_from: str, d_to: str) -> pd.DataFrame:
    """Срезы с закрытым горизонтом и eligible (без отказа за 72 ч, с событиями за 30 дней)."""
    dset = ds.dataset(str(FEAT / "daily"), format="parquet")
    flt = ((ds.field("T") >= pd.Timestamp(d_from)) & (ds.field("T") <= pd.Timestamp(d_to))
           & (ds.field("eligible") == True) & ds.field("y").is_valid())  # noqa: E712
    df = dset.to_table(filter=flt).to_pandas()
    for c in CAT_COLS:
        df[c] = df[c].astype("category")
    return df


def feature_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in ID_COLS]


def threshold_for_precision(y: np.ndarray, p: np.ndarray, target: float) -> tuple[float, float, float]:
    """Максимальный Recall при Precision ≥ target. Возвращает (порог, precision, recall)."""
    prec, rec, thr = precision_recall_curve(y, p)
    ok = np.where(prec[:-1] >= target)[0]
    if len(ok) == 0:
        i = int(np.argmax(prec[:-1]))
    else:
        i = ok[np.argmax(rec[:-1][ok])]
    return float(thr[i]), float(prec[i]), float(rec[i])


def pr(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "tp": tp, "fp": fp, "fn": fn}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="lgbm-2026.09-v2")
    ap.add_argument("--rounds", type=int, default=3000)
    args = ap.parse_args()
    out = MODELS / args.version
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    tr, va = load(*TRAIN), load(*VALID)
    # единые категории для train/valid
    for c in CAT_COLS:
        cats = sorted(set(tr[c].dropna().unique()) | set(va[c].dropna().unique()))
        tr[c] = pd.Categorical(tr[c], categories=cats)
        va[c] = pd.Categorical(va[c], categories=cats)
    feats = feature_columns(tr)
    print(f"load: {time.time() - t0:.0f} s; train {len(tr):,} (pos {tr.y.mean():.4%}), "
          f"valid {len(va):,} (pos {va.y.mean():.4%}); features {len(feats)}", flush=True)

    # Базовая линия ТЗ: «был отказ за 7 дней — будет снова»
    base_va = pr(va.y.values, va.faults_7d.values > 0)
    print("baseline valid:", base_va, flush=True)

    pos_w = (tr.y == 0).sum() / max((tr.y == 1).sum(), 1)
    params = dict(objective="binary", learning_rate=0.03, num_leaves=127, min_data_in_leaf=200,
                  feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
                  scale_pos_weight=float(np.sqrt(pos_w)), metric="average_precision", num_threads=30,
                  verbose=-1, seed=42, max_cat_to_onehot=32, cat_smooth=20)
    dtr = lgb.Dataset(tr[feats], tr.y, categorical_feature=CAT_COLS, free_raw_data=False)
    dva = lgb.Dataset(va[feats], va.y, categorical_feature=CAT_COLS, reference=dtr)
    booster = lgb.train(params, dtr, num_boost_round=args.rounds, valid_sets=[dva], valid_names=["valid"],
                        callbacks=[lgb.early_stopping(150, verbose=False), lgb.log_evaluation(100)])
    print(f"best_iteration {booster.best_iteration}, train {time.time() - t0:.0f} s", flush=True)

    raw_va = booster.predict(va[feats], num_iteration=booster.best_iteration)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw_va, va.y)
    p_va = iso.predict(raw_va)
    thr, p_at, r_at = threshold_for_precision(va.y.values, p_va, TARGET_PRECISION)
    m_va = pr(va.y.values, p_va >= thr) | {"pr_auc": float(average_precision_score(va.y, p_va)),
                                           "pr_auc_raw": float(average_precision_score(va.y, raw_va))}
    print(f"valid: threshold {thr:.4f} → {m_va}", flush=True)

    booster.save_model(str(out / "model.txt"), num_iteration=booster.best_iteration)
    imp = pd.DataFrame({"feature": feats, "gain": booster.feature_importance("gain")}).sort_values("gain", ascending=False)
    imp.to_csv(out / "feature_importance.csv", index=False)
    meta = {
        "model_version": args.version,
        "features": feats,
        "categorical": CAT_COLS,
        "categories": {c: [x if isinstance(x, str) else int(x) for x in tr[c].cat.categories] for c in CAT_COLS},
        "threshold": thr,
        "target_precision": TARGET_PRECISION,
        "risk_levels": {"normal": "health >= 80", "attention": "50 <= health < 80", "risk": "20 <= health < 50",
                        "critical": "health < 20"},
        "calibration": {"x": iso.X_thresholds_.tolist(), "y": iso.y_thresholds_.tolist()},
        "best_iteration": booster.best_iteration,
        "params": params,
        "periods": {"train": TRAIN, "valid": VALID},
        "valid": {"model": m_va, "baseline": base_va, "rows": len(va), "positives": int(va.y.sum())},
        "train_rows": len(tr), "train_positives": int(tr.y.sum()),
        "seconds": round(time.time() - t0),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    # норма признаков по типу датчика (медиана на обучении) — для фраз «обычно …»
    norms = tr.groupby("sensor_type", observed=True)[[f for f in feats if f not in CAT_COLS]].median(numeric_only=True)
    norms.to_parquet(out / "feature_norms.parquet")
    print(f"saved {out}; top features:\n{imp.head(25).to_string(index=False)}")


if __name__ == "__main__":
    main()
