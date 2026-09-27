"""Оценка v3 на тесте 2026 (01.01–29.06). Тест открывается ОДИН раз для итоговой v3 и для переоценки v2 на новой
метке (основание — официальные ответы заказчика 17–18.09 и 23.09, docs/ML_V3_PLAN.md). После запуска в папке модели
появляется TEST_OPENED; повторный запуск без --force отказывается работать. Модель, порог и правило выбора
зафиксированы по валидации до открытия теста.

Пишет: data/models/lgbm-2026.09-v3/metrics.json, ml/artifacts/metrics_v3.json, графики docs/ml_v3_*.png.
Метрики v2 (ml/artifacts/metrics.json, тест 25.09 на старой метке) не перезаписываются.

Запуск: python -m model.evaluate_v3
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
import pyarrow as pa  # noqa: E402
import pyarrow.dataset as ds  # noqa: E402
from sklearn.metrics import average_precision_score, precision_recall_curve  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ml_paths import FEAT, FEAT_V2, MODELS, ROOT  # noqa: E402
from model import experiments as ex  # noqa: E402

VERSION = "lgbm-2026.09-v3"
TEST = ("2026-01-01", "2026-06-29")
SLIDER = [round(x, 2) for x in np.arange(0.05, 0.96, 0.05)]


def read(feat_dir: Path, columns=None) -> pd.DataFrame:
    dset = ds.dataset(str(feat_dir / "daily"), format="parquet")
    flt = (ds.field("T") >= pd.Timestamp(TEST[0])) & (ds.field("T") <= pd.Timestamp(TEST[1]))
    t = dset.to_table(filter=flt, columns=columns)
    t = t.cast(pa.schema([f.with_type(pa.float32()) if pa.types.is_float64(f.type) else f for f in t.schema]))
    return t.to_pandas(self_destruct=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-valid", action="store_true",
                    help="проверка кода на валидации 2025-H2: тест не читается, TEST_OPENED не пишется")
    args = ap.parse_args()
    global TEST
    mdir = MODELS / VERSION
    marker = mdir / "TEST_OPENED"
    out_dirs = (mdir / "metrics.json", ROOT / "ml" / "artifacts" / "metrics_v3.json")
    docs = ROOT / "docs"
    if args.dry_valid:
        TEST = ex.FOLDS["2025-H2"]
        dry = ex.EXP / "dry_valid"
        dry.mkdir(exist_ok=True)
        marker, out_dirs, docs = dry / "TEST_OPENED_dry", (dry / "metrics.json",), dry
    elif marker.exists() and not args.force:
        sys.exit(f"Тест уже открывался ({marker.read_text()}). Метрики — в {mdir / 'metrics.json'}.")
    ex.check_memory("тест 2026")
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    marker.write_text(datetime.now().isoformat(timespec="seconds"))

    # v3: прогноз для всех срезов теста
    f3 = read(FEAT, list(dict.fromkeys(["ch", "T", "sensor_type", "system_type", "faults_7d", *meta["features"]])))
    for c in meta["categorical"]:
        f3[c] = pd.Categorical(f3[c], categories=meta["categories"][c])
    b3 = lgb.Booster(model_file=str(mdir / "model.txt"))
    raw = b3.predict(f3[meta["features"]], num_threads=30)
    f3["p_v3"] = np.interp(raw, meta["calibration"]["x"], meta["calibration"]["y"])
    kind_b = lgb.Booster(model_file=str(mdir / "kind_model.txt"))
    # v2: прогноз на её собственных признаках
    m2 = json.loads((MODELS / "lgbm-2026.09-v2" / "meta.json").read_text(encoding="utf-8"))
    f2 = read(FEAT_V2, list(dict.fromkeys(["ch", "T", *m2["features"]])))
    for c in m2["categorical"]:
        f2[c] = pd.Categorical(f2[c], categories=m2["categories"][c])
    b2 = lgb.Booster(model_file=str(MODELS / "lgbm-2026.09-v2" / "model.txt"))
    f2["p_v2"] = np.interp(b2.predict(f2[m2["features"]], num_threads=30), m2["calibration"]["x"], m2["calibration"]["y"])
    s2 = f2[["ch", "T", "p_v2"]]
    del f2

    lab = pd.read_parquet(FEAT / "labels_daily.parquet",
                          filters=[("T", ">=", pd.Timestamp(TEST[0])), ("T", "<=", pd.Timestamp(TEST[1]))])
    info = f3[["ch", "T", "sensor_type", "system_type", "faults_7d", "p_v3"]].copy()
    info["sensor_type"] = info.sensor_type.astype(str)
    info["system_type"] = info.system_type.astype(str)
    res = {}
    frames = {}
    for label in ("b", "v2"):
        l = lab[lab[f"elig_{label}"].fillna(False).astype(bool) & lab[f"y_{label}"].notna()]
        if label != "v2":
            l = l[~l.outage_slice]
        df = l[["ch", "T", f"y_{label}", f"kind_{label}", f"at_{label}"]].rename(
            columns={f"y_{label}": "y", f"kind_{label}": "fault_kind", f"at_{label}": "fault_at"})
        df = df.merge(info, on=["ch", "T"], how="left").merge(s2, on=["ch", "T"], how="left")
        cov = {"rows": len(df), "no_v2": int(df.p_v2.isna().sum()), "no_v3": int(df.p_v3.isna().sum())}
        # срезы без признаков v3 (канал активен только по данным v2): тип и система — из справочника v2-признаков
        df["y"] = df.y.astype(int)
        df["system_type"] = df.system_type.fillna("прочие")
        df["sensor_type"] = df.sensor_type.fillna("нет")
        frames[label] = df
        res[label] = {"coverage": cov,
                      "v2": ex.dispatcher_metrics(df, df.p_v2.fillna(0).values, m2["threshold"]),
                      "v3": ex.dispatcher_metrics(df, df.p_v3.fillna(0).values, meta["threshold"])}
        for k in ("v2", "v3"):
            print(ex.summary_line(f"ТЕСТ 2026 метка {label}: {k}", res[label][k]), flush=True)
        print("   coverage", cov, flush=True)

    # Основной формат (как у v2, для загрузки в model_metrics / model_thresholds): v3 на новой метке
    df = frames["b"]
    y, p, thr = df.y.values, df.p_v3.fillna(0).values, meta["threshold"]
    n_days = df["T"].dt.normalize().nunique()
    m = res["b"]["v3"]
    overall = ex.prf(y, p >= thr) | {"pr_auc": m["pr_auc"], "support": int(y.sum()), "rows": len(df)}
    baseline = ex.prf(y, df.faults_7d.fillna(0).values > 0) | {"support": int(y.sum())}
    by_type = []
    for st, g in df.assign(p=p).groupby("sensor_type"):
        yy = g.y.values
        r = ex.prf(yy, g.p.values >= thr) | {"sensor_type": st, "support": int(yy.sum()), "rows": len(g)}
        r["pr_auc"] = float(average_precision_score(yy, g.p.values)) if 0 < yy.sum() < len(yy) else None
        by_type.append(r)
    slider = []
    for t in sorted(set(SLIDER) | {thr}):
        mm = ex.prf(y, p >= t)
        slider.append({"threshold": round(t, 4), "precision": mm["precision"], "recall": mm["recall"],
                       "alerts_per_day": (mm["tp"] + mm["fp"]) / n_days, "tp_per_day": mm["tp"] / n_days,
                       "fp_per_day": mm["fp"] / n_days, "is_selected": bool(t == thr)})
    # голова видов на положительных срезах теста
    pos = f3.merge(df[df.y == 1][["ch", "T", "fault_kind"]], on=["ch", "T"])
    pk = kind_b.predict(pos[meta["features"]], num_threads=30)
    k2i = {k: i for i, k in enumerate(meta["kinds"])}
    yk = pos.fault_kind.map(k2i).values
    top = pk.argmax(1)
    kind_test = {"top1_accuracy": round(float((top == yk).mean()), 4),
                 "baseline_majority": round(float((yk == 0).mean()), 4),
                 "per_kind": {k: {"support": int((yk == i).sum()), "recall_top1": round(float((top[yk == i] == i).mean()), 4)
                                  if (yk == i).any() else None} for k, i in k2i.items()}}
    print("вид отказа на тесте:", json.dumps(kind_test, ensure_ascii=False), flush=True)

    metrics = {
        "model_version": VERSION, "test_period": TEST, "threshold": thr, "days": int(n_days),
        "label": meta["label"], "overall": overall, "baseline": baseline, "by_sensor_type": by_type,
        "recall_by_fault_kind": [{"fault_kind": k, **v} for k, v in m["recall_by_kind"].items()],
        "precision_at_k": {int(k): v for k, v in m["precision_at_k"].items()}, "median_lead_time_h": m["median_lead_h"],
        "episodes_warned_1_24h": m["episodes_warned_1_24h"], "alerts_per_day": m["alerts_per_day"],
        "fp_per_day": m["fp_per_day"], "by_group": m["by_group"], "thresholds": slider,
        "kind_head_test": kind_test,
        "compare": res,  # v2 и v3 на новой (b) и старой (v2) метке
        "v2_history": {"note": "тест v2 25.09 на старой метке", "precision": 0.620, "recall": 0.703, "pr_auc": 0.683},
        "valid": meta["valid"], "opened_at": marker.read_text(),
        "reopen_note": "тестовый период просмотрен повторно для v3 и для переоценки v2 на новой метке; основание — "
                       "официальные ответы заказчика от 17–18.09 и 23.09",
    }
    for path in out_dirs:
        path.write_text(json.dumps(metrics, ensure_ascii=False, indent=1, default=float), encoding="utf-8")

    fig, ax = plt.subplots(figsize=(7, 5))
    for col, name, color, t in (("p_v3", "v3", "#2f6fdf", thr), ("p_v2", "v2", "#999999", m2["threshold"])):
        pp = df[col].fillna(0).values
        pr_, rc_, _ = precision_recall_curve(y, pp)
        mm = res["b"][name]
        ax.plot(rc_, pr_, color=color, lw=2, label=f"{name}, PR-AUC = {mm['pr_auc']:.3f}")
        ax.scatter([mm["recall"]], [mm["precision"]], color=color, zorder=5, edgecolor="black",
                   label=f"{name}, порог {t:.3f}: P = {mm['precision']:.2f}, R = {mm['recall']:.2f}")
    ax.axhline(0.7, color="#bbb", ls="--", lw=1); ax.axvline(0.5, color="#bbb", ls="--", lw=1)
    ax.set_xlabel("Recall"); ax.set_ylabel("Precision"); ax.set_xlim(0, 1); ax.set_ylim(0, 1.02)
    ax.set_title("Тест 2026, новая метка (b): v3 и v2"); ax.legend(loc="lower left", fontsize=8); ax.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(docs / "ml_v3_pr_curve.png", dpi=120); plt.close(fig)

    imp = pd.read_csv(mdir / "feature_importance.csv").head(25)[::-1]
    fig, ax = plt.subplots(figsize=(7, 7))
    ax.barh(imp.feature, imp.gain / imp.gain.sum() * 100, color="#2f6fdf")
    ax.set_xlabel("доля gain, % (топ-25)"); ax.set_title("Важность признаков v3")
    fig.tight_layout(); fig.savefig(docs / "ml_v3_feature_importance.png", dpi=120); plt.close(fig)

    g = pd.DataFrame([{"group": k, "v2": res["b"]["v2"]["by_group"].get(k, {}).get("pr_auc", 0), "v3": v["pr_auc"]}
                      for k, v in m["by_group"].items()])
    fig, ax = plt.subplots(figsize=(7, 4))
    xx = np.arange(len(g))
    ax.bar(xx - 0.2, g.v2, 0.4, label="v2", color="#999999"); ax.bar(xx + 0.2, g.v3, 0.4, label="v3", color="#2f6fdf")
    ax.set_xticks(xx, g.group); ax.set_ylim(0, 1); ax.set_ylabel("PR-AUC"); ax.legend()
    ax.set_title("Тест 2026, новая метка: PR-AUC по группам датчиков заказчика")
    fig.tight_layout(); fig.savefig(docs / "ml_v3_by_group.png", dpi=120); plt.close(fig)
    print(pd.DataFrame(by_type)[["sensor_type", "support", "precision", "recall", "pr_auc"]].to_string(index=False))


if __name__ == "__main__":
    main()
