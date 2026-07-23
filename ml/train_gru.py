"""
GRU: RNN 变体对比模型 (10 件高流动性代表物品) — Seq2Seq 7 天版
===============================================================
参考: Lecture4 例 8 (IBM LSTM) — 把 LSTM 层换成 GRU

策略 (team_tasks.md 第 4 步):
  - 按 train 集 daily_volume 均值选 10 件高流动性代表物品
  - 单个 Sequential GRU 在这 10 件的滑动窗口上训练
  - 输出 Dense(7), 直接预测 7 天每日价格

核心规则 (与 LSTM-C/D 一致):
  - 严禁跨物品拼序列, 必须 groupby 后逐物品构建滑动窗口
  - 训练在 log 空间, 预测后 expm1 还原真实价格

输出文件:
  - gru.keras          模型权重 (Dense(7) 输出)
  - gru_scaler.pkl     {x_scaler, y_scaler}
  - gru_items.pkl      10 件物品名单
"""
import numpy as np
import pandas as pd
import pickle
import sys
import warnings
from pathlib import Path

# Windows GBK 控制台打印 ✅/⚠️ 会 UnicodeEncodeError, 统一转 UTF-8
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from tensorflow import keras
from tensorflow.keras.layers import Input, GRU, Dense, Dropout
from forecast_contract import (
    HORIZON_STEPS,
    add_grouped_targets_multi,
    build_sequence_windows_multi,
    load_feature_panel,
)
from gpu_config import configure_device, create_dataset

warnings.filterwarnings("ignore")

# ============================================================
# 超参数 (与 LSTM-C/D 对齐, 保证对比公平)
# ============================================================
LOOKBACK = 60           # 60 天窗口
SEQ_HORIZON = 7         # 输出未来 7 天每日价格
N_ITEMS = 10            # 高流动性代表物品数
GRU_UNITS = 50          # GRU 隐藏单元数
DROPOUT = 0.2           # Dropout 比例
BATCH_SIZE = 32
EPOCHS = 100
LEARNING_RATE = 0.001

# 输出的模型 / 映射文件
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_PATH  = OUTPUT_DIR / "gru.keras"
SCALER_PATH = OUTPUT_DIR / "gru_scaler.pkl"
ITEMS_PATH  = OUTPUT_DIR / "gru_items.pkl"


# ============================================================
# 第 1 步: 加载数据 + 选 10 件高流动性物品 + 特征工程
# ============================================================
def load_data():
    """从训练集选 top10，并返回连续特征面板。"""
    print("=" * 60)
    print("第 1 步: 加载数据 + 选高流动性物品 + 特征工程")
    print("=" * 60)

    train = pd.read_csv(DATA_DIR / "train.csv")
    val   = pd.read_csv(DATA_DIR / "val.csv")

    print(f"  原始 train: {len(train):,} 行, {train['market_hash_name'].nunique()} 件")
    print(f"  原始 val:   {len(val):,} 行, {val['market_hash_name'].nunique()} 件")

    # --- 按 train 集日均成交量选 top10 (只在 val 也有的物品里选, 保证可评估) ---
    vol = train.groupby("market_hash_name")["daily_volume"].mean()
    vol = vol[vol.index.isin(val["market_hash_name"].unique())]
    items = vol.sort_values(ascending=False).head(N_ITEMS).index.tolist()

    print(f"\n  高流动性 top{N_ITEMS} (train 日均成交量):")
    for name in items:
        print(f"    {vol[name]:>10,.0f}  {name}")

    panel = load_feature_panel(DATA_DIR)
    panel = panel[panel["market_hash_name"].isin(items)].copy()
    panel = add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)
    print(f"\n  连续特征面板: {len(panel):,} 行")
    return panel, items


# ============================================================
# 第 2 步: 滑动窗口构建 (与 LSTM-C/D 同款 15 特征)
#          ⚠️ 关键: 逐物品 groupby, 严禁跨物品拼序列
# ============================================================
FEATURE_COLS = [
    "log_price",              # 对数价格 ★ 核心
    "MA_7",                   # 7 日均线
    "MA_30",                  # 30 日均线
    "MA_90",                  # 90 日均线
    "Return_1d",              # 1 日收益率
    "Return_7d",              # 7 日收益率
    "Volatility_30",          # 30 日波动率
    "RSI_14",                 # RSI
    "MACD",                   # MACD
    "volume_ma_log",          # 对数成交量均线
    "daily_volume_log",       # 对数当日量
    "is_floor_price",         # 地板价标记
    "is_stattrak",            # StatTrak 标记
    "is_major_active",        # Major 赛期
    "steam_ccu",              # Steam 在线 (已 /1e6)
]

def build_sequences(df, sample_split, x_scaler=None, fit_scaler=False):
    """逐物品构建滑动窗口 X(60,15) → y(7), 单输入版"""
    X, y, _ = build_sequence_windows_multi(
        df, FEATURE_COLS, LOOKBACK, SEQ_HORIZON, sample_split=sample_split
    )

    # --- 全局 StandardScaler ---
    nsamples, nsteps, nfeats = X.shape
    if fit_scaler or x_scaler is None:
        x_scaler = StandardScaler()
        X = x_scaler.fit_transform(X.reshape(-1, nfeats)).reshape(nsamples, nsteps, nfeats)
    else:
        X = x_scaler.transform(X.reshape(-1, nfeats)).reshape(nsamples, nsteps, nfeats)

    print(f"\n  滑动窗口: X={X.shape}, y={y.shape}")
    if fit_scaler:
        return X, y, x_scaler
    return X, y


# ============================================================
# 第 3 步: 构建 GRU 模型 (Lecture4 例 8 架构, LSTM → GRU)
# ============================================================
def build_model():
    model = keras.Sequential(
        [
            Input(shape=(LOOKBACK, len(FEATURE_COLS))),
            GRU(GRU_UNITS, return_sequences=True),
            Dropout(DROPOUT),
            GRU(GRU_UNITS, return_sequences=False),
            Dropout(DROPOUT),
            Dense(SEQ_HORIZON),
        ],
        name="GRU_Top10_Liquidity_Seq7",
    )
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=LEARNING_RATE),
        loss="mse",
        metrics=["mae"],
    )
    return model


# ============================================================
# 第 4 步: 主流程
# ============================================================
def main():
    keras.utils.set_random_seed(42)
    configure_device()

    # --- 加载 ---
    panel, items = load_data()

    # --- 构建序列 (train fit scaler, val 复用) ---
    print("\n" + "=" * 60)
    print("第 2 步: 构建滑动窗口")
    print("=" * 60)
    X_train, y_train, x_scaler = build_sequences(panel, "train", fit_scaler=True)
    X_val, y_val = build_sequences(panel, "val", x_scaler=x_scaler)

    # --- 对 y 做标准化 (7 维, 每天独立) ---
    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train)
    y_val_scaled = y_scaler.transform(y_val)
    print(f"  y scaler mean={y_scaler.mean_}, scale={y_scaler.scale_}")

    # --- 构建模型 ---
    print("\n" + "=" * 60)
    print("第 3 步: 构建 GRU Seq2Seq 模型 (Dense(7))")
    print("=" * 60)
    model = build_model()
    model.summary()

    # --- 训练 ---
    print("\n" + "=" * 60)
    print("第 4 步: 训练")
    print("=" * 60)
    early_stop = keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=15, restore_best_weights=True
    )
    reduce_lr = keras.callbacks.ReduceLROnPlateau(
        monitor="val_loss", factor=0.5, patience=7, min_lr=1e-6
    )

    history = model.fit(
        create_dataset(X_train, y_train_scaled, batch_size=BATCH_SIZE, shuffle=True),
        validation_data=create_dataset(X_val, y_val_scaled, batch_size=BATCH_SIZE, shuffle=False),
        epochs=EPOCHS,
        callbacks=[early_stop, reduce_lr],
        verbose=1,
    )

    # --- 评估: 逐日 + 整体 ---
    print("\n" + "=" * 60)
    print("第 5 步: 评估 (仅 10 件高流动性物品)")
    print("=" * 60)
    y_pred_scaled = model.predict(X_val, verbose=0)  # (n, 7)
    y_pred_log = y_scaler.inverse_transform(y_pred_scaled)
    y_pred_price = np.expm1(y_pred_log)
    y_true_price = np.expm1(y_val)

    print(f"\n  {'Day':<8} {'RMSE':>10} {'MAE':>10} {'MAPE':>8} {'R²':>8}")
    for d in range(SEQ_HORIZON):
        yt = y_true_price[:, d]
        yp = y_pred_price[:, d]
        rmse = np.sqrt(mean_squared_error(yt, yp))
        mae = mean_absolute_error(yt, yp)
        mape = np.mean(np.abs((yt - yp) / np.maximum(yt, 0.01))) * 100
        r2 = r2_score(yt, yp)
        print(f"  Day{d+1:<5} ${rmse:>8.4f} ${mae:>8.4f} {mape:>7.2f}% {r2:>8.4f}")

    # Day 7 only
    yt7, yp7 = y_true_price[:, -1], y_pred_price[:, -1]
    rmse_price = np.sqrt(mean_squared_error(yt7, yp7))
    mae_price = mean_absolute_error(yt7, yp7)
    mape = np.mean(np.abs((yt7 - yp7) / np.maximum(yt7, 0.01))) * 100
    r2 = r2_score(yt7, yp7)
    print(f"\n  Day 7 only (对标旧版): RMSE=${rmse_price:.4f}  MAE=${mae_price:.4f}  MAPE={mape:.2f}%  R²={r2:.4f}")
    print(f"  ⚠️ 注意: GRU 只覆盖 10 件高流动性物品, 与 LSTM-C/D 的全量指标不同口径")

    # --- 保存 ---
    print("\n" + "=" * 60)
    print("第 6 步: 保存模型文件")
    print("=" * 60)
    model.save(MODEL_PATH)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump({"y_scaler": y_scaler, "x_scaler": x_scaler}, f)
    with open(ITEMS_PATH, "wb") as f:
        pickle.dump(items, f)

    print(f"  ✅ {MODEL_PATH}")
    print(f"  ✅ {SCALER_PATH}")
    print(f"  ✅ {ITEMS_PATH}")
    print(f"\n  模型参数: {model.count_params():,}")
    print(f"  训练轮数: {len(history.history['loss'])}")
    print("\n" + "=" * 60)
    print("GRU 训练完成!")
    print("=" * 60)

    return model, history, y_true_price, y_pred_price


if __name__ == "__main__":
    model, history, y_true, y_pred = main()
