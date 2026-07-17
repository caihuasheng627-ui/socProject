"""
LSTM-D: 价格分层分组训练 (3 组独立 LSTM)
==============================================
参考: Lecture4 例 8 (IBM LSTM) + LSTM-C 同款特征/滑窗

策略 (对照 LSTM-C 的 Embedding 知识迁移, 按 team_tasks.md V2.0):
  - 按 train 集逐物品价格中位数分层:
      低价位 < 55% 分位 (Mil-Spec及以下 ≈ 84件)
      中价位 55%~87% 分位 (Classified/Restricted ≈ 50件)
      高价位 > 87% 分位 (刀/手套/Covert ≈ 20件)
  - 每组训练一个独立 Sequential LSTM (无 Embedding, 单输入)
  - 组边界存盘 → 新物品按自身中位数落组 (C 对新物品 Embedding 无效, D 天然泛化)

核心规则 (与 LSTM-C 一致):
  - 严禁跨物品拼序列, 必须 groupby 后逐物品构建滑动窗口
  - 训练在 log 空间, 预测后 expm1 还原真实价格
  - 每组独立 StandardScaler (x + y)

输出文件:
  - lstm_d_low.keras / lstm_d_mid.keras / lstm_d_high.keras   三组模型
  - lstm_d_scalers.pkl     每组 {x_scaler, y_scaler}
  - lstm_d_group_map.pkl   {boundaries: (q1, q2), item_group: {物品名: 组名}}
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
from tensorflow.keras.layers import Input, LSTM, Dense, Dropout

warnings.filterwarnings("ignore")

# ============================================================
# 超参数 (与 LSTM-C 对齐, 保证对比公平)
# ============================================================
LOOKBACK = 60           # 60 天窗口
HORIZON = 7             # 预测 7 天后
LSTM_UNITS = 50         # LSTM 隐藏单元数
DROPOUT = 0.2           # Dropout 比例
BATCH_SIZE = 32
EPOCHS = 100
LEARNING_RATE = 0.001

GROUP_NAMES = ["low", "mid", "high"]

# 输出的模型 / 映射文件
BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "models"
OUTPUT_DIR.mkdir(exist_ok=True)

MODEL_PATHS  = {g: OUTPUT_DIR / f"lstm_d_{g}.keras" for g in GROUP_NAMES}
SCALER_PATH  = OUTPUT_DIR / "lstm_d_scalers.pkl"
GROUP_MAP_PATH = OUTPUT_DIR / "lstm_d_group_map.pkl"


# ============================================================
# 第 1 步: 加载数据 + 特征工程 (与 LSTM-C 完全一致)
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
# 第 2 步: 按价格中位数分层 (team_tasks.md: 55% / 87% 分位)
#   - 边界只用 train 计算 (防泄漏)
#   - val 独有物品按自身中位数落组 (模拟上线时的新物品)
# ============================================================
def assign_groups(train_df, val_df):
    """返回 (boundaries, item_group 字典)"""
    med_train = train_df.groupby("market_hash_name")["price"].median()
    q1, q2 = med_train.quantile([0.55, 0.87])
    print(f"\n  分组边界 (train 逐物品中位价): 55%=${q1:.2f}, 87%=${q2:.2f}")

    def to_group(m):
        if m <= q1:
            return "low"
        elif m <= q2:
            return "mid"
        return "high"

    item_group = {name: to_group(m) for name, m in med_train.items()}

    # val 独有物品: 用它在 val 里的中位价落组
    med_val = val_df.groupby("market_hash_name")["price"].median()
    new_items = 0
    for name, m in med_val.items():
        if name not in item_group:
            item_group[name] = to_group(m)
            new_items += 1

    counts = pd.Series(item_group).value_counts()
    print(f"  组内物品数: low={counts.get('low', 0)}, "
          f"mid={counts.get('mid', 0)}, high={counts.get('high', 0)}")
    print(f"  val 新物品 (train 未见过): {new_items} 件")

    return (q1, q2), item_group


# ============================================================
# 第 3 步: 滑动窗口构建 (与 LSTM-C 同款 15 特征)
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
    """
    逐物品构建滑动窗口 X(60,15) → y(1), 单输入版 (无物品 ID)

    x_scaler:   已 fit 的 StandardScaler (val 时传入)
    fit_scaler: True → fit 新 scaler 并返回
    """
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

    # --- 组内全局 StandardScaler ---
    nsamples, nsteps, nfeats = X.shape
    if fit_scaler or x_scaler is None:
        x_scaler = StandardScaler()
        X = x_scaler.fit_transform(X.reshape(-1, nfeats)).reshape(nsamples, nsteps, nfeats)
    else:
        X = x_scaler.transform(X.reshape(-1, nfeats)).reshape(nsamples, nsteps, nfeats)

    if fit_scaler:
        return X, y, x_scaler
    return X, y


# ============================================================
# 第 4 步: 构建单组 LSTM (Lecture4 例 8 原味: LSTM(50)×2 + Dropout)
#          与 LSTM-C 唯一区别: 无 Embedding 分支
# ============================================================
def build_model(group_name):
    model = keras.Sequential(
        [
            Input(shape=(LOOKBACK, len(FEATURE_COLS))),
            LSTM(LSTM_UNITS, return_sequences=True),
            Dropout(DROPOUT),
            LSTM(LSTM_UNITS, return_sequences=False),
            Dropout(DROPOUT),
            Dense(1),
        ],
        name=f"LSTM_D_{group_name}",
    )
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=LEARNING_RATE),
        loss="mse",
        metrics=["mae"],
    )
    return model


# ============================================================
# 第 5 步: 训练单组
# ============================================================
def train_group(gname, train_g, val_g):
    """训练一组, 返回 (model, scalers, 组内评估 dict, y_true_price, y_pred_price)"""
    print("\n" + "=" * 60)
    print(f"训练组: {gname.upper()}  "
          f"(train {train_g['market_hash_name'].nunique()} 件 / "
          f"val {val_g['market_hash_name'].nunique()} 件)")
    print("=" * 60)

    X_train, y_train, x_scaler = build_sequences(train_g, fit_scaler=True)
    X_val, y_val = build_sequences(val_g, x_scaler=x_scaler)
    print(f"  滑动窗口: X_train={X_train.shape}, X_val={X_val.shape}")

    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train.reshape(-1, 1)).ravel()
    y_val_scaled   = y_scaler.transform(y_val.reshape(-1, 1)).ravel()
    print(f"  y scaler mean={y_scaler.mean_[0]:.4f}, scale={y_scaler.scale_[0]:.4f}")

    model = build_model(gname)
    model.summary()

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

    # --- 组内评估 (输出格式与 LSTM-C 第 6 步一致) ---
    y_pred_scaled = model.predict(X_val, verbose=0).ravel()
    y_pred = y_scaler.inverse_transform(y_pred_scaled.reshape(-1, 1)).ravel()

    rmse_log = np.sqrt(mean_squared_error(y_val, y_pred))
    mae_log  = mean_absolute_error(y_val, y_pred)
    print(f"\n  Val RMSE (log space): {rmse_log:.6f}")
    print(f"  Val MAE  (log space): {mae_log:.6f}")

    y_true_price = np.expm1(y_val)
    y_pred_price = np.expm1(y_pred)
    metrics = {
        "n_val": len(y_val),
        "epochs": len(history.history["loss"]),
        "params": model.count_params(),
        "mae":  mean_absolute_error(y_true_price, y_pred_price),
        "rmse": np.sqrt(mean_squared_error(y_true_price, y_pred_price)),
        "mape": np.mean(np.abs((y_true_price - y_pred_price) / y_true_price)) * 100,
        "r2":   r2_score(y_true_price, y_pred_price),
    }
    print(f"  Val RMSE (real USD):  ${metrics['rmse']:.4f}")
    print(f"  Val MAE  (real USD):  ${metrics['mae']:.4f}")
    print(f"  Val MAPE (real USD):   {metrics['mape']:.2f}%")
    print(f"  Val R²   (real USD):   {metrics['r2']:.4f}")
    print(f"  模型参数: {metrics['params']:,} | 训练轮数: {metrics['epochs']}")

    return model, {"x_scaler": x_scaler, "y_scaler": y_scaler}, metrics, y_true_price, y_pred_price


# ============================================================
# 第 6 步: 主流程
# ============================================================
def main():
    keras.utils.set_random_seed(42)

    # --- 加载 + 分组 ---
    train_df, val_df = load_data()

    print("\n" + "=" * 60)
    print("第 2 步: 按价格中位数分 3 组")
    print("=" * 60)
    boundaries, item_group = assign_groups(train_df, val_df)
    train_df["group"] = train_df["market_hash_name"].map(item_group)
    val_df["group"]   = val_df["market_hash_name"].map(item_group)

    # --- 逐组训练 ---
    scalers, group_metrics = {}, {}
    all_true, all_pred = [], []

    for gname in GROUP_NAMES:
        train_g = train_df[train_df["group"] == gname]
        val_g   = val_df[val_df["group"] == gname]
        model, sc, metrics, y_t, y_p = train_group(gname, train_g, val_g)

        model.save(MODEL_PATHS[gname])
        scalers[gname] = sc
        group_metrics[gname] = metrics
        all_true.append(y_t)
        all_pred.append(y_p)

    # --- 整体评估 (三组合并, 与 LSTM-C 同口径) ---
    print("\n" + "=" * 60)
    print("第 7 步: 整体评估 (三组合并)")
    print("=" * 60)
    y_true = np.concatenate(all_true)
    y_pred = np.concatenate(all_pred)
    overall = {
        "mae":  mean_absolute_error(y_true, y_pred),
        "rmse": np.sqrt(mean_squared_error(y_true, y_pred)),
        "mape": np.mean(np.abs((y_true - y_pred) / y_true)) * 100,
        "r2":   r2_score(y_true, y_pred),
    }

    print(f"\n  {'组':<6} {'样本':>7} {'MAE':>10} {'RMSE':>10} {'MAPE':>8} {'R²':>8} {'轮数':>5}")
    for gname in GROUP_NAMES:
        m = group_metrics[gname]
        print(f"  {gname:<6} {m['n_val']:>7,} ${m['mae']:>8.4f} "
              f"${m['rmse']:>8.4f} {m['mape']:>7.2f}% {m['r2']:>8.4f} {m['epochs']:>5}")
    print(f"  {'ALL':<6} {len(y_true):>7,} ${overall['mae']:>8.4f} "
          f"${overall['rmse']:>8.4f} {overall['mape']:>7.2f}% {overall['r2']:>8.4f}")
    print(f"\n  对照 LSTM-C V3: MAE $2.23 | RMSE $13.25 | R² 0.9891")

    # --- 保存 ---
    print("\n" + "=" * 60)
    print("第 8 步: 保存模型文件")
    print("=" * 60)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump(scalers, f)
    with open(GROUP_MAP_PATH, "wb") as f:
        pickle.dump({"boundaries": boundaries, "item_group": item_group}, f)

    for gname in GROUP_NAMES:
        print(f"  ✅ {MODEL_PATHS[gname]}")
    print(f"  ✅ {SCALER_PATH}")
    print(f"  ✅ {GROUP_MAP_PATH}")
    print("\n" + "=" * 60)
    print("LSTM-D 训练完成!")
    print("=" * 60)

    return group_metrics, overall


if __name__ == "__main__":
    group_metrics, overall = main()
