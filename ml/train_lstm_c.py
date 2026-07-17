"""
LSTM-C: 面板 Embedding 模型 (Functional API)
==============================================
参考: Lecture4 例 8 (IBM LSTM) + 自研双输入架构

架构图:
  价格分支: Input(60, 17) ─────────────────┐
  物品分支: Input(1) → Embedding(154, 8) ──┼→ Concatenate → LSTM(50)×2 → Dense(1)

核心规则:
  - 严禁跨物品拼序列, 必须 groupby 后逐物品构建滑动窗口
  - 训练在 log 空间, 预测后 expm1 还原真实价格
  - 每件物品独立 StandardScaler

输出文件:
  - lstm_c.keras           模型权重
  - lstm_c_scaler.pkl      价格 Scaler (推理时反标准化)
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

warnings.filterwarnings("ignore")

# ============================================================
# 超参数 (方便微调)
# ============================================================
LOOKBACK = 60           # 60 天窗口
HORIZON = 7             # 预测 7 天后
EMBEDDING_DIM = 8       # 物品 Embedding 维度
LSTM_UNITS = 50         # LSTM 隐藏单元数
DROPOUT = 0.2           # Dropout 比例
BATCH_SIZE = 32
EPOCHS = 100            # 给足够时间消化 is_stattrak
LEARNING_RATE = 0.001

# 输出的模型 / 映射文件
BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "models"
OUTPUT_DIR.mkdir(exist_ok=True)

MODEL_PATH   = OUTPUT_DIR / "lstm_c.keras"
SCALER_PATH  = OUTPUT_DIR / "lstm_c_scaler.pkl"
ITEM_MAP_PATH = OUTPUT_DIR / "lstm_c_item_map.pkl"


# ============================================================
# 第 1 步: 加载数据 + 特征工程
# ============================================================
def load_data():
    """加载 train / val, 做特征工程, 返回带特征的 DataFrame"""
    from feature_engineering import build_features

    print("=" * 60)
    print("第 1 步: 加载数据 + 特征工程")
    print("=" * 60)

    train = pd.read_csv(BASE / "data" / "train.csv")
    val   = pd.read_csv(BASE / "data" / "val.csv")

    print(f"  原始 train: {len(train):,} 行, {train['market_hash_name'].nunique()} 件")
    print(f"  原始 val:   {len(val):,} 行, {val['market_hash_name'].nunique()} 件")

    train = build_features(train)
    val   = build_features(val)

    print(f"  特征化后 train: {len(train):,} 行")
    print(f"  特征化后 val:   {len(val):,} 行")

    return train, val


# ============================================================
# 第 2 步: 构建物品 ID 映射
# ============================================================
def build_item_map(train_df, val_df):
    """为所有出现过的物品分配 0-based ID"""
    all_items = pd.concat([train_df["market_hash_name"], val_df["market_hash_name"]]).unique()
    item_map = {name: i for i, name in enumerate(sorted(all_items))}
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

def build_sequences(df, item_map, x_scaler=None, fit_scaler=False):
    """
    逐物品构建滑动窗口 X(60,17) → y(1)
    参考 Lecture4 例 8 的 for i in range(60, len):

    x_scaler:   已 fit 的 StandardScaler (val 时传入)
    fit_scaler: True → fit 新 scaler 并返回
    """
    X_price, X_item, y = [], [], []
    skipped = 0

    for name, group in df.groupby("market_hash_name"):
        item_id = item_map.get(name)
        if item_id is None:
            skipped += 1
            continue

        # 取特征列 + Target, 确保按日期排序
        group = group.sort_values("date")
        feat = group[FEATURE_COLS].values.astype(np.float32)
        target = group["Target"].values.astype(np.float32)

        for i in range(LOOKBACK, len(group)):
            seq_x = feat[i - LOOKBACK : i]          # (60, 17)
            seq_y = target[i]                        # Target = log_price of day i+7
            if np.isnan(seq_y):
                continue
            X_price.append(seq_x)
            X_item.append(item_id)
            y.append(seq_y)

    X_price = np.array(X_price, dtype=np.float32)
    X_item  = np.array(X_item,  dtype=np.int32).reshape(-1, 1)
    y       = np.array(y,       dtype=np.float32)

    # --- 全局 StandardScaler (解决 17 个特征量级差异) ---
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
    """构建双输入 LSTM 模型"""

    # ---- 分支 A: 价格时间序列 ----
    input_price = Input(shape=(LOOKBACK, len(FEATURE_COLS)), name="price_input")
    # 不用在这里加 Masking, 因为我们已经逐物品构建窗口

    # ---- 分支 B: 物品 Embedding ----
    input_item = Input(shape=(1,), name="item_input")
    embed = Embedding(input_dim=n_items, output_dim=EMBEDDING_DIM, name="item_embedding")(input_item)
    # Flatten: (None, 1, 8) → (None, 8)
    embed = Flatten()(embed)
    # RepeatVector: (None, 8) → (None, 60, 8)
    embed = RepeatVector(LOOKBACK)(embed)

    # ---- 合并两路 ----
    concat = Concatenate(name="concat")([input_price, embed])
    # concat shape: (None, 60, 17+8=25)

    # ---- LSTM 层 (照搬 Lecture4 例 8: LSTM(50)×2 + Dropout(0.2)) ----
    x = LSTM(LSTM_UNITS, return_sequences=True, name="lstm_1")(concat)
    x = Dropout(DROPOUT, name="dropout_1")(x)

    x = LSTM(LSTM_UNITS, return_sequences=False, name="lstm_2")(x)
    x = Dropout(DROPOUT, name="dropout_2")(x)

    # ---- 输出层 ----
    output = Dense(1, name="output")(x)

    # ---- 组装模型 ----
    model = keras.Model(
        inputs=[input_price, input_item],
        outputs=output,
        name="LSTM_C_Panel_Embedding"
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
    # 固定随机种子 (参照 Lecture4 例 8: tf.random.set_seed(7))
    keras.utils.set_random_seed(42)

    # --- 加载 ---
    train_df, val_df = load_data()
    item_map = build_item_map(train_df, val_df)
    n_items = len(item_map)

    # --- 构建序列 (train fit scaler, val 复用) ---
    print("\n" + "=" * 60)
    print("第 2 步: 构建滑动窗口")
    print("=" * 60)
    Xp_train, Xi_train, y_train, x_scaler = build_sequences(
        train_df, item_map, fit_scaler=True
    )
    Xp_val, Xi_val, y_val = build_sequences(
        val_df, item_map, x_scaler=x_scaler, fit_scaler=False
    )

    # --- 对 y 做标准化 (全局, log 空间量级已接近) ---
    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train.reshape(-1, 1)).ravel()
    y_val_scaled   = y_scaler.transform(y_val.reshape(-1, 1)).ravel()
    print(f"  y scaler mean={y_scaler.mean_[0]:.4f}, scale={y_scaler.scale_[0]:.4f}")

    # --- 构建模型 ---
    print("\n" + "=" * 60)
    print("第 4 步: 构建 LSTM-C 模型")
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
        [Xp_train, Xi_train], y_train_scaled,
        validation_data=([Xp_val, Xi_val], y_val_scaled),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=[early_stop, reduce_lr],
        verbose=1
    )

    # --- 评估 ---
    print("\n" + "=" * 60)
    print("第 6 步: 评估")
    print("=" * 60)
    y_pred_scaled = model.predict([Xp_val, Xi_val], verbose=0).ravel()
    y_pred = y_scaler.inverse_transform(y_pred_scaled.reshape(-1, 1)).ravel()
    y_true = y_val  # 原始 log_price

    # RMSE / MAE (在 log 空间)
    rmse = np.sqrt(mean_squared_error(y_true, y_pred))
    mae  = mean_absolute_error(y_true, y_pred)
    print(f"  Val RMSE (log space): {rmse:.6f}")
    print(f"  Val MAE  (log space): {mae:.6f}")

    # 还原到真实价格
    y_true_price = np.expm1(y_true)
    y_pred_price = np.expm1(y_pred)
    rmse_price = np.sqrt(mean_squared_error(y_true_price, y_pred_price))
    mae_price  = mean_absolute_error(y_true_price, y_pred_price)
    r2 = r2_score(y_true_price, y_pred_price)
    print(f"  Val RMSE (real USD):  ${rmse_price:.4f}")
    print(f"  Val MAE  (real USD):  ${mae_price:.4f}")
    print(f"  Val R²   (real USD):   {r2:.4f}")

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
    print("LSTM-C 训练完成!")
    print("=" * 60)

    return model, history, y_true_price, y_pred_price


if __name__ == "__main__":
    model, history, y_true, y_pred = main()
