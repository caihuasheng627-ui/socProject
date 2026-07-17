"""
make_predictions: C / D / Hybrid / GRU 预测导出 CSV
==============================================
给 backtest.py 生产统一格式输入 (与组员 2 的四模型同格式):
  date, market_hash_name, current_price, predicted_price

设计:
  - val 滑动窗口只构建一次 (逐物品, 与训练脚本同逻辑), 四套预测按行对齐
  - LSTM-C: 全量 | LSTM-D: 按组路由 | Hybrid: high→C, low/mid→D (部署方案)
  - GRU: 仅 top10 高流动性物品
  - 顺带输出: C/D/GRU 在 GRU 同 10 件上的公平对比 (架构结论依据)

验证: 导出后重算各模型整体指标, 应与训练时打印一致
  C: MAE $2.23 / R² 0.9891 | D: MAE $2.36 / R² 0.9883 | GRU: MAPE 11.02%

输出 (../data/preds/):
  pred_lstm_c.csv / pred_lstm_d.csv / pred_lstm_hybrid.csv / pred_gru.csv
"""
import numpy as np
import pandas as pd
import pickle
import sys
import warnings
from pathlib import Path
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
warnings.filterwarnings("ignore")

BASE = Path(__file__).resolve().parent
MODEL_DIR = BASE / "models"
PRED_DIR  = BASE / "preds"
GROUP_NAMES = ["low", "mid", "high"]


# ============================================================
# 滑动窗口 + 元信息 (date/物品/当前价), 与训练脚本同款循环
#   窗口 = 第 i-60 ~ i-1 天特征, 预测 Target[i] = 第 i+7 天 log 价
#   current_price = 第 i 天价格 (决策日价格, 不在输入窗口内, 无泄漏)
# ============================================================
def build_windows_with_meta(df, feature_cols, lookback):
    X, y, meta = [], [], []
    for name, group in df.groupby("market_hash_name"):
        group = group.sort_values("date")
        feat   = group[feature_cols].values.astype(np.float32)
        target = group["Target"].values.astype(np.float32)
        price  = group["price"].values
        dates  = group["date"].values
        for i in range(lookback, len(group)):
            if np.isnan(target[i]):
                continue
            X.append(feat[i - lookback : i])
            y.append(target[i])
            meta.append((dates[i], name, price[i]))
    X = np.array(X, dtype=np.float32)
    y = np.array(y, dtype=np.float32)
    meta = pd.DataFrame(meta, columns=["date", "market_hash_name", "current_price"])
    return X, y, meta


def scale_X(X, scaler):
    n, t, f = X.shape
    return scaler.transform(X.reshape(-1, f)).reshape(n, t, f)


def show_metrics(tag, y_true_price, y_pred_price):
    mae  = mean_absolute_error(y_true_price, y_pred_price)
    rmse = np.sqrt(mean_squared_error(y_true_price, y_pred_price))
    mape = np.mean(np.abs((y_true_price - y_pred_price) / y_true_price)) * 100
    r2   = r2_score(y_true_price, y_pred_price)
    print(f"  [{tag:<12}] MAE ${mae:>8.4f} | RMSE ${rmse:>8.4f} | "
          f"MAPE {mape:>6.2f}% | R² {r2:.4f}")
    return {"mae": mae, "rmse": rmse, "mape": mape, "r2": r2}


def export_csv(meta, pred_price, mask, path):
    out = meta.loc[mask, ["date", "market_hash_name", "current_price"]].copy()
    out["predicted_price"] = pred_price[mask]
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out.to_csv(path, index=False)
    print(f"  ✅ {path} ({len(out):,} 行, {out['market_hash_name'].nunique()} 件)")


def main():
    from tensorflow import keras
    from feature_engineering import build_features
    from train_lstm_c import FEATURE_COLS, LOOKBACK

    PRED_DIR.mkdir(parents=True, exist_ok=True)

    # ---------- 第 1 步: 数据 + 窗口 ----------
    print("=" * 60)
    print("第 1 步: 加载 val + 构建滑动窗口 (一次, 四模型共用)")
    print("=" * 60)
    val = build_features(pd.read_csv(BASE / "data" / "val.csv"))
    X, y_log, meta = build_windows_with_meta(val, FEATURE_COLS, LOOKBACK)
    y_true_price = np.expm1(y_log)
    print(f"  窗口: X={X.shape}, 共 {meta['market_hash_name'].nunique()} 件")

    # ---------- 第 2 步: 加载全部模型产物 ----------
    print("\n" + "=" * 60)
    print("第 2 步: 加载模型")
    print("=" * 60)
    model_c = keras.models.load_model(MODEL_DIR / "lstm_c.keras")
    with open(MODEL_DIR / "lstm_c_scaler.pkl", "rb") as f:
        sc_c = pickle.load(f)
    with open(MODEL_DIR / "lstm_c_item_map.pkl", "rb") as f:
        item_map = pickle.load(f)

    models_d = {g: keras.models.load_model(MODEL_DIR / f"lstm_d_{g}.keras")
                for g in GROUP_NAMES}
    with open(MODEL_DIR / "lstm_d_scalers.pkl", "rb") as f:
        sc_d = pickle.load(f)
    with open(MODEL_DIR / "lstm_d_group_map.pkl", "rb") as f:
        gmap = pickle.load(f)

    model_g = keras.models.load_model(MODEL_DIR / "gru.keras")
    with open(MODEL_DIR / "gru_scaler.pkl", "rb") as f:
        sc_g = pickle.load(f)
    with open(MODEL_DIR / "gru_items.pkl", "rb") as f:
        gru_items = pickle.load(f)
    print(f"  ✅ C + D×3 + GRU, GRU 覆盖 {len(gru_items)} 件")

    meta["group"] = meta["market_hash_name"].map(gmap["item_group"])

    # ---------- 第 3 步: 逐模型预测 (全部对齐 meta 行序) ----------
    print("\n" + "=" * 60)
    print("第 3 步: 预测 + 指标复核 (应与训练时一致)")
    print("=" * 60)

    # --- LSTM-C ---
    Xi = meta["market_hash_name"].map(item_map).values.reshape(-1, 1).astype(np.int32)
    p = model_c.predict([scale_X(X, sc_c["x_scaler"]), Xi],
                        verbose=0, batch_size=512).ravel()
    c_price = np.expm1(sc_c["y_scaler"].inverse_transform(p.reshape(-1, 1)).ravel())
    show_metrics("LSTM-C", y_true_price, c_price)

    # --- LSTM-D (按组路由, 写回对齐数组) ---
    d_price = np.full(len(meta), np.nan)
    for g in GROUP_NAMES:
        idx = (meta["group"] == g).values
        p = models_d[g].predict(scale_X(X[idx], sc_d[g]["x_scaler"]),
                                verbose=0, batch_size=512).ravel()
        d_price[idx] = np.expm1(
            sc_d[g]["y_scaler"].inverse_transform(p.reshape(-1, 1)).ravel())
    assert not np.isnan(d_price).any(), "有窗口没被任何组覆盖"
    show_metrics("LSTM-D", y_true_price, d_price)

    # --- Hybrid (部署方案: high→C, low/mid→D) ---
    h_price = np.where((meta["group"] == "high").values, c_price, d_price)
    show_metrics("Hybrid", y_true_price, h_price)

    # --- GRU (仅 10 件) ---
    gmask = meta["market_hash_name"].isin(gru_items).values
    p = model_g.predict(scale_X(X[gmask], sc_g["x_scaler"]),
                        verbose=0, batch_size=512).ravel()
    g_price = np.full(len(meta), np.nan)
    g_price[gmask] = np.expm1(
        sc_g["y_scaler"].inverse_transform(p.reshape(-1, 1)).ravel())
    show_metrics("GRU (10件)", y_true_price[gmask], g_price[gmask])

    # ---------- 第 4 步: 同 10 件公平对比 (架构结论) ----------
    print("\n" + "=" * 60)
    print("第 4 步: GRU 同 10 件高流动性物品上的公平对比")
    print("=" * 60)
    show_metrics("LSTM-C@10", y_true_price[gmask], c_price[gmask])
    show_metrics("LSTM-D@10", y_true_price[gmask], d_price[gmask])
    show_metrics("GRU@10",    y_true_price[gmask], g_price[gmask])

    # ---------- 第 5 步: 导出 ----------
    print("\n" + "=" * 60)
    print("第 5 步: 导出预测 CSV")
    print("=" * 60)
    all_mask = np.ones(len(meta), dtype=bool)
    export_csv(meta, c_price, all_mask, PRED_DIR / "pred_lstm_c.csv")
    export_csv(meta, d_price, all_mask, PRED_DIR / "pred_lstm_d.csv")
    export_csv(meta, h_price, all_mask, PRED_DIR / "pred_lstm_hybrid.csv")
    export_csv(meta, g_price, gmask,    PRED_DIR / "pred_gru.csv")

    print("\n" + "=" * 60)
    print("预测导出完成!")
    print("=" * 60)


if __name__ == "__main__":
    main()
