"""
Seq2Seq 30 天趋势模型: Encoder + 分位数回归 + Spread Penalty (PyTorch GPU)
========================================================================
模型输出 (30, 3): 每天 [P10, P50, P90] 三条分位线
  - P10: 下界 (τ=0.10)
  - P50: 中位 (τ=0.50)
  - P90: 上界 (τ=0.90)

说明:
  原生 Windows 上 TensorFlow>=2.11 无 CUDA; 本脚本改用 PyTorch + CUDA 训练。
  需: torch 带 cu121/cu124 等 CUDA 构建, 且 nvidia-smi 可见 GPU。

输出文件:
  - seq2seq_30d.pt          模型权重 (state_dict + 超参)
  - seq2seq_30d_scaler.pkl  {x_scaler, y_scaler}
"""

from __future__ import annotations

import pickle
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, TensorDataset

from forecast_contract import (
    add_grouped_targets_multi,
    build_sequence_windows_multi,
    load_feature_panel,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

# ============================================================
# 超参数
# ============================================================
LOOKBACK = 60
SEQ_HORIZON = 30
N_QUANTILES = 3
LSTM_UNITS = 64
DROPOUT = 0.2
BATCH_SIZE = 64          # GPU 可加大 batch
EPOCHS = 150
LEARNING_RATE = 0.001
SPREAD_LAMBDA_START = 0.0
SPREAD_LAMBDA_END = 0.05
SPREAD_RAMP_EPOCH = 30
MAX_SPREAD_FRAC = 0.18
EARLY_STOP_PATIENCE = 20
LR_PATIENCE = 10
MIN_LR = 1e-6
NUM_WORKERS = 0          # Windows DataLoader 稳妥用 0
SEED = 42

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "models"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MODEL_PATH = OUTPUT_DIR / "seq2seq_30d.pt"
SCALER_PATH = OUTPUT_DIR / "seq2seq_30d_scaler.pkl"

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
# 设备
# ============================================================

def configure_torch_device(require_gpu: bool = True) -> torch.device:
    """优先 CUDA GPU；不可用时按 require_gpu 报错或回退 CPU。"""
    torch.manual_seed(SEED)
    np.random.seed(SEED)

    if torch.cuda.is_available():
        device = torch.device("cuda")
        name = torch.cuda.get_device_name(0)
        props = torch.cuda.get_device_properties(0)
        mem_gb = props.total_memory / (1024 ** 3)
        print(f"[GPU] CUDA available · {name} · {mem_gb:.1f} GB")
        print(f"[GPU] torch={torch.__version__} · cuda={torch.version.cuda}")
        torch.cuda.manual_seed_all(SEED)
        # 允许 TF32 (Ampere+) 加速 matmul
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
        return device

    msg = (
        "[GPU] CUDA 不可用。当前环境无法用 GPU 训练。\n"
        "  请确认: 1) nvidia-smi 可见显卡  2) 安装带 CUDA 的 torch "
        "(例: pip install torch --index-url https://download.pytorch.org/whl/cu121)\n"
        "  注意: 原生 Windows 上 TensorFlow>=2.11 也不支持 CUDA。"
    )
    if require_gpu:
        raise RuntimeError(msg)
    print(msg)
    print("[CPU] fallback to CPU")
    return torch.device("cpu")


# ============================================================
# 模型
# ============================================================

class Seq2Seq30dQuantile(nn.Module):
    """LSTM Encoder → Dense → (30, 3) 分位数输出。"""

    def __init__(
        self,
        n_features: int = len(FEATURE_COLS),
        lookback: int = LOOKBACK,
        horizon: int = SEQ_HORIZON,
        n_quantiles: int = N_QUANTILES,
        units: int = LSTM_UNITS,
        dropout: float = DROPOUT,
    ):
        super().__init__()
        self.horizon = horizon
        self.n_quantiles = n_quantiles
        self.lstm1 = nn.LSTM(n_features, units, batch_first=True)
        self.drop1 = nn.Dropout(dropout)
        self.lstm2 = nn.LSTM(units, units, batch_first=True)
        self.drop2 = nn.Dropout(dropout)
        self.fc = nn.Linear(units, horizon * n_quantiles)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, 60, F)
        out, _ = self.lstm1(x)
        out = self.drop1(out)
        out, (h_n, _) = self.lstm2(out)
        h = self.drop2(h_n[-1])  # (B, units)
        y = self.fc(h)
        return y.view(-1, self.horizon, self.n_quantiles)


# ============================================================
# 损失
# ============================================================

def pinball_loss(y_true: torch.Tensor, y_pred: torch.Tensor, tau: float) -> torch.Tensor:
    err = y_true - y_pred
    return torch.mean(torch.maximum(tau * err, (tau - 1.0) * err))


def combined_quantile_loss(
    y_true: torch.Tensor,
    y_pred: torch.Tensor,
    spread_lambda: float,
) -> torch.Tensor:
    """
    y_true: (B, 30)
    y_pred: (B, 30, 3)  [P10, P50, P90]
    """
    p10 = y_pred[:, :, 0]
    p50 = y_pred[:, :, 1]
    p90 = y_pred[:, :, 2]
    loss = (
        pinball_loss(y_true, p10, 0.10)
        + pinball_loss(y_true, p50, 0.50)
        + pinball_loss(y_true, p90, 0.90)
    )
    spread = p90 - p10
    excess = torch.clamp(spread - MAX_SPREAD_FRAC, min=0.0)
    loss = loss + float(spread_lambda) * torch.mean(excess)
    return loss


def spread_lambda_for_epoch(epoch: int) -> float:
    if epoch < SPREAD_RAMP_EPOCH:
        return SPREAD_LAMBDA_START
    progress = min(1.0, (epoch - SPREAD_RAMP_EPOCH) / max(1, SPREAD_RAMP_EPOCH))
    return SPREAD_LAMBDA_START + (SPREAD_LAMBDA_END - SPREAD_LAMBDA_START) * progress


# ============================================================
# 数据
# ============================================================

def load_data() -> pd.DataFrame:
    print("=" * 60)
    print("第 1 步: 加载数据 + 特征工程 + 30 天 target")
    print("=" * 60)
    panel = load_feature_panel(DATA_DIR)
    panel = add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)
    for split in ("train", "val", "test"):
        part = panel[panel["_split"] == split]
        print(f"  {split}: {len(part):,} 行, {part['market_hash_name'].nunique()} 件")
    return panel


def build_sequences(df, sample_split, x_scaler=None, fit_scaler=False):
    X, y, _meta = build_sequence_windows_multi(
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


def make_loader(X, y, batch_size: int, shuffle: bool) -> DataLoader:
    ds = TensorDataset(
        torch.from_numpy(X.astype(np.float32)),
        torch.from_numpy(y.astype(np.float32)),
    )
    return DataLoader(
        ds,
        batch_size=batch_size,
        shuffle=shuffle,
        num_workers=NUM_WORKERS,
        pin_memory=torch.cuda.is_available(),
        drop_last=False,
    )


# ============================================================
# 评估
# ============================================================

@torch.no_grad()
def evaluate_model(model, X_val, y_val, y_scaler, device):
    model.eval()
    preds = []
    x_only = torch.from_numpy(X_val.astype(np.float32))
    step = BATCH_SIZE * 2
    for i in range(0, len(x_only), step):
        xb = x_only[i:i + step].to(device, non_blocking=True)
        preds.append(model(xb).cpu().numpy())
    y_pred_scaled = np.concatenate(preds, axis=0)

    y_pred_log = np.zeros_like(y_pred_scaled)
    for q in range(N_QUANTILES):
        y_pred_log[:, :, q] = y_scaler.inverse_transform(y_pred_scaled[:, :, q])
    y_pred_price = np.expm1(y_pred_log)
    y_true_price = np.expm1(y_val)

    p10, p50, p90 = y_pred_price[:, :, 0], y_pred_price[:, :, 1], y_pred_price[:, :, 2]
    mae_p50 = mean_absolute_error(y_true_price.ravel(), p50.ravel())
    rmse_p50 = float(np.sqrt(mean_squared_error(y_true_price.ravel(), p50.ravel())))
    coverage = float(((y_true_price >= p10) & (y_true_price <= p90)).mean())
    spread_pct = float(((p90 - p10) / np.maximum(p50, 0.01)).mean() * 100)

    print(f"\n  {'Period':<10} {'P50 MAE':>10} {'P50 RMSE':>10} {'Coverage':>10} {'AvgSpread%':>12}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    for t in [0, 6, 13, 20, 29]:
        yt, p50t, p10t, p90t = y_true_price[:, t], p50[:, t], p10[:, t], p90[:, t]
        mae_t = mean_absolute_error(yt, p50t)
        rmse_t = float(np.sqrt(mean_squared_error(yt, p50t)))
        cov_t = float(((yt >= p10t) & (yt <= p90t)).mean())
        spread_t = float(((p90t - p10t) / np.maximum(p50t, 0.01)).mean() * 100)
        print(f"  Day{t+1:<6} ${mae_t:>8.4f} ${rmse_t:>8.4f} {cov_t:>9.1%} {spread_t:>11.1f}%")

    return {
        "mae_p50": mae_p50,
        "rmse_p50": rmse_p50,
        "coverage": coverage,
        "avg_spread_pct": spread_pct,
    }


# ============================================================
# 训练
# ============================================================

def train_one_epoch(model, loader, optimizer, device, spread_lambda: float, scaler=None):
    model.train()
    total, n = 0.0, 0
    for xb, yb in loader:
        xb = xb.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)
        optimizer.zero_grad(set_to_none=True)
        if scaler is not None:
            with torch.amp.autocast("cuda", enabled=True):
                pred = model(xb)
                loss = combined_quantile_loss(yb, pred.float(), spread_lambda)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
        else:
            pred = model(xb)
            loss = combined_quantile_loss(yb, pred, spread_lambda)
            loss.backward()
            optimizer.step()
        bs = xb.size(0)
        total += float(loss.detach()) * bs
        n += bs
    return total / max(n, 1)


@torch.no_grad()
def validate_epoch(model, loader, device, spread_lambda: float):
    model.eval()
    total, n = 0.0, 0
    for xb, yb in loader:
        xb = xb.to(device, non_blocking=True)
        yb = yb.to(device, non_blocking=True)
        pred = model(xb)
        loss = combined_quantile_loss(yb, pred, spread_lambda)
        bs = xb.size(0)
        total += float(loss) * bs
        n += bs
    return total / max(n, 1)


def main(require_gpu: bool = True):
    device = configure_torch_device(require_gpu=require_gpu)

    panel = load_data()

    print("\n" + "=" * 60)
    print("第 2 步: 构建 30 天滑动窗口")
    print("=" * 60)
    X_train, y_train, x_scaler = build_sequences(panel, "train", fit_scaler=True)
    X_val, y_val = build_sequences(panel, "val", x_scaler=x_scaler)
    print(f"  train: {X_train.shape[0]:,} windows, val: {X_val.shape[0]:,} windows")

    y_scaler = StandardScaler()
    y_train_scaled = y_scaler.fit_transform(y_train)
    y_val_scaled = y_scaler.transform(y_val)
    print(f"  y scaler mean range: [{y_scaler.mean_.min():.4f}, {y_scaler.mean_.max():.4f}]")

    train_loader = make_loader(X_train, y_train_scaled, BATCH_SIZE, shuffle=True)
    val_loader = make_loader(X_val, y_val_scaled, BATCH_SIZE, shuffle=False)

    print("\n" + "=" * 60)
    print("第 3 步: 构建 Seq2Seq 30d 分位数模型 (PyTorch)")
    print("=" * 60)
    model = Seq2Seq30dQuantile().to(device)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"  params: {n_params:,}")
    print(f"  device: {device}")

    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5, patience=LR_PATIENCE, min_lr=MIN_LR
    )
    amp_scaler = torch.amp.GradScaler("cuda") if device.type == "cuda" else None

    print("\n" + "=" * 60)
    print("第 4 步: GPU 训练 (Pinball + spread penalty ramp)")
    print("=" * 60)

    best_val = float("inf")
    best_state = None
    wait = 0
    history = {"loss": [], "val_loss": [], "spread_lambda": [], "lr": []}
    t0 = time.time()

    for epoch in range(EPOCHS):
        lam = spread_lambda_for_epoch(epoch)
        tr = train_one_epoch(model, train_loader, optimizer, device, lam, amp_scaler)
        va = validate_epoch(model, val_loader, device, lam)
        scheduler.step(va)
        lr = optimizer.param_groups[0]["lr"]
        history["loss"].append(tr)
        history["val_loss"].append(va)
        history["spread_lambda"].append(lam)
        history["lr"].append(lr)

        improved = va < best_val - 1e-6
        if improved:
            best_val = va
            best_state = {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}
            wait = 0
        else:
            wait += 1

        mark = "*" if improved else " "
        print(
            f"  epoch {epoch+1:03d}/{EPOCHS}{mark}  "
            f"loss={tr:.5f}  val={va:.5f}  λ={lam:.4f}  lr={lr:.2e}"
        )

        if wait >= EARLY_STOP_PATIENCE:
            print(f"  early stop @ epoch {epoch+1} (best val={best_val:.5f})")
            break

    if best_state is not None:
        model.load_state_dict(best_state)
    model.to(device)
    elapsed = time.time() - t0
    print(f"  训练耗时: {elapsed/60:.1f} min")

    print("\n" + "=" * 60)
    print("第 5 步: 评估 (分位数覆盖率 + 区间宽度)")
    print("=" * 60)
    eval_result = evaluate_model(model, X_val, y_val, y_scaler, device)
    print(f"\n  整体 P50 MAE:    ${eval_result['mae_p50']:.4f}")
    print(f"  整体 P50 RMSE:   ${eval_result['rmse_p50']:.4f}")
    print(f"  P10-P90 覆盖率:  {eval_result['coverage']:.1%}  (目标 ~80%)")
    print(f"  平均区间宽度:    {eval_result['avg_spread_pct']:.1f}% of P50")

    print("\n" + "=" * 60)
    print("第 6 步: 保存模型文件")
    print("=" * 60)
    payload = {
        "state_dict": model.state_dict(),
        "framework": "pytorch",
        "device_trained": str(device),
        "hyperparams": {
            "lookback": LOOKBACK,
            "horizon": SEQ_HORIZON,
            "n_quantiles": N_QUANTILES,
            "lstm_units": LSTM_UNITS,
            "dropout": DROPOUT,
            "feature_cols": FEATURE_COLS,
            "batch_size": BATCH_SIZE,
            "epochs_ran": len(history["loss"]),
            "best_val_loss": best_val,
        },
        "history": history,
        "eval_val": eval_result,
    }
    torch.save(payload, MODEL_PATH)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump({"x_scaler": x_scaler, "y_scaler": y_scaler}, f)

    print(f"  ✅ {MODEL_PATH}")
    print(f"  ✅ {SCALER_PATH}")
    print(f"\n  模型参数: {n_params:,}")
    print(f"  训练轮数: {len(history['loss'])}")
    print("\n" + "=" * 60)
    print("Seq2Seq 30d 分位数模型 GPU 训练完成!")
    print("=" * 60)
    return model, history, eval_result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-cpu",
        action="store_true",
        help="无 GPU 时允许回退 CPU（默认要求 CUDA）",
    )
    args = parser.parse_args()
    main(require_gpu=not args.allow_cpu)
