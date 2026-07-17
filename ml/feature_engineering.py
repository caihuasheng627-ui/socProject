"""
SkinVision AI — 特征工程
=========================
共享入口：组员1 (LSTM/GRU) 和 组员2 (树模型) 都调用此函数。

用法:
    from feature_engineering import build_features, fit_categoricals, transform_categoricals
    panel = build_features(pd.concat([train, val, test]))  # 全量面板一次算滚动特征
    encoders = fit_categoricals(panel[panel["_split"] == "train"])
    panel = transform_categoricals(panel, encoders)

原理:
    所有滚动特征在 groupby('market_hash_name') 内计算，严禁跨物品泄漏。
    类别编码仅在 train 上 fit，再 transform 全量，避免编码不一致。
"""

import numpy as np
import pandas as pd
from sklearn.preprocessing import LabelEncoder

CATEGORICAL_COLS = [
    ("weapon_type", "weapon_type_enc"),
    ("rarity", "rarity_enc"),
    ("wear", "wear_enc"),
]


def build_features(df: pd.DataFrame, drop_na_target: bool = True) -> pd.DataFrame:
    """
    输入: 原始 DataFrame（可为 train/val/test 拼接后的全量面板）
    输出: 添加了技术指标 + 收益率 + Target 的 DataFrame

    不做 LabelEncoder（由 fit/transform_categoricals 负责）。
    """
    df = df.sort_values(["market_hash_name", "date"]).copy()

    # 1. 对数价格 (处理 $0.03~$2000 量级差异)
    df["log_price"] = np.log1p(df["price"])

    # 2. 按物品逐组计算滚动特征
    for _name, group in df.groupby("market_hash_name", sort=False):
        idx = group.index
        price = group["price"]
        volume = group["daily_volume"]

        # === 移动平均线 ===
        df.loc[idx, "MA_7"] = price.rolling(7, min_periods=1).mean()
        df.loc[idx, "MA_30"] = price.rolling(30, min_periods=1).mean()
        df.loc[idx, "MA_90"] = price.rolling(90, min_periods=1).mean()

        # === 收益率 ===
        df.loc[idx, "Return_1d"] = price.pct_change(1)
        df.loc[idx, "Return_7d"] = price.pct_change(7)

        # === 波动率 (30日收益率标准差) ===
        df.loc[idx, "Volatility_30"] = (
            price.pct_change().rolling(30, min_periods=1).std()
        )

        # === RSI_14 ===
        delta = price.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14, min_periods=1).mean()
        avg_loss = loss.rolling(14, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df.loc[idx, "RSI_14"] = 100 - (100 / (1 + rs))

        # === MACD ===
        ema_12 = price.ewm(span=12, adjust=False).mean()
        ema_26 = price.ewm(span=26, adjust=False).mean()
        df.loc[idx, "MACD"] = ema_12 - ema_26

        # === 成交量均线 ===
        df.loc[idx, "Volume_MA_7"] = volume.rolling(7, min_periods=1).mean()

        # === Target (7天后 log 价格) ===
        df.loc[idx, "Target"] = group["log_price"].shift(-7)
        # 记录目标行所属 split，供下游屏蔽 train/val → test 的标签泄漏
        if "_split" in group.columns:
            df.loc[idx, "_target_split"] = group["_split"].shift(-7)

        # === 组员2 额外特征 ===
        ma30 = df.loc[idx, "MA_30"]
        df.loc[idx, "MA_30_dev"] = (price - ma30) / ma30.replace(0, 1e-10)
        ma20 = price.rolling(20, min_periods=1).mean()
        std20 = price.rolling(20, min_periods=1).std().replace(0, 1e-10)
        df.loc[idx, "BB_position"] = (price - ma20) / (2 * std20)
        df.loc[idx, "Volume_Change_Ratio"] = volume.pct_change(5)

    # 3. 填充 NaN
    df["Return_1d"] = df["Return_1d"].fillna(0)
    df["Return_7d"] = df["Return_7d"].fillna(0)
    df["RSI_14"] = df["RSI_14"].fillna(50)
    df["Volatility_30"] = df["Volatility_30"].fillna(0)
    df["MA_30_dev"] = df["MA_30_dev"].fillna(0)
    df["BB_position"] = df["BB_position"].fillna(0)
    df["Volume_Change_Ratio"] = df["Volume_Change_Ratio"].fillna(0)

    # 4. 大量级特征压缩 (LSTM 梯度友好) — 只缩放一次
    if "steam_ccu" in df.columns:
        # 原始数据为百万级；若已缩放（均值 < 100）则跳过
        if df["steam_ccu"].abs().median() > 100:
            df["steam_ccu"] = df["steam_ccu"] / 1e6
    df["daily_volume_log"] = np.log1p(df["daily_volume"])
    df["volume_ma_log"] = np.log1p(df["Volume_MA_7"])

    if drop_na_target:
        df = df.dropna(subset=["Target"])

    return df


def fit_categoricals(train_df: pd.DataFrame) -> dict:
    """仅在 train 上 fit LabelEncoder，返回 {enc_name: LabelEncoder}。"""
    encoders = {}
    for col, enc_name in CATEGORICAL_COLS:
        le = LabelEncoder()
        le.fit(train_df[col].astype(str))
        encoders[enc_name] = (col, le)
    return encoders


def transform_categoricals(df: pd.DataFrame, encoders: dict) -> pd.DataFrame:
    """用 train 上 fit 的编码器 transform；未见过的类别映射为 -1。"""
    df = df.copy()
    for enc_name, (col, le) in encoders.items():
        values = df[col].astype(str)
        known = set(le.classes_)
        # 未见类别先替换为已知类中第一个，再手动标为 -1
        mapped = values.map(lambda v: v if v in known else le.classes_[0])
        codes = le.transform(mapped)
        codes = np.where(values.isin(known), codes, -1)
        df[enc_name] = codes
    return df
