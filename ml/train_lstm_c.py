"""
LSTM-C: 面板 Embedding 模型 (Functional API) — Seq2Seq 7 天版
==============================================================
参考: Lecture4 例 8 (IBM LSTM) + 自研双输入架构

架构图:
  价格分支: Input(60, 15) ─────────────────┐
  物品分支: Input(1) → Embedding(154, 8) ──┼→ Concatenate → LSTM(50)×2 → Dense(7)

核心规则:
  - 严禁跨物品拼序列, 必须 groupby 后逐物品构建滑动窗口
  - 训练在 log 空间, 预测后 expm1 还原真实价格
  - 输出 7 天每日价格 [day1 .. day7], 前端直接用, 不再需要插值画线

输出文件:
  - lstm_c.keras           模型权重 (Dense(7) 输出)
  - lstm_c_scaler.pkl      价格 Scaler (x_scaler + y_scaler, 适配 7 维)
  - lstm_c_item_map.pkl    物品名 → ID 映射
"""
import numpy as np
import pandas as pd
import pickle
import warnings
from pathlib import Path
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score
from tensorflow import keras
from tensorflow.keras import layers
from tensorflow.keras.layers import (
    Input, LSTM, Dense, Dropout, Embedding,
    Concatenate, RepeatVector, Flatten
)
from forecast_contract import (
    HORIZON_STEPS,
    add_grouped_targets_multi,
    build_sequence_windows_multi,
    build_training_item_map,
    decode_log_price_predictions_multi,
    encode_item_ids,
    load_feature_panel,
)
from gpu_config import configure_device, create_multi_input_dataset

warnings.filterwarnings("ignore")

# ============================================================
# 超参数 (方便微调)
# ============================================================
LOOKBACK = 60           # 60 天窗口
SEQ_HORIZON = 7         # 输出未来 7 天每日价格
EMBEDDING_DIM = 8       # 物品 Embedding 维度
LSTM_UNITS = 50         # LSTM 隐藏单元数
DROPOUT = 0.2           # Dropout 比例
BATCH_SIZE = 32
EPOCHS = 100            # 给足够时间消化 is_stattrak
LEARNING_RATE = 0.001

# 输出的模型 / 映射文件
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_PATH   = OUTPUT_DIR / "lstm_c.keras"
SCALER_PATH  = OUTPUT_DIR / "lstm_c_scaler.pkl"
ITEM_MAP_PATH = OUTPUT_DIR / "lstm_c_item_map.pkl"


# ============================================================
# 第 1 步: 加载数据 + 特征工程
# ============================================================
def load_data():
    """一次加载连续 train/val/test 面板, 计算特征 + 多步 target。"""
    print("=" * 60)
    print("第 1 步: 加载数据 + 特征工程 + 多步 target")
    print("=" * 60)

    panel = load_feature_panel(DATA_DIR)
    # 追加 Target_1..Target_7 多步目标列
    panel = add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)
    for split in ("train", "val", "test"):
        part = panel[panel["_split"] == split]
        print(f"  {split}: {len(part):,} 行, {part['market_hash_name'].nunique()} 件")
    return panel


# ============================================================
# 第 2 步: 构建物品 ID 映射
# ============================================================
def build_item_map(train_df):
    """仅用训练物品建 ID，并保留一个未知物品 ID。"""
    item_map = build_training_item_map(train_df)
    print(f"\n  物品 ID 映射: {len(item_map)} 件 (0 ~ {len(item_map)-1})")
    return item_map


# ============================================================
# 第 3 步: 滑动窗口构建 (参照 Lecture4 例 8 的 create_dataset)
#          ⚠️ 关键: 逐物品 groupby, 严禁跨物品拼序列
# ============================================================
# 选哪些列做 LSTM 输入 (17 个)
FEATURE_COLS = [
    "log_price",              # 对数价格 ★ 核心 (0.03~7.3)
    "MA_7",                   # 7 日均线
    "MA_30",                  # 30 日均线
    "MA_90",                  # 90 日均线
    "Return_1d",              # 1 日收益率 (-0.05~0.05)
    "Return_7d",              # 7 日收益率
    "Volatility_30",          # 30 日波动率 (0~0.5)
    "RSI_14",                 # RSI (0~100)
    "MACD",                   # MACD (~-10~100)
    "volume_ma_log",          # 对数成交量 (0~13.7)
    "daily_volume_log",       # 对数当日量 (0~13.7)
    "is_floor_price",         # 地板价标记 (0/1)
    "is_stattrak",            # StatTrak 标记 (0/1)
    "is_major_active",        # Major 赛期 (0/1)
    "steam_ccu",              # Steam 在线 (13~30, 已 /1e6)
]

def build_sequences(df, item_map, sample_split, x_scaler=None, fit_scaler=False):
    """
    逐物品构建滑动窗口 X(60,15) → y(7)
    返回多步 target y shape: (n_samples, 7)
    """
    X_price, y, meta = build_sequence_windows_multi(
        df, FEATURE_COLS, LOOKBACK, SEQ_HORIZON, sample_split=sample_split
    )
    X_item = encode_item_ids(meta["market_hash_name"], item_map).reshape(-1, 1)

    # --- 全局 StandardScaler (解决 15 个特征量级差异) ---
    nsamples, nsteps, nfeats = X_price.shape
    if fit_scaler or x_scaler is None:
        x_scaler = StandardScaler()
        X_flat = X_price.reshape(-1, nfeats)
        X_flat = x_scaler.fit_transform(X_flat)
        X_price = X_flat.reshape(nsamples, nsteps, nfeats)
    else:
        X_flat = X_price.reshape(-1, nfeats)
        X_flat = x_scaler.transform(X_flat)
        X_price = X_flat.reshape(nsamples, nsteps, nfeats)

    print(f"\n  滑动窗口: X_price={X_price.shape}, X_item={X_item.shape}, y={y.shape}")
    if fit_scaler:
        return X_price, X_item, y, x_scaler
    return X_price, X_item, y


# ============================================================
# 第 4 步: 构建 LSTM-C 模型 (Functional API)
#         参照 Lecture4 例 8: LSTM(50) × 2 + Dropout(0.2)
#         加上物品 Embedding 分支
# ============================================================
def build_model(n_items):
    """构建双输入 LSTM 模型, Dense(7) 输出 7 天每日 log_price"""

    # ---- 分支 A: 价格时间序列 ----
    input_price = Input(shape=(LOOKBACK, len(FEATURE_COLS)), name="price_input")

    # ---- 分支 B: 物品 Embedding ----
    input_item = Input(shape=(1,), name="item_input")
    embed = Embedding(input_dim=n_items, output_dim=EMBEDDING_DIM, name="item_embedding")(input_item)
    embed = Flatten()(embed)
    embed = RepeatVector(LOOKBACK)(embed)

    # ---- 合并两路 ----
    concat = Concatenate(name="concat")([input_price, embed])
    # concat shape: (None, 60, 15+8=23)

    # ---- LSTM 层 ----
    x = LSTM(LSTM_UNITS, return_sequences=True, name="lstm_1")(concat)
    x = Dropout(DROPOUT, name="dropout_1")(x)

    x = LSTM(LSTM_UNITS, return_sequences=False, name="lstm_2")(x)
    x = Dropout(DROPOUT, name="dropout_2")(x)

    # ---- 输出层: 7 天每日预测 ----
    output = Dense(SEQ_HORIZON, name="output")(x)

    # ---- 组装模型 ----
    model = keras.Model(
        inputs=[input_price, input_item],
        outputs=output,
        name="LSTM_C_Panel_Embedding_Seq7"
    )

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=LEARNING_RATE),
        loss="mse",
        metrics=["mae"]
    )

    return model



# ============================================================
# 第 6 步: 主流程
# ============================================================
def main():
    # 固定随机种子
    keras.utils.set_random_seed(42)
    configure_device()

    # --- 加载 ---
    panel = load_data()
    train_df = panel[panel["_split"] == "train"]
    item_map = build_item_map(train_df)
    n_items = len(item_map)

    # --- 构建序列 (train fit scaler, val 复用) ---
    print("\n" + "=" * 60)
    print("第 2 步: 构建滑动窗口")
    print("=" * 60)
    Xp_train, Xi_train, y_train, x_scaler = build_sequences(
        panel, item_map, "train", fit_scaler=True
    )
    Xp_val, Xi_val, y_val = build_sequences(
        panel, item_map, "val", x_scaler=x_scaler, fit_scaler=False
    )

    # --- 对 y 做标准化 (7 维, 每天独立 mean/scale) ---
    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train)
    y_val_scaled = y_scaler.transform(y_val)
    print(f"  y scaler mean={y_scaler.mean_}, scale={y_scaler.scale_}")

    # --- 构建 tf.data 流水线 ---
    train_ds = create_multi_input_dataset(
        Xp_train, Xi_train, y_train_scaled, batch_size=BATCH_SIZE, shuffle=True
    )
    val_ds = create_multi_input_dataset(
        Xp_val, Xi_val, y_val_scaled, batch_size=BATCH_SIZE, shuffle=False
    )

    # --- 构建模型 ---
    print("\n" + "=" * 60)
    print("第 4 步: 构建 LSTM-C Seq2Seq 模型 (Dense(7))")
    print("=" * 60)
    model = build_model(n_items)
    model.summary()

    # --- 训练 ---
    print("\n" + "=" * 60)
    print("第 5 步: 训练")
    print("=" * 60)
    early_stop = keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=15, restore_best_weights=True
    )
    reduce_lr = keras.callbacks.ReduceLROnPlateau(
        monitor="val_loss", factor=0.5, patience=7, min_lr=1e-6
    )

    history = model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=EPOCHS,
        callbacks=[early_stop, reduce_lr],
        verbose=1
    )

    # --- 评估: 逐日 + 整体 ---
    print("\n" + "=" * 60)
    print("第 6 步: 评估 (逐日 + 整体)")
    print("=" * 60)
    y_pred_scaled = model.predict([Xp_val, Xi_val], verbose=0)  # (n, 7)
    y_pred_log = y_scaler.inverse_transform(y_pred_scaled)       # (n, 7) log_price
    y_pred = np.expm1(y_pred_log)                                # (n, 7) USD

    # 真实价格 (USD) per day
    y_true_price_all = np.expm1(y_val)  # (n, 7), y_val 是 log_price

    print(f"\n  {'Day':<8} {'RMSE':>10} {'MAE':>10} {'MAPE':>8} {'R²':>8}")
    print(f"  {'-'*8} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")
    for d in range(SEQ_HORIZON):
        yt = y_true_price_all[:, d]
        yp = y_pred[:, d]
        rmse = np.sqrt(mean_squared_error(yt, yp))
        mae = mean_absolute_error(yt, yp)
        mape = np.mean(np.abs((yt - yp) / np.maximum(yt, 0.01))) * 100
        r2 = r2_score(yt, yp)
        print(f"  Day{d+1:<5} ${rmse:>8.4f} ${mae:>8.4f} {mape:>7.2f}% {r2:>8.4f}")

    # 整体 (全部 7 天展平)
    yt_all = y_true_price_all.ravel()
    yp_all = y_pred.ravel()
    rmse_all = np.sqrt(mean_squared_error(yt_all, yp_all))
    mae_all = mean_absolute_error(yt_all, yp_all)
    r2_all = r2_score(yt_all, yp_all)
    print(f"  {'ALL':<8} ${rmse_all:>8.4f} ${mae_all:>8.4f} {'-':>8} {r2_all:>8.4f}")

    # Day 7 单点 (对标旧版)
    yt_d7 = y_true_price_all[:, -1]
    yp_d7 = y_pred[:, -1]
    rmse_d7 = np.sqrt(mean_squared_error(yt_d7, yp_d7))
    mae_d7 = mean_absolute_error(yt_d7, yp_d7)
    r2_d7 = r2_score(yt_d7, yp_d7)
    print(f"\n  Day 7 only (对标旧版单点): RMSE=${rmse_d7:.4f}  MAE=${mae_d7:.4f}  R²={r2_d7:.4f}")

    # --- 保存 ---
    print("\n" + "=" * 60)
    print("第 7 步: 保存模型文件")
    print("=" * 60)
    model.save(MODEL_PATH)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump({"y_scaler": y_scaler, "x_scaler": x_scaler}, f)
    with open(ITEM_MAP_PATH, "wb") as f:
        pickle.dump(item_map, f)

    print(f"  ✅ {MODEL_PATH}")
    print(f"  ✅ {SCALER_PATH}")
    print(f"  ✅ {ITEM_MAP_PATH}")
    print(f"\n  模型参数: {model.count_params():,}")
    print(f"  训练轮数: {len(history.history['loss'])}")
    print("\n" + "=" * 60)
    print("LSTM-C Seq2Seq (Dense(7)) 训练完成!")
    print("=" * 60)

    return model, history, y_true_price_all, y_pred


if __name__ == "__main__":
    model, history, y_true, y_pred = main()
