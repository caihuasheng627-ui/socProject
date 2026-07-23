"""
Seq2Seq 30 天趋势模型: Encoder + 分位数回归 + Spread Penalty
=============================================================
模型输出 (30, 3): 每天 [P10, P50, P90] 三条分位线
  - P10: 下界 (τ=0.10) — 保守估计
  - P50: 中位 (τ=0.50) — 最可能的价格路径
  - P90: 上界 (τ=0.90) — 乐观估计

损失函数:
  - Pinball (quantile) loss 对三条线分别计算
  - Spread penalty: 约束 P90-P10 宽度, 防止区间过大失去参考价值
  - 训练早期 λ_spread=0, 逐渐开启 → 先学分位数再收紧区间

输入: (60, 15) 历史特征 (与 7 天模型同款)
输出: (30, 3) 未来 30 天每日 [P10, P50, P90] (log_price 空间)

输出文件:
  - seq2seq_30d.keras       模型权重
  - seq2seq_30d_scaler.pkl  {x_scaler, y_scaler}
"""

import numpy as np
import pandas as pd
import pickle
import sys
import warnings
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import tensorflow as tf
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_squared_error, mean_absolute_error
from tensorflow import keras
from tensorflow.keras.layers import Input, LSTM, Dense, Dropout, Reshape
from forecast_contract import (
    add_grouped_targets_multi,
    build_sequence_windows_multi,
    load_feature_panel,
)
from gpu_config import configure_device, create_dataset

warnings.filterwarnings("ignore")

# ============================================================
# 超参数
# ============================================================
LOOKBACK = 60            # 60 天历史窗口
SEQ_HORIZON = 30         # 预测未来 30 天
N_QUANTILES = 3          # P10, P50, P90
LSTM_UNITS = 64          # 比 7 天模型略大
DROPOUT = 0.2
BATCH_SIZE = 32
EPOCHS = 150             # 更长序列需要更多轮数
LEARNING_RATE = 0.001
SPREAD_LAMBDA_START = 0.0       # 初始 spread penalty 权重
SPREAD_LAMBDA_END = 0.05        # 最终 spread penalty 权重
SPREAD_RAMP_EPOCH = 30          # 从第 N 轮开始逐渐增加 penalty
MAX_SPREAD_FRAC = 0.18          # 区间宽度上限 (相对于当前价格的比例, log 空间)

# 输出的模型 / 映射文件
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_PATH = OUTPUT_DIR / "seq2seq_30d.keras"
SCALER_PATH = OUTPUT_DIR / "seq2seq_30d_scaler.pkl"

# 特征列 (与 7 天模型完全一致)
FEATURE_COLS = [
    "log_price",
    "MA_7", "MA_30", "MA_90",
    "Return_1d", "Return_7d",
    "Volatility_30",
    "RSI_14", "MACD",
    "volume_ma_log", "daily_volume_log",
    "is_floor_price", "is_stattrak",
    "is_major_active", "steam_ccu",
]


# ============================================================
# 损失函数
# ============================================================

def pinball_loss(y_true, y_pred, tau):
    """Pinball (quantile) loss for a single quantile τ."""
    error = y_true - y_pred
    return tf.reduce_mean(tf.maximum(tau * error, (tau - 1.0) * error))


def combined_quantile_loss(y_true, y_pred):
    """
    Total loss = Pinball(P10) + Pinball(P50) + Pinball(P90) + spread_penalty.

    y_true: (batch, 30)  — true log_price for each future day
    y_pred: (batch, 30, 3) — [P10, P50, P90] for each day

    Uses a global step counter to ramp spread penalty over training.
    """
    p10 = y_pred[:, :, 0]  # τ=0.10
    p50 = y_pred[:, :, 1]  # τ=0.50
    p90 = y_pred[:, :, 2]  # τ=0.90

    # Pinball losses
    loss_p10 = pinball_loss(y_true, p10, 0.10)
    loss_p50 = pinball_loss(y_true, p50, 0.50)
    loss_p90 = pinball_loss(y_true, p90, 0.90)

    # Spread penalty: penalize P90-P10 exceeding max_spread
    spread = p90 - p10  # (batch, 30)
    max_spread = MAX_SPREAD_FRAC  # in log space, ~18% corresponds to ~20% in real price
    excess = tf.maximum(spread - max_spread, 0.0)
    spread_penalty = tf.reduce_mean(excess)

    # Dynamic lambda — ramped via a tf.Variable updated by callback
    lam = combined_quantile_loss.spread_lambda

    total = loss_p10 + loss_p50 + loss_p90 + lam * spread_penalty
    return total


# Attach a mutable lambda variable to the loss function
combined_quantile_loss.spread_lambda = tf.Variable(SPREAD_LAMBDA_START, trainable=False, dtype=tf.float32)


class SpreadLambdaScheduler(keras.callbacks.Callback):
    """Ramp up the spread penalty weight during training."""

    def __init__(self, lambda_var, start_epoch=SPREAD_RAMP_EPOCH,
                 lambda_start=SPREAD_LAMBDA_START, lambda_end=SPREAD_LAMBDA_END):
        super().__init__()
        self.lambda_var = lambda_var
        self.start_epoch = start_epoch
        self.lambda_start = lambda_start
        self.lambda_end = lambda_end

    def on_epoch_begin(self, epoch, logs=None):
        if epoch < self.start_epoch:
            new_val = self.lambda_start
        else:
            progress = min(1.0, (epoch - self.start_epoch) / max(1, self.start_epoch))
            new_val = self.lambda_start + (self.lambda_end - self.lambda_start) * progress
        self.lambda_var.assign(new_val)

    def on_epoch_end(self, epoch, logs=None):
        if logs is not None:
            logs["spread_lambda"] = float(self.lambda_var.numpy())


# ============================================================
# 数据加载
# ============================================================

def load_data():
    """加载面板 + 30 天 multi-step targets。"""
    print("=" * 60)
    print("第 1 步: 加载数据 + 特征工程 + 30 天 target")
    print("=" * 60)

    panel = load_feature_panel(DATA_DIR)
    panel = add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)
    for split in ("train", "val", "test"):
        part = panel[panel["_split"] == split]
        print(f"  {split}: {len(part):,} 行, {part['market_hash_name'].nunique()} 件")
    return panel


# ============================================================
# 滑动窗口
# ============================================================

def build_sequences(df, sample_split, x_scaler=None, fit_scaler=False):
    """构建 X(60,15) → y(30) 多步窗口。"""
    X, y, meta = build_sequence_windows_multi(
        df, FEATURE_COLS, LOOKBACK, SEQ_HORIZON, sample_split=sample_split
    )

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
# 模型构建
# ============================================================

def build_model():
    """
    LSTM Encoder → Dense(90) → Reshape(30, 3)

    输出: (None, 30, 3)  → 每天 [P10_log, P50_log, P90_log]
    """
    inp = Input(shape=(LOOKBACK, len(FEATURE_COLS)), name="price_input")

    x = LSTM(LSTM_UNITS, return_sequences=True, name="lstm_1")(inp)
    x = Dropout(DROPOUT, name="dropout_1")(x)

    x = LSTM(LSTM_UNITS, return_sequences=False, name="lstm_2")(x)
    x = Dropout(DROPOUT, name="dropout_2")(x)

    # 90 = 30 days × 3 quantiles
    x = Dense(SEQ_HORIZON * N_QUANTILES, name="dense_out")(x)
    out = Reshape((SEQ_HORIZON, N_QUANTILES), name="output")(x)

    model = keras.Model(inputs=inp, outputs=out, name="Seq2Seq_30d_Quantile")

    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=LEARNING_RATE),
        loss=combined_quantile_loss,
    )

    return model


# ============================================================
# 评估函数
# ============================================================

def evaluate_model(model, X_val, y_val, y_scaler):
    """逐区间评估: P50 MAE/RMSE, P10-P90 覆盖率, spread 均值。"""
    y_pred_scaled = model.predict(X_val, verbose=0)  # (n, 30, 3)

    # Inverse transform: 每个 quantile 独立 inverse (y_scaler 在 30 天上 fit)
    n_samples = y_pred_scaled.shape[0]
    y_pred_log = np.zeros_like(y_pred_scaled)
    for q in range(N_QUANTILES):
        y_pred_log[:, :, q] = y_scaler.inverse_transform(y_pred_scaled[:, :, q])
    y_pred_price = np.expm1(y_pred_log)

    y_true_price = np.expm1(y_val)  # (n, 30)

    p10 = y_pred_price[:, :, 0]
    p50 = y_pred_price[:, :, 1]
    p90 = y_pred_price[:, :, 2]

    # P50 accuracy
    mae_p50 = mean_absolute_error(y_true_price.ravel(), p50.ravel())
    rmse_p50 = np.sqrt(mean_squared_error(y_true_price.ravel(), p50.ravel()))

    # Coverage: fraction of true values inside [P10, P90]
    covered = (y_true_price >= p10) & (y_true_price <= p90)
    coverage = float(covered.mean())

    # Average spread (P90 - P10) as fraction of P50 price
    spread_abs = (p90 - p10).mean()
    spread_pct = float(((p90 - p10) / np.maximum(p50, 0.01)).mean() * 100)

    # Per-period breakdown
    print(f"\n  {'Period':<10} {'P50 MAE':>10} {'P50 RMSE':>10} {'Coverage':>10} {'AvgSpread%':>12}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    for t in [0, 6, 13, 20, 29]:  # day 1, 7, 14, 21, 30
        yt = y_true_price[:, t]
        p50t = p50[:, t]
        p10t = p10[:, t]
        p90t = p90[:, t]
        mae_t = mean_absolute_error(yt, p50t)
        rmse_t = np.sqrt(mean_squared_error(yt, p50t))
        cov_t = float(((yt >= p10t) & (yt <= p90t)).mean())
        spread_t = float(((p90t - p10t) / np.maximum(p50t, 0.01)).mean() * 100)
        print(f"  Day{t+1:<6} ${mae_t:>8.4f} ${rmse_t:>8.4f} {cov_t:>9.1%} {spread_t:>11.1f}%")

    return {
        "mae_p50": mae_p50,
        "rmse_p50": rmse_p50,
        "coverage": coverage,
        "avg_spread_pct": spread_pct,
        "avg_spread_abs": spread_abs,
    }


# ============================================================
# 主流程
# ============================================================

def main():
    keras.utils.set_random_seed(42)
    configure_device()

    # --- 加载 ---
    panel = load_data()

    # --- 构建序列 ---
    print("\n" + "=" * 60)
    print("第 2 步: 构建 30 天滑动窗口")
    print("=" * 60)
    X_train, y_train, x_scaler = build_sequences(panel, "train", fit_scaler=True)
    X_val, y_val = build_sequences(panel, "val", x_scaler=x_scaler)
    print(f"  train: {X_train.shape[0]:,} windows, val: {X_val.shape[0]:,} windows")

    # y scaler: 对 y 展平后 fit (30 维 per sample, 列独立缩放)
    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train)
    y_val_scaled = y_scaler.transform(y_val)
    print(f"  y scaler mean range: [{y_scaler.mean_.min():.4f}, {y_scaler.mean_.max():.4f}]")
    print(f"  y scaler scale range: [{y_scaler.scale_.min():.4f}, {y_scaler.scale_.max():.4f}]")

    # --- 构建模型 ---
    print("\n" + "=" * 60)
    print("第 3 步: 构建 Seq2Seq 30d 分位数模型")
    print("=" * 60)
    model = build_model()
    model.summary()

    # --- 训练 ---
    print("\n" + "=" * 60)
    print("第 4 步: 训练 (Pinball loss + spread penalty ramp)")
    print("=" * 60)
    early_stop = keras.callbacks.EarlyStopping(
        monitor="val_loss", patience=20, restore_best_weights=True
    )
    reduce_lr = keras.callbacks.ReduceLROnPlateau(
        monitor="val_loss", factor=0.5, patience=10, min_lr=1e-6
    )
    spread_scheduler = SpreadLambdaScheduler(combined_quantile_loss.spread_lambda)

    history = model.fit(
        create_dataset(X_train, y_train_scaled, batch_size=BATCH_SIZE, shuffle=True),
        validation_data=create_dataset(X_val, y_val_scaled, batch_size=BATCH_SIZE, shuffle=False),
        epochs=EPOCHS,
        callbacks=[early_stop, reduce_lr, spread_scheduler],
        verbose=1,
    )

    # --- 评估 ---
    print("\n" + "=" * 60)
    print("第 5 步: 评估 (分位数覆盖率 + 区间宽度)")
    print("=" * 60)
    eval_result = evaluate_model(model, X_val, y_val, y_scaler)

    print(f"\n  整体 P50 MAE:    ${eval_result['mae_p50']:.4f}")
    print(f"  整体 P50 RMSE:   ${eval_result['rmse_p50']:.4f}")
    print(f"  P10-P90 覆盖率:  {eval_result['coverage']:.1%}  (目标 ~80%)")
    print(f"  平均区间宽度:    {eval_result['avg_spread_pct']:.1f}% of P50")

    # --- 保存 ---
    print("\n" + "=" * 60)
    print("第 6 步: 保存模型文件")
    print("=" * 60)
    model.save(MODEL_PATH)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump({"x_scaler": x_scaler, "y_scaler": y_scaler}, f)

    print(f"  ✅ {MODEL_PATH}")
    print(f"  ✅ {SCALER_PATH}")
    print(f"\n  模型参数: {model.count_params():,}")
    print(f"  训练轮数: {len(history.history['loss'])}")
    print("\n" + "=" * 60)
    print("Seq2Seq 30d 分位数模型训练完成!")
    print("=" * 60)

    return model, history, eval_result


if __name__ == "__main__":
    model, history, eval_result = main()
