"""
回测引擎: 逐日逐笔模拟交易
==============================================
参考: 策划书 5.4 + team_tasks.md 第 5 步

交易规则 (7 天预测 vs 当前价):
  预测涨幅 >= +2% → 买入 (若无持仓) / 持有 (若已持仓)
  预测跌幅 <= -2% → 卖出 (若持仓) / 观望 (若无持仓)
  ±2% 之间        → 不动

资金模型:
  - 初始资金默认 $10,000, 等权分给该模型覆盖的所有物品 (每件独立模拟)
  - 每日组合价值 = Σ(各物品 现金 + 持仓市值)
  - 自动附加 "买入持有" 基准 (首日全买入拿到底, 物品取所有模型的并集)

输入 CSV (每个模型一份, 组员 2 的四个模型也按这个发):
  date, market_hash_name, current_price, predicted_price

用法:
  python backtest.py LSTM-C=../data/preds/pred_lstm_c.csv XGBoost=../data/preds/pred_xgb.csv
  (不带 "标签=" 时用文件名作标签; 可选 --capital --fee --buy-th --sell-th)

输出 (../data/backtest/):
  - backtest_curve_<模型>.csv   累计资金曲线 DataFrame (date, capital)
  - backtest_curves.json        前端格式 {dates, series} (归一化到 100, 含买入持有)
  - backtest_results.json       {模型: {returnPct, maxDrawdownPct, trades, winRate}}
"""
import argparse
import json
import sys
import numpy as np
import pandas as pd
from pathlib import Path

# Windows GBK 控制台打印 ✅/⚠️ 会 UnicodeEncodeError, 统一转 UTF-8
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
OUTPUT_DIR = BASE / "outputs" / "backtest"


# ============================================================
# 第 1 步: 读预测 CSV
# ============================================================
REQUIRED_COLS = ["date", "market_hash_name", "current_price", "predicted_price"]

def load_predictions(path):
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} 缺少列: {missing} (需要 {REQUIRED_COLS})")
    df["date"] = pd.to_datetime(df["date"])
    # 同一物品同一天多行 → 保留最后一条
    df = (df.sort_values("date")
            .drop_duplicates(["market_hash_name", "date"], keep="last"))
    return df


# ============================================================
# 第 2 步: 单物品逐日模拟
# ============================================================
def simulate_item(group, budget, fee, buy_th, sell_th):
    """
    group: 单物品的预测 df (已按日期排序)
    返回: (逐日价值 dict{date: value}, 买入次数, 已平仓每笔盈亏 list)
    """
    cash, units = budget, 0.0
    buy_price = None
    values, trades, closed = {}, 0, []

    for row in group.itertuples(index=False):
        price, pred = row.current_price, row.predicted_price
        if price and price > 0 and not np.isnan(pred):
            exp_ret = (pred - price) / price
            if exp_ret >= buy_th and units == 0:
                units = cash * (1 - fee) / price          # 全仓买入
                buy_price = price
                cash = 0.0
                trades += 1
            elif exp_ret <= -sell_th and units > 0:
                cash = units * price * (1 - fee)          # 全仓卖出
                closed.append(price * (1 - fee) - buy_price * (1 + fee))
                units = 0.0
        values[row.date] = cash + units * (price if price > 0 else 0)

    return values, trades, closed


# ============================================================
# 第 3 步: 组合回测 (等权分配 + 逐物品模拟 + 按日合计)
# ============================================================
def run_backtest(pred_df, capital=10_000.0, fee=0.0, buy_th=0.02, sell_th=0.02):
    """返回 (curve_df: [date, capital], metrics dict)"""
    items = pred_df["market_hash_name"].unique()
    budget = capital / len(items)

    all_values, total_trades, all_closed = {}, 0, []
    for name, group in pred_df.groupby("market_hash_name"):
        values, trades, closed = simulate_item(
            group.sort_values("date"), budget, fee, buy_th, sell_th
        )
        all_values[name] = values
        total_trades += trades
        all_closed += closed

    # 对齐所有物品的日期: 缺失日向前填充, 上市前 = 预算现金
    value_df = pd.DataFrame(all_values).sort_index()
    value_df = value_df.ffill().fillna(budget)
    curve = value_df.sum(axis=1)

    curve_df = pd.DataFrame({"date": curve.index, "capital": curve.values})

    # --- 指标 ---
    peak = curve.cummax()
    metrics = {
        "n_items": int(len(items)),
        "returnPct": round((curve.iloc[-1] / capital - 1) * 100, 2),
        "maxDrawdownPct": round(((curve - peak) / peak).min() * 100, 2),
        "trades": int(total_trades),
        "winRate": round(float(np.mean([p > 0 for p in all_closed])) * 100, 1)
                   if all_closed else None,
    }
    return curve_df, metrics


# ============================================================
# 第 4 步: 买入持有基准 (首日全买, 拿到底)
# ============================================================
def buy_hold_curve(price_df, capital=10_000.0):
    """price_df: [date, market_hash_name, current_price] (所有模型物品并集)"""
    items = price_df["market_hash_name"].unique()
    budget = capital / len(items)

    all_values = {}
    for name, group in price_df.groupby("market_hash_name"):
        group = group.sort_values("date")
        first_price = group["current_price"].iloc[0]
        units = budget / first_price if first_price > 0 else 0
        all_values[name] = {
            row.date: units * row.current_price
            for row in group.itertuples(index=False)
        }

    value_df = pd.DataFrame(all_values).sort_index().ffill().fillna(budget)
    curve = value_df.sum(axis=1)
    return pd.DataFrame({"date": curve.index, "capital": curve.values})


# ============================================================
# 第 5 步: 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="SkinVest 回测引擎")
    parser.add_argument("specs", nargs="+",
                        help="预测CSV, 形如 标签=路径 或直接给路径")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.0, help="单边手续费率, 如 0.025")
    parser.add_argument("--buy-th", type=float, default=0.02)
    parser.add_argument("--sell-th", type=float, default=0.02)
    parser.add_argument("--outdir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"回测引擎: 资金 ${args.capital:,.0f} | 手续费 {args.fee:.1%} | "
          f"阈值 ±{args.buy_th:.0%}")
    print("=" * 60)

    curves, results, price_frames = {}, {}, []

    for spec in args.specs:
        label, _, path = spec.rpartition("=")
        if not label:
            label = Path(path).stem.removeprefix("pred_").removeprefix("predictions_")
        pred_df = load_predictions(path)
        price_frames.append(pred_df[["date", "market_hash_name", "current_price"]])

        curve_df, metrics = run_backtest(
            pred_df, args.capital, args.fee, args.buy_th, args.sell_th
        )
        curves[label] = curve_df
        results[label] = metrics

        csv_path = outdir / f"backtest_curve_{label}.csv"
        curve_df.to_csv(csv_path, index=False)
        win = f"{metrics['winRate']}%" if metrics["winRate"] is not None else "—"
        print(f"  [{label:<10}] 覆盖 {metrics['n_items']:>3} 件 | "
              f"收益 {metrics['returnPct']:>+7.2f}% | "
              f"最大回撤 {metrics['maxDrawdownPct']:>6.2f}% | "
              f"交易 {metrics['trades']:>4} 笔 | "
              f"胜率 {win}")

    # --- 买入持有基准 (物品并集) ---
    all_prices = (pd.concat(price_frames)
                    .drop_duplicates(["market_hash_name", "date"], keep="last"))
    bh_df = buy_hold_curve(all_prices, args.capital)
    curves["买入持有"] = bh_df
    bh_ret = round((bh_df["capital"].iloc[-1] / args.capital - 1) * 100, 2)
    results["买入持有"] = {"n_items": int(all_prices['market_hash_name'].nunique()),
                       "returnPct": bh_ret, "maxDrawdownPct": None,
                       "trades": None, "winRate": None}
    print(f"  [{'买入持有':<8}] 覆盖 {results['买入持有']['n_items']:>3} 件 | "
          f"收益 {bh_ret:>+7.2f}%")

    # --- 前端格式 JSON: 归一化到 100, 日期取所有曲线并集 ---
    all_dates = sorted(set().union(*[set(c["date"]) for c in curves.values()]))
    series = {}
    for label, cdf in curves.items():
        s = cdf.set_index("date")["capital"].reindex(all_dates).ffill()
        s = s.fillna(s.dropna().iloc[0])          # 起点前用首个有效值补
        series[label] = (s / s.iloc[0] * 100).round(2).tolist()

    curves_json = {
        "dates": [f"{d.month}/{d.day}" for d in all_dates],
        "series": series,
    }
    with open(outdir / "backtest_curves.json", "w", encoding="utf-8") as f:
        json.dump(curves_json, f, ensure_ascii=False, indent=2)
    with open(outdir / "backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ {outdir / 'backtest_curves.json'} (前端格式, 归一化 100)")
    print(f"  ✅ {outdir / 'backtest_results.json'}")
    print(f"  ✅ backtest_curve_<模型>.csv × {len(curves) - 1}")
    print("\n" + "=" * 60)
    print("回测完成!")
    print("=" * 60)
    return curves, results


if __name__ == "__main__":
    curves, results = main()
