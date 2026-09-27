"""Обучение итоговой модели v3 (lgbm-2026.09-v3) — конфигурация, выбранная по правилу docs/ML_V3_PLAN.md, раздел 5:
метка (b), признаки v3, веса свежих данных (полураспад 1 год), обучение 2023-01-01 … 2025-06-29 на полных данных,
ранняя остановка, изотоническая калибровка и порог (максимальный Recall при Precision ≥ 0,72) — на валидации 2025-H2.
Голова видов отказа (kind_probs): многоклассовая LightGBM на положительных срезах обучения.

Тест (2026) здесь НЕ читается — только в model/evaluate.py, один раз.

Запуск: python -m model.train_v3 [--version lgbm-2026.09-v3]
"""

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import MODELS  # noqa: E402
from model import experiments as ex  # noqa: E402

KIND_ORDER = ex.KIND_ORDER


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="lgbm-2026.09-v3")
    args = ap.parse_args()
    assert not ex.NEG_SAMPLE, "итоговая модель обучается на полных данных (без ML_NEG_SAMPLE)"
    out = MODELS / args.version
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cand = ex.train_candidate(label=ex.NEW_LABEL, weight_fn=ex.recency_weights)
    va, p, thr, b = cand["va"], cand["p"], cand["thr"], cand["booster"]
    m_va = ex.dispatcher_metrics(va, p, thr)
    base_va = ex.prf(va.y.values, va.faults_7d.values > 0)
    print(ex.summary_line("v3 валидация 2025-H2", m_va), flush=True)
    kind_metrics, kind_model = ex.kind_head(cand)
    print(json.dumps(kind_metrics, ensure_ascii=False), flush=True)

    b.save_model(str(out / "model.txt"), num_iteration=b.best_iteration)
    kind_model.save_model(str(out / "kind_model.txt"), num_iteration=kind_model.best_iteration)
    feats = cand["feats"]
    imp = pd.DataFrame({"feature": feats, "gain": b.feature_importance("gain")}).sort_values("gain", ascending=False)
    imp.to_csv(out / "feature_importance.csv", index=False)
    meta = {
        "model_version": args.version,
        "features": feats,
        "categorical": list(cand["cats"]),
        "categories": {c: [x if isinstance(x, str) else int(x) for x in v] for c, v in cand["cats"].items()},
        "threshold": thr,
        "thresholds_by_group_valid": cand["thr_groups"],
        "target_precision": ex.TARGET_PRECISION,
        "risk_levels": {"normal": "health >= 80", "attention": "50 <= health < 80", "risk": "20 <= health < 50",
                        "critical": "health < 20"},
        "calibration": {"x": cand["iso"].X_thresholds_.tolist(), "y": cand["iso"].y_thresholds_.tolist()},
        "best_iteration": b.best_iteration,
        "label": "b: Неисправен, Отключено устройство, Пропадание связи, Сбой значения; без массовых сбоев, "
                 "групповой тишины и дней без данных",
        "kinds": KIND_ORDER,
        "kind_head": kind_metrics,
        "weights": "полураспад 365 дней от последнего среза обучения",
        "periods": {"train": (ex.TRAIN_FROM, ex.fold_before(ex.FOLDS["2025-H2"][0])[1]), "valid": ex.FOLDS["2025-H2"]},
        "valid": {"model": m_va, "baseline": base_va, "rows": len(va), "positives": int(va.y.sum())},
        "seconds": round(time.time() - t0),
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    # нормы признаков по типу датчика (медиана на обучении) — для фраз «обычно …»
    check = ex.load(ex.FEAT, ex.TRAIN_FROM, ex.fold_before(ex.FOLDS["2025-H2"][0])[1], ex.NEW_LABEL,
                    columns=["ch", "T", "sensor_type", *[f for f in feats if f not in ex.CAT_COLS]])
    norms = check.groupby("sensor_type", observed=True)[[f for f in feats if f not in ex.CAT_COLS]].median(numeric_only=True)
    norms.to_parquet(out / "feature_norms.parquet")
    print(f"saved {out} за {time.time() - t0:.0f} с; top:\n{imp.head(20).to_string(index=False)}")


if __name__ == "__main__":
    main()
