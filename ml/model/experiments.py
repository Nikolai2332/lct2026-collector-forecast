"""Эксперименты v3 на обучающих данных и валидации (тест 2026 здесь НЕ читается: все периоды < 2026-01-01).

- скользящая валидация по времени: фолды 2024-H1, 2024-H2, 2025-H1, 2025-H2 (обучение — всё до фолда, зазор — сутки);
- сравнение v2 и v3 на одной и той же метке (старой v2 и новой v3) на валидации 2025-H2;
- метрики с точки зрения диспетчера: доля эпизодов с предупреждением за 1–24 ч, медианное упреждение,
  тревог в сутки, Precision@20/50/100 за сутки, метрики по группам датчиков заказчика.

Запуск (примеры):
  python -m model.experiments labels                     # варианты метки a/b/c на фолдах
  python -m model.experiments compare                    # v2 vs v3 на обеих метках (валидация 2025-H2)
Результаты — data/experiments/*.json (числа для docs/ML_V3_PLAN.md).
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
from ml_paths import DATA, FEAT, FEAT_V2, MODELS  # noqa: E402

import ctypes
import os

EXP = DATA / "experiments"
# Подвыборка негативных срезов обучения (доля; вес негативов = 1/доля). Включается, только если памяти не хватает на
# полные данные; одна и та же доля и seed для всех вариантов метки и для v2/v3. Валидация всегда полная.
NEG_SAMPLE = float(os.environ["ML_NEG_SAMPLE"]) if os.environ.get("ML_NEG_SAMPLE") else None
MIN_FREE_GB = float(os.environ.get("ML_MIN_FREE_GB", "12"))


def free_memory_gb() -> float:
    """Свободная физическая память (Windows, GlobalMemoryStatusEx)."""
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


def check_memory(tag: str) -> None:
    free = free_memory_gb()
    print(f"[память] перед «{tag}»: свободно {free:.1f} ГБ (порог {MIN_FREE_GB:.0f} ГБ)"
          f"{'; подвыборка негативов ' + str(NEG_SAMPLE) if NEG_SAMPLE else ''}", flush=True)
    if free < MIN_FREE_GB:
        sys.exit(f"Мало свободной памяти ({free:.1f} ГБ) перед «{tag}» — прогон не начат")


def sample_negatives(tr: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray | None]:
    """Без NEG_SAMPLE — как есть. С ним — все положительные и доля NEG_SAMPLE негативов (seed 42), вес негативов 1/доля."""
    if not NEG_SAMPLE:
        return tr, None
    rng = np.random.default_rng(42)
    keep = (tr.y.values == 1) | (rng.random(len(tr)) < NEG_SAMPLE)
    tr = tr[keep].reset_index(drop=True)
    return tr, np.where(tr.y.values == 1, 1.0, 1.0 / NEG_SAMPLE).astype("float32")
EXP.mkdir(parents=True, exist_ok=True)
TEST_START = pd.Timestamp("2026-01-01")
TARGET_PRECISION = 0.72
# последний срез фолда — за сутки до конца периода (горизонт 24 ч закрывается внутри), следующий период — через сутки
FOLDS = {
    "2024-H1": ("2024-01-01", "2024-06-29"),
    "2024-H2": ("2024-07-01", "2024-12-30"),
    "2025-H1": ("2025-01-01", "2025-06-29"),
    "2025-H2": ("2025-07-01", "2025-12-30"),
}
TRAIN_FROM = "2023-01-01"
ID_COLS = {"ch", "T", "y", "fault_at", "fault_kind", "eligible", "last_fault_ts", "next_fault_ts", "next_fault_kind",
           "object_id"}
CAT_COLS = ["sensor_type", "system_type", "parent_id"]
GROUPS = {"Газовая охрана": "газовые", "Пожарная охрана": "пожарные", "Охранная подсистема": "охранные"}
KIND_ORDER = ["Пропадание связи", "Неисправен", "Отключено устройство", "Сбой значения"]
PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=127, min_data_in_leaf=200,
              feature_fraction=0.7, bagging_fraction=0.7, bagging_freq=1, lambda_l2=10.0,
              metric="average_precision", num_threads=30, verbose=-1, seed=42, max_cat_to_onehot=32, cat_smooth=20)


def fold_before(d_from: str) -> tuple[str, str]:
    """Обучение: TRAIN_FROM … за 2 дня до начала фолда (горизонт закрывается, зазор — сутки)."""
    end = (pd.Timestamp(d_from) - pd.Timedelta(days=2)).strftime("%Y-%m-%d")
    return TRAIN_FROM, end


_LABELS = None


def labels() -> pd.DataFrame:
    global _LABELS
    if _LABELS is None:
        _LABELS = pd.read_parquet(FEAT / "labels_daily.parquet")
        _LABELS = _LABELS[_LABELS["T"] < TEST_START]
    return _LABELS


def load(feat_dir: Path, d_from: str, d_to: str, label: str, columns: list[str] | None = None) -> pd.DataFrame:
    """Срезы периода с меткой `label` (a/b/c — варианты v3, v2 — старая метка) и её eligible."""
    assert pd.Timestamp(d_to) < TEST_START, "тест 2026 закрыт до его открытия"
    dset = ds.dataset(str(feat_dir / "daily"), format="parquet")
    flt = (ds.field("T") >= pd.Timestamp(d_from)) & (ds.field("T") <= pd.Timestamp(d_to))
    tbl = dset.to_table(filter=flt, columns=columns)
    # float64 → float32 ещё в Arrow: вдвое меньше памяти при переходе в pandas (32 ГБ на машине)
    import pyarrow as pa
    tbl = tbl.cast(pa.schema([f.with_type(pa.float32()) if pa.types.is_float64(f.type) else f for f in tbl.schema]))
    df = tbl.to_pandas(self_destruct=True)
    del tbl
    lab = labels()
    lab = lab[(lab["T"] >= pd.Timestamp(d_from)) & (lab["T"] <= pd.Timestamp(d_to))]
    cols = ["ch", "T", f"y_{label}", f"elig_{label}", f"kind_{label}", f"at_{label}", "outage_slice"]
    df = df.drop(columns=[c for c in ("y", "eligible", "fault_kind", "fault_at") if c in df.columns])
    df = df.merge(lab[cols], on=["ch", "T"], how="inner")
    df = df.rename(columns={f"y_{label}": "y", f"elig_{label}": "eligible", f"kind_{label}": "fault_kind",
                            f"at_{label}": "fault_at"})
    df = df[df["eligible"].fillna(False).astype(bool) & df["y"].notna() & ~df["outage_slice"]].copy()
    df["y"] = df["y"].astype(int)
    for c in CAT_COLS:
        if c in df.columns:
            df[c] = df[c].astype("category")
    return df.reset_index(drop=True)


def feature_columns(df: pd.DataFrame, drop: tuple[str, ...] = ()) -> list[str]:
    return [c for c in df.columns if c not in ID_COLS and c not in {"outage_slice"} and c not in drop]


def align_categories(*frames: pd.DataFrame) -> None:
    for c in CAT_COLS:
        cats = sorted(set().union(*[set(f[c].dropna().unique()) for f in frames if c in f.columns]))
        for f in frames:
            if c in f.columns:
                f[c] = pd.Categorical(f[c], categories=cats)


def fit(tr: pd.DataFrame, va: pd.DataFrame, feats: list[str], weight: np.ndarray | None = None,
        rounds: int = 3000, params: dict | None = None) -> tuple[lgb.Booster, IsotonicRegression]:
    wt = np.ones(len(tr)) if weight is None else weight
    pos_w = wt[tr.y.values == 0].sum() / max(wt[tr.y.values == 1].sum(), 1)  # с учётом веса подвыборки
    prm = dict(PARAMS) | {"scale_pos_weight": float(np.sqrt(pos_w))} | (params or {})
    cats = [c for c in CAT_COLS if c in feats]
    dtr = lgb.Dataset(tr[feats], tr.y, weight=weight, categorical_feature=cats, free_raw_data=True).construct()
    dva = lgb.Dataset(va[feats], va.y, categorical_feature=cats, reference=dtr).construct()
    b = lgb.train(prm, dtr, num_boost_round=rounds, valid_sets=[dva], valid_names=["valid"],
                  callbacks=[lgb.early_stopping(150, verbose=False)])
    raw = b.predict(va[feats], num_iteration=b.best_iteration)
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(raw, va.y)
    return b, iso


def threshold_for_precision(y: np.ndarray, p: np.ndarray, target: float = TARGET_PRECISION) -> float:
    prec, rec, thr = precision_recall_curve(y, p)
    ok = np.where(prec[:-1] >= target)[0]
    i = int(np.argmax(prec[:-1])) if len(ok) == 0 else ok[np.argmax(rec[:-1][ok])]
    return float(thr[i])


def prf(y: np.ndarray, pred: np.ndarray) -> dict:
    tp = int((pred & (y == 1)).sum()); fp = int((pred & (y == 0)).sum()); fn = int((~pred & (y == 1)).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(2 * p * r / (p + r) if p + r else 0.0, 4),
            "tp": tp, "fp": fp, "fn": fn}


def customer_group(system_type: pd.Series) -> pd.Series:
    return system_type.astype(str).map(GROUPS).fillna("прочие")


def dispatcher_metrics(df: pd.DataFrame, p: np.ndarray, thr) -> dict:
    """df: срезы с y, fault_at, fault_kind, T, system_type, sensor_type. thr — число или Series порогов по строкам."""
    y = df.y.values
    thr_arr = np.asarray(thr if np.ndim(thr) else np.full(len(df), thr), dtype=float)
    pred = p >= thr_arr
    n_days = df["T"].dt.normalize().nunique()
    out = prf(y, pred) | {"pr_auc": round(float(average_precision_score(y, p)), 4), "support": int(y.sum()),
                          "rows": int(len(df)), "alerts_per_day": round(float(pred.sum() / n_days), 1),
                          "fp_per_day": round(float((pred & (y == 0)).sum() / n_days), 1)}
    lead = (df.fault_at - df["T"]).dt.total_seconds().values / 3600
    ep = y == 1
    warned = pred & ep & (lead >= 1)
    out["episodes_warned_1_24h"] = round(float(warned.sum() / max(ep.sum(), 1)), 4)
    out["median_lead_h"] = round(float(np.median(lead[pred & ep])), 1) if (pred & ep).any() else None
    ranked = pd.DataFrame({"T": df["T"].values, "p": p, "y": y}).sort_values(["T", "p"], ascending=[True, False])
    ranked["rank"] = ranked.groupby("T").cumcount() + 1
    out["precision_at_k"] = {k: round(float(ranked[ranked["rank"] <= k].y.mean()), 4) for k in (20, 50, 100)}
    kinds = {}
    for k in KIND_ORDER:
        m = ep & (df.fault_kind.values == k)
        if m.sum():
            kinds[k] = {"support": int(m.sum()), "recall": round(float(pred[m].mean()), 4)}
    out["recall_by_kind"] = kinds
    grp = customer_group(df.system_type)
    out["by_group"] = {}
    for g in ["газовые", "пожарные", "охранные", "прочие"]:
        m = (grp == g).values
        if m.sum() and y[m].sum():
            out["by_group"][g] = prf(y[m], pred[m]) | {
                "pr_auc": round(float(average_precision_score(y[m], p[m])), 4), "support": int(y[m].sum()),
                "alerts_per_day": round(float(pred[m].sum() / n_days), 1)}
    out["by_type"] = {}
    for st in sorted(df.sensor_type.astype(str).unique()):
        m = (df.sensor_type.astype(str) == st).values
        if y[m].sum():
            out["by_type"][st] = prf(y[m], pred[m]) | {"support": int(y[m].sum())}
    return out


def save(name: str, obj) -> None:
    (EXP / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


def summary_line(tag: str, m: dict) -> str:
    rk = m["recall_by_kind"]
    g = m["by_group"]
    return (f"{tag:<34} P {m['precision']:.3f} R {m['recall']:.3f} F1 {m['f1']:.3f} AUC {m['pr_auc']:.3f} "
            f"| связь {rk.get('Пропадание связи', {}).get('recall', 0):.3f} неиспр {rk.get('Неисправен', {}).get('recall', 0):.3f} "
            f"откл {rk.get('Отключено устройство', {}).get('recall', 0):.3f} сбой {rk.get('Сбой значения', {}).get('recall', 0):.3f} "
            f"| газ {g.get('газовые', {}).get('pr_auc', 0):.3f} пож {g.get('пожарные', {}).get('pr_auc', 0):.3f} "
            f"охр {g.get('охранные', {}).get('pr_auc', 0):.3f} | alerts/d {m['alerts_per_day']}")


def run_fold(fold: str, label: str, feat_dir: Path = FEAT, drop: tuple[str, ...] = (), weight_fn=None,
             train_from: str = TRAIN_FROM, params: dict | None = None, keep: bool = False):
    check_memory(f"{label} {fold}")
    a, b = FOLDS[fold]
    tr, ws = sample_negatives(load(feat_dir, train_from, fold_before(a)[1], label))
    va = load(feat_dir, a, b, label)
    align_categories(tr, va)
    feats = feature_columns(tr, drop)
    w = weight_fn(tr) if weight_fn else None
    w = ws if w is None else (w if ws is None else w * ws)
    t0 = time.time()
    info = {"train_rows": len(tr), "train_pos": int(tr.y.sum())}
    booster, iso = fit(tr, va, feats, w, params=params)
    del tr
    raw = booster.predict(va[feats], num_iteration=booster.best_iteration)
    p = iso.predict(raw)
    thr = threshold_for_precision(va.y.values, p)
    m = dispatcher_metrics(va, p, thr) | {"threshold": round(thr, 4), "best_iteration": booster.best_iteration,
                                          **info, "features": len(feats),
                                          "seconds": round(time.time() - t0)}
    if keep:
        return m, booster, iso, feats, va, p
    return m


def cmd_labels() -> None:
    """Варианты метки на фолдах: сколько положительных и насколько они предсказуемы (одинаковые признаки v3)."""
    res = json.loads((EXP / "labels_variants.json").read_text(encoding="utf-8")) if (EXP / "labels_variants.json").exists() else {}
    for label in ("a", "b"):  # c ≡ b: служебных кодов с флагом «тревожное» в 2022–2025 нет
        for fold in ("2025-H1", "2025-H2"):
            if f"{label}/{fold}" in res:
                print(summary_line(f"label {label} {fold} (готово)", res[f"{label}/{fold}"]), flush=True)
                continue
            m = run_fold(fold, label)
            res[f"{label}/{fold}"] = m
            print(summary_line(f"label {label} {fold}", m), flush=True)
            save("labels_variants", res)


def v2_scores(d_from: str, d_to: str) -> pd.DataFrame:
    """Калиброванные вероятности модели v2 на её собственных признаках (data/features/daily) — для сравнения."""
    mdir = MODELS / "lgbm-2026.09-v2"
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    b = lgb.Booster(model_file=str(mdir / "model.txt"))
    dset = ds.dataset(str(FEAT_V2 / "daily"), format="parquet")
    flt = (ds.field("T") >= pd.Timestamp(d_from)) & (ds.field("T") <= pd.Timestamp(d_to))
    assert pd.Timestamp(d_to) < TEST_START
    df = dset.to_table(filter=flt).to_pandas()
    for c in meta["categorical"]:
        df[c] = pd.Categorical(df[c], categories=meta["categories"][c])
    raw = b.predict(df[meta["features"]], num_threads=30)
    p = np.interp(raw, meta["calibration"]["x"], meta["calibration"]["y"])
    return pd.DataFrame({"ch": df.ch.values, "T": df["T"].values, "p_v2": p}), meta["threshold"]




NEW_LABEL = "b"


def group_thresholds(df: pd.DataFrame, p: np.ndarray, global_thr: float) -> dict:
    """Порог на группу датчиков заказчика: максимальный Recall при Precision ≥ 0,72 на валидации;
    если группа не достигает 0,72 ни при каком пороге — общий порог."""
    grp = customer_group(df.system_type).values
    out = {}
    for g in ["газовые", "пожарные", "охранные", "прочие"]:
        m = grp == g
        y = df.y.values[m]
        prec, rec, thr = precision_recall_curve(y, p[m])
        ok = np.where(prec[:-1] >= TARGET_PRECISION)[0]
        out[g] = float(thr[ok[np.argmax(rec[:-1][ok])]]) if len(ok) else global_thr
    return out


def row_thresholds(system_type: pd.Series, thr_by_group: dict) -> np.ndarray:
    return customer_group(system_type).map(thr_by_group).values.astype(float)


def train_candidate(label: str = NEW_LABEL, drop: tuple[str, ...] = (), weight_fn=None, train_from: str = TRAIN_FROM,
                    params: dict | None = None) -> dict:
    """Кандидат v3: обучение 2023-01-01 … 2025-06-29, ранняя остановка, калибровка и порог — на валидации 2025-H2."""
    check_memory(f"кандидат v3, метка {label}")
    a, b = FOLDS["2025-H2"]
    tr, ws = sample_negatives(load(FEAT, train_from, fold_before(a)[1], label))
    va = load(FEAT, a, b, label)
    align_categories(tr, va)
    feats = feature_columns(tr, drop)
    cats = {c: list(tr[c].cat.categories) for c in CAT_COLS if c in feats}
    w = weight_fn(tr) if weight_fn else None
    w = ws if w is None else (w if ws is None else w * ws)
    booster, iso = fit(tr, va, feats, w, params=params)
    del tr
    p = iso.predict(booster.predict(va[feats], num_iteration=booster.best_iteration))
    thr = threshold_for_precision(va.y.values, p)
    return {"booster": booster, "iso": iso, "feats": feats, "cats": cats, "va": va, "p": p, "thr": thr,
            "thr_groups": group_thresholds(va, p, thr)}


def score_all(cand: dict, d_from: str, d_to: str) -> pd.DataFrame:
    """Прогноз v3 для всех срезов периода (не только eligible по новой метке) — для оценки на старой метке."""
    assert pd.Timestamp(d_to) < TEST_START
    dset = ds.dataset(str(FEAT / "daily"), format="parquet")
    flt = (ds.field("T") >= pd.Timestamp(d_from)) & (ds.field("T") <= pd.Timestamp(d_to))
    df = dset.to_table(filter=flt, columns=["ch", "T", *cand["feats"]]).to_pandas()
    for c, cats in cand["cats"].items():
        df[c] = pd.Categorical(df[c], categories=cats)
    raw = cand["booster"].predict(df[cand["feats"]], num_iteration=cand["booster"].best_iteration, num_threads=30)
    return pd.DataFrame({"ch": df.ch.values, "T": df["T"].values, "p_v3": cand["iso"].predict(raw),
                         "system_type": df.system_type.astype(str).values})


def compare_on_labels(cand: dict, d_from: str, d_to: str, tag: str) -> dict:
    """v2 и v3 на старой (v2) и новой метке на одних и тех же срезах. Срезы без прогноза одной из моделей
    (канал активен только по данным одной версии) получают p = 0; их число пишется в coverage."""
    s2, thr_v2 = v2_scores(d_from, d_to)
    s3 = score_all(cand, d_from, d_to)
    info = pd.concat([
        pd.read_parquet(FEAT / "daily", columns=["ch", "T", "system_type", "sensor_type"],
                        filters=[("T", ">=", pd.Timestamp(d_from)), ("T", "<=", pd.Timestamp(d_to))]),
        pd.read_parquet(FEAT_V2 / "daily", columns=["ch", "T", "system_type", "sensor_type"],
                        filters=[("T", ">=", pd.Timestamp(d_from)), ("T", "<=", pd.Timestamp(d_to))]),
    ]).drop_duplicates(["ch", "T"])
    info["system_type"] = info.system_type.astype(str)
    info["sensor_type"] = info.sensor_type.astype(str)
    res = {}
    lab_all = labels()
    lab_all = lab_all[(lab_all["T"] >= pd.Timestamp(d_from)) & (lab_all["T"] <= pd.Timestamp(d_to))]
    for label in ("v2", NEW_LABEL):
        lab = lab_all[lab_all[f"elig_{label}"].fillna(False).astype(bool) & lab_all[f"y_{label}"].notna()]
        if label != "v2":
            lab = lab[~lab.outage_slice]
        df = lab[["ch", "T", f"y_{label}", f"kind_{label}", f"at_{label}"]].rename(
            columns={f"y_{label}": "y", f"kind_{label}": "fault_kind", f"at_{label}": "fault_at"})
        df = df.merge(info, on=["ch", "T"], how="left").merge(s2, on=["ch", "T"], how="left")
        df = df.merge(s3[["ch", "T", "p_v3"]], on=["ch", "T"], how="left")
        cov = {"rows": len(df), "no_v2": int(df.p_v2.isna().sum()), "no_v3": int(df.p_v3.isna().sum())}
        df["y"] = df.y.astype(int)
        p3 = df.p_v3.fillna(0).values
        res[label] = {"coverage": cov,
                      "v2": dispatcher_metrics(df, df.p_v2.fillna(0).values, thr_v2),
                      "v3": dispatcher_metrics(df, p3, cand["thr"]),
                      "v3_group_thr": dispatcher_metrics(df, p3, row_thresholds(df.system_type, cand["thr_groups"]))}
        for k in ("v2", "v3", "v3_group_thr"):
            print(summary_line(f"{tag} метка {label}: {k}", res[label][k]), flush=True)
        print("   coverage", cov, flush=True)
    return res


def recency_weights(tr: pd.DataFrame, half_life_days: float = 365.0) -> np.ndarray:
    age = (tr["T"].max() - tr["T"]).dt.days.values
    return np.power(0.5, age / half_life_days).astype("float32")


V2_LIKE_DROP = (  # признаки, которых не было в v2: для абляции «данные и метка v3, признаки v2»
    "sentinel_24h", "sentinel_7d", "sentinel_30d", "date1970_24h", "date1970_7d", "date1970_30d", "sentinel_share_7d",
    "date_current_7d", "gas1_work_24h", "gas1_work_7d", "gas1_offhours_24h", "gas1_offhours_7d", "gas_detected_24h",
    "gas_detected_7d", "gas5_7d", "gas5_30d", "fault_episodes_30d", "fault_episodes_90d", "disc_episodes_30d",
    "disc_episodes_90d", "value_failures_30d", "value_failures_90d", "group_silences_30d", "group_silences_90d",
    "recoveries_90d", "recovery_h_90d", "type_fault_rate_24h", "type_fault_rate_7d", "type_fault_msg_rate_7d",
    "type_fault_trend_7d_90d", "zero_days_30d", "daily_cv_30d", "history_days", "nb_silent_share")


def cmd_variants() -> None:
    """Что даёт каждое изменение (фолды 2025-H1 и 2025-H2, метка NEW_LABEL): признаки v2 vs v3, окно обучения,
    веса свежих данных."""
    res = json.loads((EXP / "variants.json").read_text(encoding="utf-8")) if (EXP / "variants.json").exists() else {}
    configs = {
        "v3 признаки, с 2023": dict(),
        "признаки v2 (данные и метка v3)": dict(drop=V2_LIKE_DROP),
        "v3 признаки, с 2024": dict(train_from="2024-01-01"),
        "v3 признаки, веса свежих (полураспад 1 год)": dict(weight_fn=recency_weights),
    }
    for name, cfg in configs.items():
        for fold in ("2024-H2", "2025-H1", "2025-H2"):
            key = f"{name} / {fold}"
            if key in res:
                continue
            if fold == "2024-H2" and cfg.get("train_from") == "2024-01-01":
                continue
            m = run_fold(fold, NEW_LABEL, **cfg)
            res[key] = m
            print(summary_line(key, m), flush=True)
            save("variants", res)


def cmd_compare() -> None:
    """Кандидат v3 против v2 на валидации 2025-H2 на старой и новой метке (+ пороги по группам)."""
    cand = train_candidate()
    res = compare_on_labels(cand, *FOLDS["2025-H2"], "валидация 2025-H2")
    res["candidate"] = {"threshold": cand["thr"], "thr_groups": cand["thr_groups"],
                        "best_iteration": cand["booster"].best_iteration, "features": len(cand["feats"])}
    imp = pd.DataFrame({"feature": cand["feats"], "gain": cand["booster"].feature_importance("gain")})
    imp["share"] = imp.gain / imp.gain.sum()
    res["importance_top30"] = imp.sort_values("gain", ascending=False).head(30)[["feature", "share"]].to_dict("records")
    save("compare_valid", res)


def kind_head(cand: dict, label: str = NEW_LABEL) -> dict:
    """B. Вид отказа: многоклассовая LightGBM на положительных срезах обучения (какой вид будет первым),
    kind_probs = p(отказ) × P(вид | отказ). Метрики на положительных срезах валидации."""
    a, b = FOLDS["2025-H2"]
    check_memory("голова видов отказа")
    tr = load(FEAT, TRAIN_FROM, fold_before(a)[1], label)
    tr = tr[tr.y == 1].reset_index(drop=True)
    va = cand["va"]
    for c, cats in cand["cats"].items():
        tr[c] = pd.Categorical(tr[c], categories=cats)
    feats = cand["feats"]
    k2i = {k: i for i, k in enumerate(KIND_ORDER)}
    ytr = tr.fault_kind.map(k2i).values
    vpos = va[va.y == 1]
    yva = vpos.fault_kind.map(k2i).values
    prm = dict(objective="multiclass", num_class=len(KIND_ORDER), learning_rate=0.05, num_leaves=63, min_data_in_leaf=100,
               feature_fraction=0.7, bagging_fraction=0.8, bagging_freq=1, lambda_l2=10.0, num_threads=30, verbose=-1,
               seed=42, metric="multi_logloss")
    cats = list(cand["cats"])
    dtr = lgb.Dataset(tr[feats], ytr, categorical_feature=cats)
    dva = lgb.Dataset(vpos[feats], yva, categorical_feature=cats, reference=dtr)
    m = lgb.train(prm, dtr, 2000, valid_sets=[dva], callbacks=[lgb.early_stopping(100, verbose=False)])
    pk = m.predict(vpos[feats], num_iteration=m.best_iteration)
    top = pk.argmax(1)
    out = {"best_iteration": m.best_iteration, "top1_accuracy": round(float((top == yva).mean()), 4),
           "baseline_majority": round(float((yva == np.bincount(ytr).argmax()).mean()), 4), "per_kind": {}}
    for k, i in k2i.items():
        mk = yva == i
        if mk.sum():
            out["per_kind"][k] = {"support": int(mk.sum()), "recall_top1": round(float((top[mk] == i).mean()), 4),
                                  "precision_top1": round(float((yva[top == i] == i).mean()), 4) if (top == i).any() else None,
                                  "mean_prob": round(float(pk[mk, i].mean()), 4)}
    return out, m


def status_head(cand: dict, label: str = NEW_LABEL) -> dict:
    """A. Отдельная голова для статусных отказов («Неисправен», «Отключено устройство», «Сбой значения»):
    бинарная модель «будет ли статусный отказ за 24 ч»; объединение с основной: p = max(p_main, p_status)."""
    a, b = FOLDS["2025-H2"]
    status = ["Неисправен", "Отключено устройство", "Сбой значения"]
    check_memory("голова статусных отказов")
    tr, ws = sample_negatives(load(FEAT, TRAIN_FROM, fold_before(a)[1], label))
    va = cand["va"].copy()
    for c, cats in cand["cats"].items():
        tr[c] = pd.Categorical(tr[c], categories=cats)
    for d in (tr, va):
        d["y_status"] = d.fault_kind.isin(status).astype(int)
    feats = cand["feats"]
    t2 = tr.assign(y=tr.y_status)
    v2 = va.assign(y=va.y_status)
    del tr
    b_s, iso_s = fit(t2, v2, feats, ws)
    ps = iso_s.predict(b_s.predict(va[feats], num_iteration=b_s.best_iteration))
    res = {"status_head_pr_auc_on_status": round(float(average_precision_score(va.y_status, ps)), 4),
           "main_pr_auc_on_status": round(float(average_precision_score(va.y_status, cand["p"])), 4),
           "best_iteration": b_s.best_iteration}
    pc = np.maximum(cand["p"], ps)
    thr_c = threshold_for_precision(va.y.values, pc)
    res["combined"] = dispatcher_metrics(va, pc, thr_c) | {"threshold": thr_c}
    res["main"] = dispatcher_metrics(va, cand["p"], cand["thr"])
    print(summary_line("основная", res["main"]))
    print(summary_line("основная + голова статусов (max)", res["combined"]))
    return res


def cmd_heads() -> None:
    cand = train_candidate()
    res = {}
    kh, _ = kind_head(cand)
    res["kind_head"] = kh
    print(json.dumps(kh, ensure_ascii=False, indent=1))
    res["status_head"] = status_head(cand)
    save("heads", res)


def cmd_precursors() -> None:
    """A. Предвестники статусных отказов на обучении (2023-01 … 2025-06): доля срезов с отказом вида k в следующие
    24 ч при наличии признака-предвестника и без него (lift)."""
    check_memory("предвестники")
    tr = load(FEAT, TRAIN_FROM, "2025-06-29", NEW_LABEL)
    conds = {
        "служебные значения за 7 дней > 0": tr.sentinel_7d > 0,
        "значения 01.01.1970 за 7 дней > 0": tr.date1970_7d > 0,
        "доля «Неопределен» за сутки ≥ 20 %": tr.unc_share_24h >= 0.2,
        "смен статуса за сутки ≥ 10 (дребезг)": tr.status_changes_24h >= 10,
        "«Обесточен» у фаз объекта за сутки > 0": tr.obj_phase_off_share_24h > 0,
        "отказы соседей по объекту за сутки > 0": tr.obj_faults_24h > 0,
        "«Неисправен» у канала за 90 дней > 0 (повторяемость)": tr.fault_episodes_90d > 0,
        "сообщения «Неисправен» за 7 дней > 0": tr.fault_msgs_7d > 0,
        "залипание показаний за сутки ≥ 90 %": tr.stuck_share_24h >= 0.9,
        "газ ≥ 1 % вне рабочего времени за 7 дней > 0": tr.gas1_offhours_7d > 0,
    }
    out = []
    for kind in ["Неисправен", "Отключено устройство", "Сбой значения", "Пропадание связи"]:
        yk = (tr.fault_kind == kind).values
        for name, m in conds.items():
            m = m.fillna(False).values
            if m.sum() < 100:
                continue
            r1, r0 = yk[m].mean(), yk[~m].mean()
            out.append({"kind": kind, "precursor": name, "slices_with": int(m.sum()), "rate_with": round(float(r1), 5),
                        "rate_without": round(float(r0), 5), "lift": round(float(r1 / r0), 1) if r0 else None,
                        "share_of_kind_covered": round(float(yk[m].sum() / max(yk.sum(), 1)), 3)})
    for pt in ["Состояние насоса", "Состояние вентилятора"]:
        sub = tr[tr.sensor_type.astype(str) == pt]
        yk = (sub.fault_kind == "Неисправен")
        out.append({"kind": "Неисправен", "precursor": f"{pt}: доля отказов по месяцам",
                    "by_month": {int(k): round(float(v), 4) for k, v in yk.groupby(sub.cal_month).mean().items()}})
    df = pd.DataFrame([o for o in out if "lift" in o])
    print(df.to_string())
    save("precursors", out)


class _Iso:
    def __init__(self, cal: dict):
        self.x, self.y = cal["x"], cal["y"]

    def predict(self, raw):
        return np.interp(raw, self.x, self.y)


def load_saved(version: str) -> dict:
    """Сохранённая модель в формате кандидата (для сравнения v2/v3 на валидации)."""
    mdir = MODELS / version
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
    b = lgb.Booster(model_file=str(mdir / "model.txt"))
    b.best_iteration = 0
    return {"booster": b, "iso": _Iso(meta["calibration"]), "feats": meta["features"], "cats": meta["categories"],
            "thr": meta["threshold"], "thr_groups": meta["thresholds_by_group_valid"], "meta": meta}


def cmd_final() -> None:
    """Итоговая v3 против v2 на валидации 2025-H2 на старой и новой метке — таблица для решения перед открытием теста."""
    check_memory("сравнение итоговой v3 с v2")
    cand = load_saved("lgbm-2026.09-v3")
    res = compare_on_labels(cand, *FOLDS["2025-H2"], "итог v3, валидация 2025-H2")
    save("final_valid", res)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["labels", "variants", "compare", "heads", "precursors", "final"])
    args = ap.parse_args()
    {"labels": cmd_labels, "variants": cmd_variants, "compare": cmd_compare, "heads": cmd_heads,
     "precursors": cmd_precursors, "final": cmd_final}[args.cmd]()


if __name__ == "__main__":
    main()
