"""Оценка на тесте 2026 года. Тест открывается ОДИН раз: после запуска в папке модели появляется
TEST_OPENED, повторный запуск без --force отказывается работать (модель и порог уже зафиксированы по валидации).

Пишет: data/models/<версия>/metrics.json, ml/artifacts/metrics.json (копия для репозитория),
графики docs/ml_pr_curve.png, docs/ml_feature_importance.png, docs/ml_by_sensor_type.png.

Запуск: python -m model.evaluate [--version lgbm-2026.09-v1]
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import average_precision_score, precision_recall_curve  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import MODELS, ROOT  # noqa: E402
from model.train import load, pr  # noqa: E402

TEST = ("2026-01-01", "2026-06-29")  # последний срез 29.06 00:00: горизонт закрывается 30.06, внутри данных
SLIDER = [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)]


def calibrate(meta: dict, raw: np.ndarray) -> np.ndarray:
    return np.interp(raw, meta["calibration"]["x"], meta["calibration"]["y"])


def prepare_frame(df: pd.DataFrame, meta: dict) -> pd.DataFrame:
    for c in meta["categorical"]:
        df[c] = pd.Categorical(df[c], categories=meta["categories"][c])
    return df


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="lgbm-2026.09-v2")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    mdir = MODELS / args.version
    marker = mdir / "TEST_OPENED"
    if marker.exists() and not args.force:
        sys.exit(f"Тест уже открывался ({marker.read_text()}). Метрики — в {mdir / 'metrics.json'}.")
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    marker.write_text(datetime.now().isoformat(timespec="seconds"))

    booster = lgb.Booster(model_file=str(mdir / "model.txt"))
    te = prepare_frame(load(*TEST), meta)
    p = calibrate(meta, booster.predict(te[meta["features"]]))
    y = te.y.values
    thr = meta["threshold"]
    pred = p >= thr
    n_days = te["T"].dt.normalize().nunique()

    overall = pr(y, pred) | {"pr_auc": float(average_precision_score(y, p)), "support": int(y.sum()), "rows": len(te)}
    baseline = pr(y, te.faults_7d.values > 0) | {"support": int(y.sum())}
    by_type = []
    for st, g in te.assign(p=p).groupby("sensor_type", observed=True):
        yy = g.y.values
        m = pr(yy, g.p.values >= thr) | {"sensor_type": st, "support": int(yy.sum()), "rows": len(g)}
        m["pr_auc"] = float(average_precision_score(yy, g.p.values)) if 0 < yy.sum() < len(yy) else None
        by_type.append(m)
    by_kind = te.assign(p=p, pred=pred)[te.y == 1].groupby("fault_kind", observed=True).agg(
        support=("y", "size"), recall=("pred", "mean")).reset_index().to_dict("records")

    # Precision@K: за каждые сутки K самых рискованных каналов — сколько реально отказали
    tp_k = {}
    ranked = te.assign(p=p).sort_values(["T", "p"], ascending=[True, False])
    ranked["rank"] = ranked.groupby("T").cumcount() + 1
    for k in (20, 50, 100):
        top = ranked[ranked["rank"] <= k]
        tp_k[k] = float(top.y.mean())
    # Упреждение: от среза с тревогой до момента отказа (по верным тревогам)
    tp_rows = te[(pred) & (y == 1)]
    lead_h = float(((tp_rows.fault_at - tp_rows["T"]).dt.total_seconds() / 3600).median()) if len(tp_rows) else None

    slider = []
    # строка рабочего порога считается по точному значению (как overall), в таблицу пишется округлённым
    for t in sorted(set(SLIDER) | {thr}):
        m = pr(y, p >= t)
        slider.append({"threshold": round(t, 4), "precision": m["precision"], "recall": m["recall"],
                       "alerts_per_day": (m["tp"] + m["fp"]) / n_days, "tp_per_day": m["tp"] / n_days,
                       "fp_per_day": m["fp"] / n_days, "is_selected": bool(t == thr)})
    daily = (te.assign(pred=pred.astype(int)).assign(tp=lambda d: d.pred * d.y)
             .groupby(te["T"].dt.date).agg(predicted=("pred", "sum"), actual=("y", "sum"), true_positive=("tp", "sum"))
             .reset_index().rename(columns={"T": "date"}))
    daily["date"] = daily["date"].astype(str)

    metrics = {
        "model_version": args.version, "test_period": TEST, "threshold": thr, "days": int(n_days),
        "overall": overall, "baseline": baseline, "by_sensor_type": by_type, "recall_by_fault_kind": by_kind,
        "precision_at_k": tp_k, "median_lead_time_h": lead_h, "thresholds": slider,
        "daily": daily.to_dict("records"), "valid": meta["valid"], "opened_at": marker.read_text(),
    }
    for path in (mdir / "metrics.json", ROOT / "ml" / "artifacts" / "metrics.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(metrics, ensure_ascii=False, indent=1, default=float), encoding="utf-8")

    # Графики
    docs = ROOT / "docs"
    prec, rec, _ = precision_recall_curve(y, p)
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(rec, prec, color="#2f6fdf", lw=2, label=f"LightGBM, PR-AUC = {overall['pr_auc']:.3f}")
    ax.scatter([overall["recall"]], [overall["precision"]], color="#d62728", zorder=5,
               label=f"рабочий порог {thr:.3f}: P = {overall['precision']:.2f}, R = {overall['recall']:.2f}")
    ax.scatter([baseline["recall"]], [baseline["precision"]], color="#7f7f7f", marker="s", zorder=5,
               label=f"базовая линия «отказ за 7 дней»: P = {baseline['precision']:.2f}, R = {baseline['recall']:.2f}")
    ax.axhline(0.7, color="#999", ls="--", lw=1)
    ax.axvline(0.5, color="#999", ls="--", lw=1)
    ax.set_xlabel("Recall"); ax.set_ylabel("Precision"); ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    ax.set_title(f"PR-кривая на тесте {TEST[0]} … {TEST[1]}")
    ax.legend(loc="upper right", fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(docs / "ml_pr_curve.png", dpi=120); plt.close(fig)

    imp = pd.read_csv(mdir / "feature_importance.csv").head(25)[::-1]
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.barh(imp.feature, imp.gain / imp.gain.sum() * 100, color="#2f6fdf")
    ax.set_xlabel("доля gain, % (топ-25)"); ax.set_title("Важность признаков")
    fig.tight_layout(); fig.savefig(docs / "ml_feature_importance.png", dpi=120); plt.close(fig)

    bt = pd.DataFrame(by_type).sort_values("support")
    fig, ax = plt.subplots(figsize=(8, 6))
    yy = np.arange(len(bt))
    ax.barh(yy - 0.2, bt.precision, 0.4, label="Precision", color="#2f6fdf")
    ax.barh(yy + 0.2, bt.recall, 0.4, label="Recall", color="#f28e2b")
    ax.set_yticks(yy, [f"{s} ({n})" for s, n in zip(bt.sensor_type, bt.support)], fontsize=8)
    ax.set_xlim(0, 1); ax.legend(); ax.set_title("Метрики по типам датчиков (в скобках — отказов на тесте)")
    fig.tight_layout(); fig.savefig(docs / "ml_by_sensor_type.png", dpi=120); plt.close(fig)

    print(json.dumps({k: metrics[k] for k in ("overall", "baseline", "precision_at_k", "median_lead_time_h",
                                               "recall_by_fault_kind")}, ensure_ascii=False, indent=1, default=float))
    print(pd.DataFrame(by_type)[["sensor_type", "support", "precision", "recall", "pr_auc"]].to_string(index=False))


if __name__ == "__main__":
    main()
