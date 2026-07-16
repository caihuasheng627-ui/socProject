"""
SkinVision AI — 特征工程
=========================
共享入口：组员1 (LSTM/GRU) 和 组员2 (树模型) 都调用此函数。

用法:
    from feature_engineering import build_features
    train_feat = build_features(pd.read_csv("data/train.csv"))

原理:
    所有滚动特征在 groupby('market_hash_name') 内计算，严禁跨物品泄漏。
"""

import numpy as np
import pandas as pd


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    输入: 原始 DataFrame (train.csv / val.csv / test.csv 的 14 列)
    输出: 添加了技术指标 + 收益率 + Target 列的 DataFrame
    """
    df = df.sort_values(["market_hash_name", "date"]).copy()

    # 1. 对数价格 (处理 $0.03~$2000 量级差异)
    df["log_price"] = np.log1p(df["price"])

    # 2. 按物品逐组计算滚动特征
    for name, group in df.groupby("market_hash_name"):
        idx = group.index
        # === 移动平均线 ===
        df.loc[idx, "MA_7"]  = group["price"].rolling(7,  min_periods=1).mean()
        df.loc[idx, "MA_30"] = group["price"].rolling(30, min_periods=1).mean()
        df.loc[idx, "MA_90"] = group["price"].rolling(90, min_periods=1).mean()

        # === 收益率 ===
        df.loc[idx, "Return_1d"] = group["price"].pct_change(1)
        df.loc[idx, "Return_7d"] = group["price"].pct_change(7)

        # === 波动率 (30日收益率标准差) ===
        df.loc[idx, "Volatility_30"] = (
            group["price"].pct_change().rolling(30, min_periods=1).std()
        )

        # === RSI_14 ===
        delta = group["price"].diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.rolling(14, min_periods=1).mean()
        avg_loss = loss.rolling(14, min_periods=1).mean()
        rs = avg_gain / avg_loss.replace(0, 1e-10)
        df.loc[idx, "RSI_14"] = 100 - (100 / (1 + rs))

        # === MACD ===
        ema_12 = group["price"].ewm(span=12, adjust=False).mean()
        ema_26 = group["price"].ewm(span=26, adjust=False).mean()
        df.loc[idx, "MACD"] = ema_12 - ema_26

        # === 成交量均线 ===
        df.loc[idx, "Volume_MA_7"] = group["daily_volume"].rolling(7, min_periods=1).mean()

        # === Target (7天后 log 价格) ===
        df.loc[idx, "Target"] = group["log_price"].shift(-7)

    # 3. 填充 NaN + 去掉没有 Target 的行
    df["Return_1d"] = df["Return_1d"].fillna(0)       # 第一天没有前一天
    df["Return_7d"] = df["Return_7d"].fillna(0)       # 前7天没有7天前
    df["RSI_14"] = df["RSI_14"].fillna(50)            # 50 = 中性
    df["Volatility_30"] = df["Volatility_30"].fillna(0)
    df = df.dropna(subset=["Target"])                  # 最后7天无目标

    return df