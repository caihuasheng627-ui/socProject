"""
compare_models: 模型对比汇总 — 主表 A (回归) / 主表 B (方向分类) / 附表 C (GRU top10)
====================================================================================
评估口径铁律 (team_tasks.md):
  - 测试集: val.csv (时序 hold-out, 严禁随机打乱)
  - 价格空间: USD 原价 (log1p 训练 → expm1 还原后算 RMSE/MAE/MAPE)
  - 聚合方式: 全样本逐日误差聚合 (跨物品池化)
  - 树模型口径: 与 LSTM 同一 val 集, USD 还原空间, 可并排比较
  - GRU/top10: 只进附表 C

输出:
  - compare_results.json   → 前端模型实验室用
  - 终端三张格式化表
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PRED_DIR = Path("../data/preds")
BACKTEST_DIR = Path("../data/backtest")
OUTPUT_PATH = Path("../data/backtest/compare_results.json")

MODELS = {
    "LSTM-C": "pred_lstm_c.csv",
    "LSTM-D": "pred_lstm_d.csv",
    "Hybrid": "pred_lstm_hybrid.csv",
    "RF": "pred_rf.csv",
    "LightGBM": "pred_lightgbm.csv",
    "XGBoost": "pred_xgboost.csv",
}
GRU_FILE = "pred_gru.csv"
ARIMA_FILE = "pred_arima.csv"


# ============================================================
# 第 1 步: 加载预测 CSV + 计算回归指标
# ============================================================
def load_pred(path):
    df = pd.read_csv(PRED_DIR / path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def reg_metrics(y_true, y_pred):
    return {
        "rmse": round(np.sqrt(mean_squared_error(y_true, y_pred)), 2),
        "mae": round(mean_absolute_error(y_true, y_pred), 2),
        "mape": round(
            float(np.mean(np.abs((y_true - y_pred) / np.maximum(y_true, 0.01))) * 100), 2
        ),
        "r2": round(r2_score(y_true, y_pred), 4),
    }


# ============================================================
# 第 2 步: 方向分类指标 (用 val.csv 真实 7 天后价格)
# ============================================================
def compute_direction_metrics(pred_df, val_df):
    """
    pred_df: date, market_hash_name, current_price, predicted_price
    val_df: 原始 val.csv (含 price 列 = 当日真实价格)
    方向: sign(actual_future_price - current_price) vs sign(predicted_price - current_price)
    actual_future_price = val_df 中同物品 date+7days 的 price
    """
    val_prices = val_df[["date", "market_hash_name", "price"]].copy()
    val_prices["date"] = pd.to_datetime(val_prices["date"])
    val_prices = val_prices.set_index(["market_hash_name", "date"]).sort_index()

    actual_dirs, pred_dirs = [], []

    for row in pred_df.itertuples(index=False):
        try:
            current = row.current_price
            future = val_prices.loc[(row.market_hash_name, row.date + pd.Timedelta(days=7))].price
        except (KeyError, TypeError):
            continue
        if current <= 0 or future <= 0:
            continue
        actual_dirs.append(1 if future > current else 0)
        pred_dirs.append(1 if row.predicted_price > current else 0)

    if len(actual_dirs) < 10:
        return {"accuracy": None, "auc": None, "precision": None, "recall": None, "f1": None, "n": len(actual_dirs)}

    actual = np.array(actual_dirs)
    pred = np.array(pred_dirs)
    acc = accuracy_score(actual, pred)

    # AUC
    try:
        auc = roc_auc_score(actual, pred_dirs)
    except ValueError:
        auc = None

    # Precision / Recall / F1 (up=1 为正类)
    tp = int(np.sum((pred == 1) & (actual == 1)))
    fp = int(np.sum((pred == 1) & (actual == 0)))
    fn = int(np.sum((pred == 0) & (actual == 1)))
    precision = round(tp / (tp + fp), 4) if (tp + fp) > 0 else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) > 0 else None
    f1 = round(2 * precision * recall / (precision + recall), 4) if precision and recall and (precision + recall) > 0 else None

    return {
        "accuracy": round(float(acc), 4),
        "auc": round(float(auc), 4) if auc else None,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "n": len(actual_dirs),
    }


# ============================================================
# 第 3 步: 加载回测结果
# ============================================================
def load_backtest_results():
    path = BACKTEST_DIR / "backtest_results.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================
# 第 4 步: 格式化输出
# ============================================================
def print_sep(title):
    print(f"\n{'=' * 100}")
    print(f"  {title}")
    print(f"{'=' * 100}")


def print_table_a(results, backtest):
    """主表 A: 全量同口径回归 + 回测收益"""
    print_sep("主表 A — 全量同口径回归 (USD 还原空间, val.csv)")
    header = f"{'模型':<12} {'物品':>5} {'行数':>7} {'RMSE':>8} {'MAE':>8} {'MAPE':>8} {'R²':>8} {'回测收益':>10} {'最大回撤':>8} {'交易':>6} {'胜率':>6}"
    print(header)
    print("-" * 100)

    bt_key_map = {
        "LSTM-C": "lstm_c", "LSTM-D": "lstm_d", "Hybrid": "hybrid",
        "RF": "rf", "LightGBM": "lightgbm", "XGBoost": "xgboost",
        "ARIMA": "arima", "GRU": "gru",
    }
    order = ["LSTM-C", "LSTM-D", "Hybrid", "RF", "LightGBM", "XGBoost"]
    for name in order:
        if name not in results:
            continue
        r = results[name]
        bt_key = bt_key_map.get(name, name.lower())
        bt = backtest.get(bt_key, {})
        bt_ret = f"{bt.get('returnPct', '—'):>+9.1f}%" if isinstance(bt.get('returnPct'), (int, float)) else f"{'—':>10}"
        bt_dd = f"{bt.get('maxDrawdownPct', '—'):>7.1f}%" if isinstance(bt.get('maxDrawdownPct'), (int, float)) else f"{'—':>8}"
        bt_tr = f"{bt.get('trades', '—'):>5}" if isinstance(bt.get('trades'), (int, float)) and bt.get('trades') is not None else f"{'—':>6}"
        bt_wr = f"{bt.get('winRate', '—'):>5}%" if isinstance(bt.get('winRate'), (int, float)) and bt.get('winRate') is not None else f"{'—':>6}"

        print(
            f"{name:<12} {r['items']:>5} {r['rows']:>7} "
            f"${r['rmse']:>7.2f} ${r['mae']:>7.2f} "
            f"{r['mape']:>7.2f}% {r['r2']:>8.4f} "
            f"{bt_ret} {bt_dd} {bt_tr} {bt_wr}"
        )

    # 附录: ARIMA + GRU
    print("\n  附录 (口径不同, 不参与排名):")
    for name in ["ARIMA", "GRU"]:
        if name not in results:
            continue
        r = results[name]
        bt = backtest.get(bt_key_map.get(name, name.lower()), {})
        bt_ret = f"{bt.get('returnPct', '—'):>+9.1f}%" if isinstance(bt.get('returnPct'), (int, float)) else f"{'—':>10}"
        print(
            f"  {name:<10} {r['items']:>5} {r['rows']:>7} "
            f"${r['rmse']:>7.2f} ${r['mae']:>7.2f} "
            f"{r['mape']:>7.2f}% {r['r2']:>8.4f} "
            f"{bt_ret}  (口径: {r['note']})"
        )


def print_table_b(results):
    """主表 B: 方向分类"""
    print_sep("主表 B — 方向分类 (预测 7 天涨跌, val.csv)")
    header = f"{'模型':<12} {'样本':>6} {'Accuracy':>9} {'AUC':>7} {'Precision':>10} {'Recall':>7} {'F1':>7}"
    print(header)
    print("-" * 80)

    order = ["RF", "LightGBM", "XGBoost", "LSTM-C", "LSTM-D", "Hybrid"]
    for name in order:
        if name not in results or results[name].get("direction") is None:
            continue
        d = results[name]["direction"]
        if d.get("accuracy") is None:
            continue

        def fmt(v):
            return f"{v:.4f}" if isinstance(v, float) else "—"

        print(
            f"{name:<12} {d['n']:>6} "
            f"{fmt(d['accuracy']):>9} {fmt(d['auc']):>7} "
            f"{fmt(d['precision']):>10} {fmt(d['recall']):>7} {fmt(d['f1']):>7}"
        )


def print_table_c():
    """附表 C: GRU top10 公平对比 (从 LSTM 预测 CSV 中取相同 10 件)"""
    print_sep("附表 C — GRU top10 公平对比 (同 10 件高流动性物品)")

    gru_df = load_pred(GRU_FILE)
    gru_items = sorted(gru_df["market_hash_name"].unique())

    header = f"{'模型':<12} {'MAE':>8} {'RMSE':>8} {'MAPE':>8} {'R²':>8}"
    print(header)
    print("-" * 50)

    for label, fname in [("GRU", GRU_FILE), ("LSTM-C@10", "pred_lstm_c.csv"), ("LSTM-D@10", "pred_lstm_d.csv")]:
        df = load_pred(fname)
        sub = df[df["market_hash_name"].isin(gru_items)]
        if len(sub) == 0:
            continue
        m = reg_metrics(sub["current_price"].values, sub["predicted_price"].values)
        print(f"{label:<12} ${m['mae']:>7.2f} ${m['rmse']:>7.2f} {m['mape']:>7.2f}% {m['r2']:>8.4f}")


# ============================================================
# 第 5 步: 主流程
# ============================================================
def main():
    print("=" * 100)
    print("  CSVest — 模型对比汇总 (主表 A / B / C)")
    print("=" * 100)

    # 加载 val.csv 原始数据 (用于方向分类)
    val_df = pd.read_csv("../data/val.csv", parse_dates=["date"])

    # --- 回归指标 ---
    results = {}
    for name, fname in MODELS.items():
        df = load_pred(fname)
        m = reg_metrics(df["current_price"].values, df["predicted_price"].values)
        m["items"] = int(df["market_hash_name"].nunique())
        m["rows"] = len(df)
        results[name] = m

    # ARIMA + GRU (口径不同)
    for name, fname in [("ARIMA", ARIMA_FILE), ("GRU", GRU_FILE)]:
        df = load_pred(fname)
        m = reg_metrics(df["current_price"].values, df["predicted_price"].values)
        m["items"] = int(df["market_hash_name"].nunique())
        m["rows"] = len(df)
        m["note"] = "仅5件代表性物品" if name == "ARIMA" else "仅10件高流动性物品"
        results[name] = m

    # --- 方向分类 ---
    print("\n  计算方向分类指标 (需要匹配 val.csv 7天后真实价格)...")
    for name, fname in MODELS.items():
        df = load_pred(fname)
        results[name]["direction"] = compute_direction_metrics(df, val_df)
        print(f"    {name:<12} direction samples: {results[name]['direction']['n']}")

    # --- 回测 ---
    backtest = load_backtest_results()
    if backtest:
        print(f"\n  回测结果已加载 ({len(backtest)} 条)")

    # --- 打印三张表 ---
    print_table_a(results, backtest)
    print_table_b(results)
    print_table_c()

    # --- 前端 JSON ---
    # 只保留可序列化的字段, 加上回测结果
    export = {}
    for name, r in results.items():
        entry = {
            "rmse": r["rmse"],
            "mae": r["mae"],
            "mape": r["mape"],
            "r2": r["r2"],
            "items": r["items"],
            "rows": r["rows"],
            "direction": r.get("direction"),
        }
        # 匹配回测结果 (名映射)
        bt_key_map = {
            "LSTM-C": "lstm_c", "LSTM-D": "lstm_d", "Hybrid": "hybrid",
            "RF": "rf", "LightGBM": "lightgbm", "XGBoost": "xgboost",
            "ARIMA": "arima", "GRU": "gru",
        }
        bt_key = bt_key_map.get(name, name.lower())
        if bt_key in backtest:
            entry["backtest"] = backtest[bt_key]
        export[name] = entry

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(export, f, ensure_ascii=False, indent=2)

    # 顺带更新 backtest_curves.json 的 series 名 → 中文显示
    curves_path = BACKTEST_DIR / "backtest_curves.json"
    if curves_path.exists():
        with open(curves_path, encoding="utf-8") as f:
            curves = json.load(f)
        label_map = {
            "lstm_c": "LSTM-C", "lstm_d": "LSTM-D", "hybrid": "Hybrid",
            "rf": "RF", "lightgbm": "LightGBM", "xgboost": "XGBoost",
            "gru": "GRU", "arima": "ARIMA",
        }
        curves["series"] = {label_map.get(k, k): v for k, v in curves["series"].items()}
        with open(curves_path, "w", encoding="utf-8") as f:
            json.dump(curves, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ {OUTPUT_PATH} (前端模型实验室)")
    print(f"  ✅ {curves_path} (曲线标签已映射为中文)")
    print("\n" + "=" * 100)
    print("模型对比汇总完成!")
    print("=" * 100)


if __name__ == "__main__":
    main()
