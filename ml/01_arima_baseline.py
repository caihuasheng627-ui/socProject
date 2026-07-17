"""
任务 1: ARIMA 基线模型
======================
选 5 个代表性饰品, 对每个跑 auto_arima, 记录 RMSE。
作为"最差成绩"参考基线。

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


def run_arima_for_skin(skin_name, train_df, test_df):
    """对单个饰品跑 ARIMA"""
    train_series = train_df[train_df["market_hash_name"] == skin_name].sort_values("date")
    test_series = test_df[test_df["market_hash_name"] == skin_name].sort_values("date")

    if len(train_series) < 30 or len(test_series) < 7:
        return None

    y_train = train_series["price"].values
    y_test = test_series["price"].values[:30]

    try:
        model = auto_arima(
            y_train,
            seasonal=False,
            stepwise=True,
            suppress_warnings=True,
            max_p=5, max_q=5, max_d=2,
            error_action="ignore",
            trace=False,
        )
        forecast = model.predict(n_periods=len(y_test))
        forecast = np.maximum(forecast, 0.01)

        rmse = float(np.sqrt(mean_squared_error(y_test, forecast)))
        mae = float(mean_absolute_error(y_test, forecast))
        mape = float(np.mean(np.abs((y_test - forecast) / np.maximum(y_test, 0.01))) * 100)
        r2 = float(r2_score(y_test, forecast)) if len(y_test) > 2 else 0.0

        print(f"    {skin_name[:45]:45s}  RMSE={rmse:10.2f}  MAE={mae:10.2f}  MAPE={mape:6.2f}%  (p={model.order[0]},d={model.order[1]},q={model.order[2]})")
        return {"skin": skin_name, "rmse": rmse, "mae": mae, "mape": mape, "r2": r2, "order": model.order}
    except Exception as e:
        print(f"    {skin_name[:45]:45s}  FAILED: {e}")
        return None


def main():
    print("=" * 70)
    print("任务 1: ARIMA 基线模型")
    print("=" * 70)

    print("\n[1/3] 加载数据...")
    train_df = load_and_prepare("train")
    test_df = pd.read_csv(os.path.join(DATA_DIR, "test.csv"), parse_dates=["date"])

    print("[2/3] 选择 5 个代表性饰品...")
    skins = select_representative_skins(train_df, n=5)
    print(f"  选中: {skins}")

    print("[3/3] 逐个跑 ARIMA...\n")
    results = []
    for skin in skins:
        r = run_arima_for_skin(skin, train_df, test_df)
        if r:
            results.append(r)

    if not results:
        print("  !! 没有成功跑出任何结果")
        return None

    avg_rmse = float(np.mean([r["rmse"] for r in results]))
    avg_mae = float(np.mean([r["mae"] for r in results]))
    avg_mape = float(np.mean([r["mape"] for r in results]))
    avg_r2 = float(np.mean([r["r2"] for r in results]))

    print(f"\n  ARIMA 平均:  RMSE={avg_rmse:.2f}  MAE={avg_mae:.2f}  MAPE={avg_mape:.2f}%  R2={avg_r2:.4f}")

    summary = {
        "model": "ARIMA",
        "type": "统计基线",
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
