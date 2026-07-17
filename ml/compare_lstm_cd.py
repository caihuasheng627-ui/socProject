"""
LSTM-C vs LSTM-D 分组对垒评估
==============================================
把 LSTM-C 的 val 预测按 D 的三组 (low/mid/high) 切开重新评估,
与 D 各组模型逐组对比 MAE / RMSE / MAPE / R² — 择优部署的依据。

前置条件 (先把两边都训练完):
  - lstm_c.keras + lstm_c_scaler.pkl + lstm_c_item_map.pkl
  - lstm_d_low/mid/high.keras + lstm_d_scalers.pkl + lstm_d_group_map.pkl

输出:
  - 控制台对垒表 (每组 C 一行 D 一行, ⭐ 标胜者)
  - ../data/models/lstm_cd_group_comparison.json (给汇总表/幻灯片用)
"""
import json
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
GROUP_NAMES = ["low", "mid", "high"]


def metric_block(y_true_log, y_pred_log):
    """log 空间的 y → 真实 USD 指标"""
    t, p = np.expm1(y_true_log), np.expm1(y_pred_log)
    return {
        "n": int(len(t)),
        "mae":  float(mean_absolute_error(t, p)),
        "rmse": float(np.sqrt(mean_squared_error(t, p))),
        "mape": float(np.mean(np.abs((t - p) / t)) * 100),
        "r2":   float(r2_score(t, p)),
    }


def main():
    from tensorflow import keras
    from feature_engineering import build_features
    # 复用两个训练脚本的滑窗构建, 保证窗口逻辑零偏差
    from train_lstm_c import build_sequences as build_seq_c
    from train_lstm_d import build_sequences as build_seq_d

    # ---------- 第 1 步: 加载 val + 特征 ----------
    print("=" * 60)
    print("第 1 步: 加载 val + 特征工程")
    print("=" * 60)
    val = pd.read_csv(BASE / "data" / "val.csv")
    val = build_features(val)
    print(f"  val: {len(val):,} 行, {val['market_hash_name'].nunique()} 件")

    # ---------- 第 2 步: 加载两边模型产物 ----------
    print("\n" + "=" * 60)
    print("第 2 步: 加载模型产物")
    print("=" * 60)
    need = [MODEL_DIR / "lstm_c.keras", MODEL_DIR / "lstm_d_group_map.pkl",
            MODEL_DIR / "lstm_d_scalers.pkl"] + \
           [MODEL_DIR / f"lstm_d_{g}.keras" for g in GROUP_NAMES]
    missing = [str(p) for p in need if not p.exists()]
    if missing:
        print("  ❌ 缺文件 (先把 LSTM-C / LSTM-D 都训练完):")
        for m in missing:
            print(f"     {m}")
        sys.exit(1)

    model_c = keras.models.load_model(MODEL_DIR / "lstm_c.keras")
    with open(MODEL_DIR / "lstm_c_scaler.pkl", "rb") as f:
        sc_c = pickle.load(f)                      # {y_scaler, x_scaler}
    with open(MODEL_DIR / "lstm_c_item_map.pkl", "rb") as f:
        item_map = pickle.load(f)

    with open(MODEL_DIR / "lstm_d_group_map.pkl", "rb") as f:
        gmap = pickle.load(f)                      # {boundaries, item_group}
    with open(MODEL_DIR / "lstm_d_scalers.pkl", "rb") as f:
        sc_d = pickle.load(f)                      # {组: {x_scaler, y_scaler}}
    models_d = {g: keras.models.load_model(MODEL_DIR / f"lstm_d_{g}.keras")
                for g in GROUP_NAMES}
    print(f"  ✅ LSTM-C + LSTM-D×3 加载完成, 分组边界 "
          f"55%=${gmap['boundaries'][0]:.2f}, 87%=${gmap['boundaries'][1]:.2f}")

    item_group = gmap["item_group"]
    val["group"] = val["market_hash_name"].map(item_group)

    # ---------- 第 3 步: LSTM-C 全量预测, 按组切开 ----------
    print("\n" + "=" * 60)
    print("第 3 步: LSTM-C 预测并按 D 的分组切开")
    print("=" * 60)
    Xp, Xi, y_log = build_seq_c(val, item_map, x_scaler=sc_c["x_scaler"])
    pred_scaled = model_c.predict([Xp, Xi], verbose=0).ravel()
    pred_log = sc_c["y_scaler"].inverse_transform(pred_scaled.reshape(-1, 1)).ravel()

    # 每个窗口的物品 id → 物品名 → 组
    id2name = {i: n for n, i in item_map.items()}
    win_group = np.array([item_group[id2name[i]] for i in Xi.ravel()])

    results = {g: {"LSTM-C": metric_block(y_log[win_group == g],
                                          pred_log[win_group == g])}
               for g in GROUP_NAMES}
    results["ALL"] = {"LSTM-C": metric_block(y_log, pred_log)}

    # ---------- 第 4 步: LSTM-D 逐组预测 ----------
    print("\n" + "=" * 60)
    print("第 4 步: LSTM-D 逐组预测")
    print("=" * 60)
    d_true_all, d_pred_all = [], []
    for g in GROUP_NAMES:
        val_g = val[val["group"] == g]
        Xg, yg = build_seq_d(val_g, x_scaler=sc_d[g]["x_scaler"])
        pg_scaled = models_d[g].predict(Xg, verbose=0).ravel()
        pg = sc_d[g]["y_scaler"].inverse_transform(pg_scaled.reshape(-1, 1)).ravel()

        results[g]["LSTM-D"] = metric_block(yg, pg)
        d_true_all.append(yg)
        d_pred_all.append(pg)

        n_c, n_d = results[g]["LSTM-C"]["n"], results[g]["LSTM-D"]["n"]
        if n_c != n_d:
            print(f"  ⚠️ [{g}] 样本数不一致: C={n_c}, D={n_d} (检查窗口构建)")
    results["ALL"]["LSTM-D"] = metric_block(
        np.concatenate(d_true_all), np.concatenate(d_pred_all)
    )

    # ---------- 第 5 步: 对垒表 ----------
    print("\n" + "=" * 60)
    print("第 5 步: 分组对垒 (⭐ = 该组该指标胜者)")
    print("=" * 60)
    print(f"\n  {'组':<5} {'模型':<8} {'样本':>7} {'MAE':>11} {'RMSE':>11} "
          f"{'MAPE':>9} {'R²':>9}")
    for g in GROUP_NAMES + ["ALL"]:
        c, d = results[g]["LSTM-C"], results[g]["LSTM-D"]
        for label, m, other in (("LSTM-C", c, d), ("LSTM-D", d, c)):
            star_mape = " ⭐" if m["mape"] < other["mape"] else ""
            star_r2   = " ⭐" if m["r2"] > other["r2"] else ""
            print(f"  {g:<5} {label:<8} {m['n']:>7,} ${m['mae']:>9.4f} "
                  f"${m['rmse']:>9.4f} {m['mape']:>7.2f}%{star_mape:<2} "
                  f"{m['r2']:>8.4f}{star_r2}")
        print()

    # 按整体 MAPE + R² 给出择优建议
    c_all, d_all = results["ALL"]["LSTM-C"], results["ALL"]["LSTM-D"]
    winner = "LSTM-C" if (c_all["mape"] < d_all["mape"]) else "LSTM-D"
    print(f"  整体口径建议部署: {winner}  "
          f"(C MAPE {c_all['mape']:.2f}% / R² {c_all['r2']:.4f}  vs  "
          f"D MAPE {d_all['mape']:.2f}% / R² {d_all['r2']:.4f})")

    # ---------- 保存 ----------
    out = MODEL_DIR / "lstm_cd_group_comparison.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n  ✅ {out}")
    return results


if __name__ == "__main__":
    results = main()
