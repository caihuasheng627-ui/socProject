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
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import (
    mean_squared_error, mean_absolute_error, r2_score,
    accuracy_score, f1_score, roc_auc_score,
)

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from feature_engineering import build_features

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


def load_and_prepare(split="train"):
    """加载 CSV 并返回增强特征 DataFrame"""
    path = os.path.join(DATA_DIR, f"{split}.csv")
    df = pd.read_csv(path, parse_dates=["date"])
    df = build_features(df)
    df = _add_extra_features(df)
    df = _encode_categoricals(df)
    return df


def _add_extra_features(df):
    """添加前端 SHAP 所需的额外特征"""
    df = df.sort_values(["market_hash_name", "date"]).copy()
    for name, group in df.groupby("market_hash_name"):
        idx = group.index
        df.loc[idx, "MA_30_dev"] = (
            (group["price"] - group["MA_30"]) / group["MA_30"].replace(0, 1e-10)
        )
        ma20 = group["price"].rolling(20, min_periods=1).mean()
        std20 = group["price"].rolling(20, min_periods=1).std().replace(0, 1e-10)
        df.loc[idx, "BB_position"] = (group["price"] - ma20) / (2 * std20)
        df.loc[idx, "Volume_Change_Ratio"] = group["daily_volume"].pct_change(5)
    df["MA_30_dev"] = df["MA_30_dev"].fillna(0)
    df["BB_position"] = df["BB_position"].fillna(0)
    df["Volume_Change_Ratio"] = df["Volume_Change_Ratio"].fillna(0)
    return df


def _encode_categoricals(df):
    """Label encode 分类列 (树模型不需要 one-hot)"""
    for col, enc_name in [("weapon_type", "weapon_type_enc"),
                          ("rarity", "rarity_enc"),
                          ("wear", "wear_enc")]:
        le = LabelEncoder()
        df[enc_name] = le.fit_transform(df[col].astype(str))
    return df


def make_regression_target(df):
    """回归: y = Target (7天后 log_price), X = 特征矩阵"""
    valid = df.dropna(subset=["Target"]).copy()
    X = valid[FEATURE_COLS].values
    y = valid["Target"].values
    dates = valid["date"].values
    skins = valid["market_hash_name"].values
    prices = valid["price"].values
    return X, y, dates, skins, prices


def make_classification_target(df, threshold=CLASS_THRESHOLD):
    """分类: 涨/平/跌 三分类 (基于7天前瞻收益率)"""
    valid = df.dropna(subset=["Target"]).copy()
    future_return = np.exp(valid["Target"] - valid["log_price"]) - 1
    y = np.where(future_return >= threshold, 2,
         np.where(future_return <= -threshold, 0, 1))
    X = valid[FEATURE_COLS].values
    dates = valid["date"].values
    skins = valid["market_hash_name"].values
    prices = valid["price"].values
    return X, y, dates, skins, prices


def eval_regression(y_true_log, y_pred_log, prices_true):
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
