"""
LSTM-D: 价格分层分组训练 (3 组独立 LSTM) — Seq2Seq 7 天版
===========================================================
参考: Lecture4 例 8 (IBM LSTM) + LSTM-C 同款特征/滑窗

策略 (对照 LSTM-C 的 Embedding 知识迁移):
  - 按 train 集逐物品价格中位数分层:
      低价位 < 55% 分位 (Mil-Spec及以下 ≈ 84件)
      中价位 55%~87% 分位 (Classified/Restricted ≈ 50件)
      高价位 > 87% 分位 (刀/手套/Covert ≈ 20件)
  - 每组训练一个独立 Sequential LSTM (无 Embedding, 单输入)
  - 组边界存盘 → 新物品按自身中位数落组 (C 对新物品 Embedding 无效, D 天然泛化)
  - 每组输出 Dense(7), 直接预测 7 天每日价格

核心规则 (与 LSTM-C 一致):
  - 严禁跨物品拼序列, 必须 groupby 后逐物品构建滑动窗口
  - 训练在 log 空间, 预测后 expm1 还原真实价格
  - 每组独立 StandardScaler (x + y)

输出文件:
  - lstm_d_low.keras / lstm_d_mid.keras / lstm_d_high.keras   三组模型 (Dense(7))
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
from forecast_contract import (
    HORIZON_STEPS,
    add_grouped_targets_multi,
    assign_price_groups,
    build_sequence_windows_multi,
    decode_log_price_predictions_multi,
    load_feature_panel,
    route_price_group,
)
from gpu_config import configure_device, create_dataset

warnings.filterwarnings("ignore")

# ============================================================
# 超参数 (与 LSTM-C 对齐, 保证对比公平)
# ============================================================
LOOKBACK = 60           # 60 天窗口
SEQ_HORIZON = 7         # 输出未来 7 天每日价格
LSTM_UNITS = 50         # LSTM 隐藏单元数
DROPOUT = 0.2           # Dropout 比例
BATCH_SIZE = 32
EPOCHS = 100
LEARNING_RATE = 0.001

GROUP_NAMES = ["low", "mid", "high"]

# 输出的模型 / 映射文件
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_PATHS  = {g: OUTPUT_DIR / f"lstm_d_{g}.keras" for g in GROUP_NAMES}
SCALER_PATH  = OUTPUT_DIR / "lstm_d_scalers.pkl"
GROUP_MAP_PATH = OUTPUT_DIR / "lstm_d_group_map.pkl"


# ============================================================
# 第 1 步: 加载数据 + 特征工程 (与 LSTM-C 完全一致)
# ============================================================
def load_data():
    """一次加载连续 train/val/test 面板, 计算特征 + 多步 target。"""
    print("=" * 60)
    print("第 1 步: 加载数据 + 特征工程 + 多步 target")
    print("=" * 60)

    panel = load_feature_panel(DATA_DIR)
    panel = add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)
    for split in ("train", "val", "test"):
        part = panel[panel["_split"] == split]
        print(f"  {split}: {len(part):,} 行, {part['market_hash_name'].nunique()} 件")
    return panel


# ============================================================
# 第 2 步: 按价格中位数分层 (team_tasks.md: 55% / 87% 分位)
#   - 边界只用 train 计算 (防泄漏)
#   - val 独有物品按自身中位数落组 (模拟上线时的新物品)
# ============================================================
def assign_groups(train_df):
    """只用训练集返回价格边界和已知物品固定分组。"""
    boundaries, item_group = assign_price_groups(train_df)
    q1, q2 = boundaries
    print(f"\n  分组边界 (train 逐物品中位价): 55%=${q1:.2f}, 87%=${q2:.2f}")

    counts = pd.Series(item_group).value_counts()
    print(f"  组内物品数: low={counts.get('low', 0)}, "
          f"mid={counts.get('mid', 0)}, high={counts.get('high', 0)}")
    return boundaries, item_group


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

def build_sequences(
    df, sample_split, group_name, boundaries, item_group,
    x_scaler=None, fit_scaler=False,
):
    """
    逐物品构建滑动窗口 X(60,15) → y(7)

    x_scaler:   已 fit 的 StandardScaler (val 时传入)
    fit_scaler: True → fit 新 scaler 并返回
    """
    X, y, meta = build_sequence_windows_multi(
        df, FEATURE_COLS, LOOKBACK, SEQ_HORIZON, sample_split=sample_split
    )
    routes = np.array([
        route_price_group(row.market_hash_name, row.current_price, item_group, boundaries)
        for row in meta.itertuples(index=False)
    ])
    mask = routes == group_name
    X, y = X[mask], y[mask]

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
            Dense(SEQ_HORIZON),
        ],
        name=f"LSTM_D_{group_name}_Seq7",
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
def train_group(gname, panel, boundaries, item_group):
    """训练一组, 返回 (model, scalers, 组内评估 dict, y_true_price, y_pred_price)"""
    print("\n" + "=" * 60)
    n_known = sum(group == gname for group in item_group.values())
    print(f"训练组: {gname.upper()} (train 已知物品 {n_known} 件)")
    print("=" * 60)

    X_train, y_train, x_scaler = build_sequences(
        panel, "train", gname, boundaries, item_group, fit_scaler=True
    )
    X_val, y_val = build_sequences(
        panel, "val", gname, boundaries, item_group, x_scaler=x_scaler
    )
    print(f"  滑动窗口: X_train={X_train.shape}, X_val={X_val.shape}")
    print(f"  y shape: train={y_train.shape}, val={y_val.shape}")

    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train)    # (n, 7)
    y_val_scaled = y_scaler.transform(y_val)
    print(f"  y scaler mean={y_scaler.mean_}, scale={y_scaler.scale_}")

    model = build_model(gname)
    model.summary()

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

    # --- 组内评估: 逐日 + 整体 ---
    y_pred_scaled = model.predict(X_val, verbose=0)  # (n, 7)
    y_pred_log = y_scaler.inverse_transform(y_pred_scaled)
    y_pred_price = np.expm1(y_pred_log)               # (n, 7) USD
    y_true_price = np.expm1(y_val)                    # (n, 7) USD

    # Day 7 only (对标旧版)
    yt_d7 = y_true_price[:, -1]
    yp_d7 = y_pred_price[:, -1]

    print(f"\n  {'Day':<8} {'RMSE':>10} {'MAE':>10} {'R²':>8}")
    for d in range(SEQ_HORIZON):
        yt = y_true_price[:, d]
        yp = y_pred_price[:, d]
        rmse = np.sqrt(mean_squared_error(yt, yp))
        mae = mean_absolute_error(yt, yp)
        r2 = r2_score(yt, yp)
        print(f"  Day{d+1:<5} ${rmse:>8.4f} ${mae:>8.4f} {r2:>8.4f}")

    metrics = {
        "n_val": len(y_val),
        "epochs": len(history.history["loss"]),
        "params": model.count_params(),
        "mae":  mean_absolute_error(yt_d7, yp_d7),
        "rmse": np.sqrt(mean_squared_error(yt_d7, yp_d7)),
        "mape": np.mean(np.abs((yt_d7 - yp_d7) / np.maximum(yt_d7, 0.01))) * 100,
        "r2":   r2_score(yt_d7, yp_d7),
        "mae_all": mean_absolute_error(y_true_price.ravel(), y_pred_price.ravel()),
        "rmse_all": np.sqrt(mean_squared_error(y_true_price.ravel(), y_pred_price.ravel())),
    }
    print(f"\n  Day 7 only (对标旧版): RMSE=${metrics['rmse']:.4f}  MAE=${metrics['mae']:.4f}  R²={metrics['r2']:.4f}")
    print(f"  模型参数: {metrics['params']:,} | 训练轮数: {metrics['epochs']}")

    return model, {"x_scaler": x_scaler, "y_scaler": y_scaler}, metrics, y_true_price, y_pred_price


# ============================================================
# 第 6 步: 主流程
# ============================================================
def main():
    keras.utils.set_random_seed(42)
    configure_device()

    # --- 加载 + 分组 ---
    panel = load_data()
    train_df = panel[panel["_split"] == "train"]

    print("\n" + "=" * 60)
    print("第 2 步: 按价格中位数分 3 组")
    print("=" * 60)
    boundaries, item_group = assign_groups(train_df)

    # --- 逐组训练 ---
    scalers, group_metrics = {}, {}
    all_true, all_pred = [], []

    for gname in GROUP_NAMES:
        model, sc, metrics, y_t, y_p = train_group(
            gname, panel, boundaries, item_group
        )

        model.save(MODEL_PATHS[gname])
        scalers[gname] = sc
        group_metrics[gname] = metrics
        all_true.append(y_t)
        all_pred.append(y_p)

    # --- 整体评估 (三组合并, Day 7 only + all days) ---
    print("\n" + "=" * 60)
    print("第 7 步: 整体评估 (三组合并)")
    print("=" * 60)
    y_true_all = np.concatenate(all_true)   # each (n_g, 7)
    y_pred_all = np.concatenate(all_pred)

    # Day 7 only
    yt7 = y_true_all[:, -1]
    yp7 = y_pred_all[:, -1]
    overall = {
        "mae":  mean_absolute_error(yt7, yp7),
        "rmse": np.sqrt(mean_squared_error(yt7, yp7)),
        "mape": np.mean(np.abs((yt7 - yp7) / np.maximum(yt7, 0.01))) * 100,
        "r2":   r2_score(yt7, yp7),
        "mae_all": mean_absolute_error(y_true_all.ravel(), y_pred_all.ravel()),
        "rmse_all": np.sqrt(mean_squared_error(y_true_all.ravel(), y_pred_all.ravel())),
    }

    print(f"\n  {'组':<6} {'样本':>7} {'MAE(d7)':>10} {'RMSE(d7)':>10} {'R²(d7)':>8} {'轮数':>5}")
    for gname in GROUP_NAMES:
        m = group_metrics[gname]
        print(f"  {gname:<6} {m['n_val']:>7,} ${m['mae']:>8.4f} "
              f"${m['rmse']:>8.4f} {m['r2']:>8.4f} {m['epochs']:>5}")
    print(f"  {'ALL':<6} {len(y_true_all):>7,} ${overall['mae']:>8.4f} "
          f"${overall['rmse']:>8.4f} {overall['r2']:>8.4f}")
    print(f"  ALL (7天展平) MAE=${overall['mae_all']:.4f} RMSE=${overall['rmse_all']:.4f}")
    print(f"\n  对照见 compare_results_test.json（公平 test 口径）")

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
