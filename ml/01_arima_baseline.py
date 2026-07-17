"""
任务 1: ARIMA 基线模型
======================
选 5 个代表性饰品, 对每个做 7 日前瞻价格预测 (与树模型同一目标)。
作为"最差成绩"参考基线。

评估方式:
  - 拼接 train+val+test 的原始价格序列 (不用 drop Target 后的面板)
  - 在 test 段每个评估日 t: 用截至 t 的历史拟合/更新 ARIMA,
    预测 t+7 价格, 与真实 price[t+7] 对比

运行: python 01_arima_baseline.py
"""

import os
import sys
import numpy as np
import pandas as pd
from pmdarima import auto_arima
from sklearn.metrics import mean_squared_error, mean_absolute_error, r2_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import load_and_prepare, select_representative_skins, save_json, DATA_DIR

HORIZON = 7
# 每隔 step 天评估一次，控制运行时间；仍覆盖整个 test 段
EVAL_STEP = 7


def _load_raw_split(split):
    path = os.path.join(DATA_DIR, f"{split}.csv")
    return pd.read_csv(path, parse_dates=["date"])


def run_arima_for_skin(skin_name, train_raw, val_raw, test_raw):
    """对单个饰品做 7 日前瞻 walk-forward 评估"""
    parts = []
    for split_name, df in [("train", train_raw), ("val", val_raw), ("test", test_raw)]:
        sub = df[df["market_hash_name"] == skin_name][["date", "price"]].copy()
        if len(sub) == 0:
            continue
        sub["_split"] = split_name
        parts.append(sub)

    if not parts:
        return None

    series = pd.concat(parts, ignore_index=True).sort_values("date").reset_index(drop=True)
    prices = series["price"].values.astype(float)
    splits = series["_split"].values

    test_idx = np.where(splits == "test")[0]
    if (splits != "test").sum() < 30 or len(test_idx) < HORIZON + 1:
        return None

    # 可评估的 test 起点: 需要 t+7 仍在序列内
    max_t = len(prices) - HORIZON - 1
    eval_starts = [i for i in test_idx if i <= max_t]
    if not eval_starts:
        return None
    eval_starts = eval_starts[::EVAL_STEP]

    try:
        # 在截至第一个评估日的历史上选阶，之后用 update 增量扩展
        hist0 = prices[: eval_starts[0] + 1]
        model = auto_arima(
            hist0,
            seasonal=False,
            stepwise=True,
            suppress_warnings=True,
            max_p=5, max_q=5, max_d=2,
            error_action="ignore",
            trace=False,
        )
        order = model.order

        y_true_list = []
        y_pred_list = []
        last_end = eval_starts[0]

        for t in eval_starts:
            if t > last_end:
                new_obs = prices[last_end + 1: t + 1]
                if len(new_obs) > 0:
                    try:
                        model.update(new_obs)
                    except Exception:
                        model = auto_arima(
                            prices[: t + 1],
                            seasonal=False,
                            stepwise=True,
                            suppress_warnings=True,
                            max_p=5, max_q=5, max_d=2,
                            error_action="ignore",
                            trace=False,
                            start_p=order[0], start_q=order[2],
                            d=order[1],
                        )
                last_end = t

            fc = model.predict(n_periods=HORIZON)
            pred = float(np.maximum(fc[-1], 0.01))
            true = float(prices[t + HORIZON])
            y_pred_list.append(pred)
            y_true_list.append(true)

        y_true = np.array(y_true_list)
        y_pred = np.array(y_pred_list)

        rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
        mae = float(mean_absolute_error(y_true, y_pred))
        mape = float(np.mean(np.abs((y_true - y_pred) / np.maximum(y_true, 0.01))) * 100)
        r2 = float(r2_score(y_true, y_pred)) if len(y_true) > 2 else 0.0

        print(f"    {skin_name[:45]:45s}  RMSE={rmse:10.2f}  MAE={mae:10.2f}  "
              f"MAPE={mape:6.2f}%  n={len(y_true)}  "
              f"(p={order[0]},d={order[1]},q={order[2]})")
        return {
            "skin": skin_name,
            "rmse": rmse, "mae": mae, "mape": mape, "r2": r2,
            "order": list(order),
            "n_eval": len(y_true),
            "horizon": HORIZON,
        }
    except Exception as e:
        print(f"    {skin_name[:45]:45s}  FAILED: {e}")
        return None


def main():
    print("=" * 70)
    print("任务 1: ARIMA 基线模型 (7 日前瞻)")
    print("=" * 70)

    print("\n[1/3] 加载数据...")
    # 选皮肤用特征面板 train；ARIMA 价格序列用原始 CSV（保留末尾 7 天真值）
    train_feat = load_and_prepare("train")
    train_raw = _load_raw_split("train")
    val_raw = _load_raw_split("val")
    test_raw = _load_raw_split("test")

    print("[2/3] 选择 5 个代表性饰品...")
    skins = select_representative_skins(train_feat, n=5)
    print(f"  选中: {skins}")

    print(f"[3/3] 逐个跑 ARIMA (horizon={HORIZON}, step={EVAL_STEP})...\n")
    results = []
    for skin in skins:
        r = run_arima_for_skin(skin, train_raw, val_raw, test_raw)
        if r:
            results.append(r)

    if not results:
        print("  !! 没有成功跑出任何结果")
        return None

    avg_rmse = float(np.mean([r["rmse"] for r in results]))
    avg_mae = float(np.mean([r["mae"] for r in results]))
    avg_mape = float(np.mean([r["mape"] for r in results]))
    avg_r2 = float(np.mean([r["r2"] for r in results]))

    print(f"\n  ARIMA 平均:  RMSE={avg_rmse:.2f}  MAE={avg_mae:.2f}  "
          f"MAPE={avg_mape:.2f}%  R2={avg_r2:.4f}")

    summary = {
        "model": "ARIMA",
        "type": "统计基线",
        "horizon": HORIZON,
        "rmse": avg_rmse,
        "mae": avg_mae,
        "mape": avg_mape,
        "r2": avg_r2,
        "accuracy": None,
        "auc": None,
        "speed": "快",
        "interpretability": 3,
        "per_skin": results,
    }
    save_json(summary, "arima_results.json")
    return summary


if __name__ == "__main__":
    main()
