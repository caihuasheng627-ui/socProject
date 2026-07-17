"""
GRU: RNN 变体对比模型 (10 件高流动性代表物品)
==============================================
参考: Lecture4 例 8 (IBM LSTM) — 把 LSTM 层换成 GRU

策略 (team_tasks.md 第 4 步):
  - 按 train 集 daily_volume 均值选 10 件高流动性代表物品
  - 单个 Sequential GRU 在这 10 件的滑动窗口上训练
  - 与 LSTM-D 单组网络唯一区别是循环单元 (LSTM → GRU), 构成干净的变体对比

核心规则 (与 LSTM-C/D 一致):
  - 严禁跨物品拼序列, 必须 groupby 后逐物品构建滑动窗口
  - 训练在 log 空间, 预测后 expm1 还原真实价格

输出文件:
  - gru.keras          模型权重
  - gru_scaler.pkl     {x_scaler, y_scaler}
  - gru_items.pkl      10 件物品名单 (组员 3 推理时判断是否支持)
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

warnings.filterwarnings("ignore")

# ============================================================
# 超参数 (与 LSTM-C/D 对齐, 保证对比公平)
# ============================================================
LOOKBACK = 60           # 60 天窗口
HORIZON = 7             # 预测 7 天后
N_ITEMS = 10            # 高流动性代表物品数
GRU_UNITS = 50          # GRU 隐藏单元数
DROPOUT = 0.2           # Dropout 比例
BATCH_SIZE = 32
EPOCHS = 100
LEARNING_RATE = 0.001

# 输出的模型 / 映射文件
BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "models"
OUTPUT_DIR.mkdir(exist_ok=True)

MODEL_PATH  = OUTPUT_DIR / "gru.keras"
SCALER_PATH = OUTPUT_DIR / "gru_scaler.pkl"
ITEMS_PATH  = OUTPUT_DIR / "gru_items.pkl"


# ============================================================
# 第 1 步: 加载数据 + 选 10 件高流动性物品 + 特征工程
# ============================================================
def load_data():
    """加载 train / val, 选高流动性 top10, 做特征工程"""
    from feature_engineering import build_features

    print("=" * 60)
    print("第 1 步: 加载数据 + 选高流动性物品 + 特征工程")
    print("=" * 60)

    train = pd.read_csv(BASE / "data" / "train.csv")
    val   = pd.read_csv(BASE / "data" / "val.csv")

    print(f"  原始 train: {len(train):,} 行, {train['market_hash_name'].nunique()} 件")
    print(f"  原始 val:   {len(val):,} 行, {val['market_hash_name'].nunique()} 件")

    # --- 按 train 集日均成交量选 top10 (只在 val 也有的物品里选, 保证可评估) ---
    vol = train.groupby("market_hash_name")["daily_volume"].mean()
    vol = vol[vol.index.isin(val["market_hash_name"].unique())]
    items = vol.sort_values(ascending=False).head(N_ITEMS).index.tolist()

    print(f"\n  高流动性 top{N_ITEMS} (train 日均成交量):")
    for name in items:
        print(f"    {vol[name]:>10,.0f}  {name}")

    train = train[train["market_hash_name"].isin(items)]
    val   = val[val["market_hash_name"].isin(items)]

    train = build_features(train)
    val   = build_features(val)

    print(f"\n  特征化后 train: {len(train):,} 行")
    print(f"  特征化后 val:   {len(val):,} 行")

    return train, val, items


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

def build_sequences(df, x_scaler=None, fit_scaler=False):
    """逐物品构建滑动窗口 X(60,15) → y(1), 单输入版"""
    X, y = [], []

    for _, group in df.groupby("market_hash_name"):
        group = group.sort_values("date")
        feat = group[FEATURE_COLS].values.astype(np.float32)
        target = group["Target"].values.astype(np.float32)

        for i in range(LOOKBACK, len(group)):
            if np.isnan(target[i]):
                continue
            X.append(feat[i - LOOKBACK : i])         # (60, 15)
            y.append(target[i])                       # Target = log_price of day i+7

    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)

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
            Dense(1),
        ],
        name="GRU_Top10_Liquidity",
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

    # --- 加载 ---
    train_df, val_df, items = load_data()

    # --- 构建序列 (train fit scaler, val 复用) ---
    print("\n" + "=" * 60)
    print("第 2 步: 构建滑动窗口")
    print("=" * 60)
    X_train, y_train, x_scaler = build_sequences(train_df, fit_scaler=True)
    X_val, y_val = build_sequences(val_df, x_scaler=x_scaler)

    # --- 对 y 做标准化 ---
    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train.reshape(-1, 1)).ravel()
    y_val_scaled   = y_scaler.transform(y_val.reshape(-1, 1)).ravel()
    print(f"  y scaler mean={y_scaler.mean_[0]:.4f}, scale={y_scaler.scale_[0]:.4f}")

    # --- 构建模型 ---
    print("\n" + "=" * 60)
    print("第 3 步: 构建 GRU 模型")
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
        X_train, y_train_scaled,
        validation_data=(X_val, y_val_scaled),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=[early_stop, reduce_lr],
        verbose=1,
    )

    # --- 评估 (输出格式与 LSTM-C 第 6 步一致) ---
    print("\n" + "=" * 60)
    print("第 5 步: 评估 (仅 10 件高流动性物品)")
    print("=" * 60)
    y_pred_scaled = model.predict(X_val, verbose=0).ravel()
    y_pred = y_scaler.inverse_transform(y_pred_scaled.reshape(-1, 1)).ravel()

    rmse_log = np.sqrt(mean_squared_error(y_val, y_pred))
    mae_log  = mean_absolute_error(y_val, y_pred)
    print(f"  Val RMSE (log space): {rmse_log:.6f}")
    print(f"  Val MAE  (log space): {mae_log:.6f}")

    y_true_price = np.expm1(y_val)
    y_pred_price = np.expm1(y_pred)
    rmse_price = np.sqrt(mean_squared_error(y_true_price, y_pred_price))
    mae_price  = mean_absolute_error(y_true_price, y_pred_price)
    mape       = np.mean(np.abs((y_true_price - y_pred_price) / y_true_price)) * 100
    r2         = r2_score(y_true_price, y_pred_price)
    print(f"  Val RMSE (real USD):  ${rmse_price:.4f}")
    print(f"  Val MAE  (real USD):  ${mae_price:.4f}")
    print(f"  Val MAPE (real USD):   {mape:.2f}%")
    print(f"  Val R²   (real USD):   {r2:.4f}")
    print(f"\n  ⚠️ 注意: GRU 只覆盖 10 件高流动性物品, 与 LSTM-C/D 的全量指标不同口径")

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
