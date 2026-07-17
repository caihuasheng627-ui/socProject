"""
SkinVision AI -- 共享工具模块
=============================
数据加载、特征增强、评估函数、分类标签生成、输出格式化。
所有模型脚本 (01-04) 共用此模块。
"""

import json
import os

_ascii_tmp = "C:/sv_joblib_tmp"
os.makedirs(_ascii_tmp, exist_ok=True)
os.environ["JOBLIB_TEMP_FOLDER"] = _ascii_tmp
os.environ["TMPDIR"] = _ascii_tmp
os.environ["TEMP"] = _ascii_tmp
os.environ["TMP"] = _ascii_tmp

import numpy as np
import pandas as pd
from sklearn.metrics import (
    mean_squared_error, mean_absolute_error, r2_score,
    accuracy_score, f1_score, roc_auc_score,
)

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feature_engineering import build_features, fit_categoricals, transform_categoricals

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")

FEATURE_COLS = [
    "log_price", "MA_7", "MA_30", "MA_90",
    "Return_1d", "Return_7d", "Volatility_30",
    "RSI_14", "MACD", "Volume_MA_7",
    "MA_30_dev", "BB_position", "Volume_Change_Ratio",
    "is_stattrak", "is_floor_price",
    "days_to_next_major", "days_since_last_major", "is_major_active",
    "steam_ccu", "days_since_cs2_announce",
    "weapon_type_enc", "rarity_enc", "wear_enc",
]

SHAP_FEATURE_NAMES = [
    "MA_30_dev", "RSI_14", "Volume_Change_Ratio", "Return_7d",
    "MACD", "BB_position", "days_to_next_major", "steam_ccu",
]

CLASS_LABELS = {0: "die", 1: "ping", 2: "zhang"}
CLASS_THRESHOLD = 0.02

# 缓存全量面板，避免 01-04 重复读盘与重复算特征
_PANEL_CACHE = None


def load_panel(splits=("train", "val", "test"), use_cache=True):
    """
    加载多个 split，拼接后一次算滚动特征，用 train 拟合类别编码。

    返回: dict[str, DataFrame]，key 为 split 名。
    """
    global _PANEL_CACHE
    cache_key = tuple(splits)
    if use_cache and _PANEL_CACHE is not None and _PANEL_CACHE.get("_key") == cache_key:
        return {s: _PANEL_CACHE[s].copy() for s in splits}

    frames = []
    for split in splits:
        path = os.path.join(DATA_DIR, f"{split}.csv")
        df = pd.read_csv(path, parse_dates=["date"])
        df["_split"] = split
        frames.append(df)

    panel = pd.concat(frames, ignore_index=True)
    # 全量面板一次算滚动特征（保留 train→val→test 历史连续性）
    # 先不 drop Target，便于在编码后再统一 drop
    panel = build_features(panel, drop_na_target=False)

    # 屏蔽 train/val 行上落在 test 的 Target，避免终训时标签泄漏
    if "_target_split" in panel.columns:
        leak = panel["_split"].isin(["train", "val"]) & (panel["_target_split"] == "test")
        panel.loc[leak, "Target"] = np.nan

    train_mask = panel["_split"] == "train"
    encoders = fit_categoricals(panel.loc[train_mask])
    panel = transform_categoricals(panel, encoders)
    panel = panel.dropna(subset=["Target"]).reset_index(drop=True)

    drop_cols = [c for c in ("_split", "_target_split") if c in panel.columns]
    result = {"_key": cache_key, "_encoders": encoders}
    for split in splits:
        result[split] = panel[panel["_split"] == split].drop(columns=drop_cols).copy()

    if use_cache:
        _PANEL_CACHE = result

    return {s: result[s].copy() for s in splits}


def load_and_prepare(split="train"):
    """兼容旧接口：从全量面板中取指定 split（滚动特征含历史）。"""
    panel = load_panel(("train", "val", "test"))
    if split not in panel:
        raise ValueError(f"未知 split: {split}")
    return panel[split]


def load_train_val_test():
    """返回 (train_df, val_df, test_df)。"""
    panel = load_panel(("train", "val", "test"))
    return panel["train"], panel["val"], panel["test"]


def _sort_by_date(df):
    """按全局日期排序，保证 TimeSeriesSplit 沿时间前进。"""
    return df.sort_values(["date", "market_hash_name"]).reset_index(drop=True)


def make_regression_target(df):
    """回归: y = Target (7天后 log_price), X = 特征矩阵（按 date 排序）"""
    valid = _sort_by_date(df.dropna(subset=["Target"]))
    X = valid[FEATURE_COLS].values
    y = valid["Target"].values
    dates = valid["date"].values
    skins = valid["market_hash_name"].values
    prices = valid["price"].values
    return X, y, dates, skins, prices


def make_classification_target(df, threshold=CLASS_THRESHOLD):
    """分类: 涨/平/跌 三分类 (基于7天前瞻收益率)，按 date 排序"""
    valid = _sort_by_date(df.dropna(subset=["Target"]))
    future_return = np.exp(valid["Target"].values - valid["log_price"].values) - 1
    y = np.where(future_return >= threshold, 2,
         np.where(future_return <= -threshold, 0, 1))
    X = valid[FEATURE_COLS].values
    dates = valid["date"].values
    skins = valid["market_hash_name"].values
    prices = valid["price"].values
    return X, y, dates, skins, prices


def eval_regression(y_true_log, y_pred_log, prices_true=None):
    """回归评估: RMSE/MAE/MAPE/R2 (在真实价格空间计算)"""
    y_true = np.expm1(y_true_log)
    y_pred = np.expm1(y_pred_log)
    y_pred = np.maximum(y_pred, 0.01)
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mae = float(mean_absolute_error(y_true, y_pred))
    mape = float(np.mean(np.abs((y_true - y_pred) / np.maximum(y_true, 0.01))) * 100)
    r2 = float(r2_score(y_true, y_pred))
    return {"rmse": rmse, "mae": mae, "mape": mape, "r2": r2}


def eval_classification(y_true, y_pred, y_proba=None):
    """分类评估: Accuracy/F1/AUC"""
    acc = float(accuracy_score(y_true, y_pred))
    f1 = float(f1_score(y_true, y_pred, average="weighted"))
    auc = None
    if y_proba is not None:
        try:
            auc = float(roc_auc_score(y_true, y_proba, multi_class="ovr", average="weighted"))
        except Exception:
            auc = None
    return {"accuracy": acc, "f1": f1, "auc": auc}


def save_json(data, filename):
    """保存 JSON 到 outputs/ 目录"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    path = os.path.join(OUTPUT_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  -> 已保存: {path}")
    return path


def select_representative_skins(df, n=5):
    """选择 n 个代表性饰品 (按价格分位数 + 数据量)"""
    stats = df.groupby("market_hash_name").agg(
        mean_price=("price", "mean"),
        count=("price", "count"),
    ).reset_index()
    stats = stats[stats["count"] >= 800].sort_values("mean_price")
    if len(stats) <= n:
        return stats["market_hash_name"].tolist()
    indices = np.linspace(0, len(stats) - 1, n).astype(int)
    return stats.iloc[indices]["market_hash_name"].tolist()


def print_tscv_fold_dates(dates, tscv):
    """打印 TimeSeriesSplit 各 fold 的日期范围，便于自检时序正确性。"""
    dates = pd.to_datetime(dates)
    for i, (train_idx, val_idx) in enumerate(tscv.split(np.arange(len(dates)))):
        tr_min, tr_max = dates[train_idx].min(), dates[train_idx].max()
        va_min, va_max = dates[val_idx].min(), dates[val_idx].max()
        print(f"    fold {i+1}: train [{tr_min.date()} ~ {tr_max.date()}]  "
              f"val [{va_min.date()} ~ {va_max.date()}]", flush=True)
