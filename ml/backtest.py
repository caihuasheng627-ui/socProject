"""
回测引擎 V2: 逐日逐笔模拟交易
==============================================
修复 (7/19 方案 D):
  A. 统一日期网格 — 只保留所有 154 件物品都有数据的日期 (去稀疏跳空)
  B. 单物品收益 cap +200% — 极端值截断
  C. 固定持有期 — BUY 后必须持有 7 天才允许 SELL (对齐预测 horizon)

交易规则:
  预测涨幅 >= +2% → 买入 (若无持仓)
  预测跌幅 <= -2% → 卖出 (若持仓且已持满 7 天)
  ±2% 之间        → 不动

资金模型:
  - 初始资金默认 $10,000, 等权分给该模型覆盖的所有物品
  - 每日组合价值 = Σ(各物品 现金 + 持仓市值)
  - 自动附加 "买入持有" 基准

用法:
  python backtest.py LSTM-C=../data/preds/pred_lstm_c.csv XGBoost=../data/preds/pred_xgb.csv
"""
import argparse
import json
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUTPUT_DIR = Path("../data/backtest")
REQUIRED_COLS = ["date", "market_hash_name", "current_price", "predicted_price"]
HOLD_DAYS = 7          # 方案 C: 对齐预测 horizon


# ============================================================
# 第 1 步: 读预测 CSV + 统一日期网格 (方案 A)
# ============================================================
def load_predictions(path):
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"{path} 缺少列: {missing}")
    df["date"] = pd.to_datetime(df["date"])
    df = (df.sort_values("date")
            .drop_duplicates(["market_hash_name", "date"], keep="last"))
    return df


def align_date_grid(pred_df, min_pct=0.80):
    """
    方案 A: 只保留 ≥min_pct 物品都有数据的日期。
    缺失物品用前向填充 (ffill) 补齐。
    """
    n_items = pred_df["market_hash_name"].nunique()
    min_n = int(n_items * min_pct)
    date_counts = pred_df.groupby("date")["market_hash_name"].nunique()
    valid_dates = sorted(date_counts[date_counts >= min_n].index)
    if not valid_dates:
        raise RuntimeError(f"没有日期满足 ≥{min_pct:.0%} 物品 ({min_n}/{n_items})!")
    aligned = pred_df[pred_df["date"].isin(valid_dates)].copy()
    return aligned, valid_dates


# ============================================================
# 第 2 步: 单物品模拟 (方案 C: 强制持满 7 天)
# ============================================================
def simulate_item(group, budget, fee, buy_th, sell_th):
    """
    group: 单物品预测 df (已按日期排序, 日期网格已对齐)
    方案 C: BUY 后必须持有 HOLD_DAYS 天才允许 SELL
    """
    cash, units = budget, 0.0
    buy_price = None
    buy_day_idx = -1           # 买入时的观测序号 (不是自然日)
    values, trades, closed = {}, 0, []

    rows = list(group.itertuples(index=False))
    for day_idx, row in enumerate(rows):
        price, pred = row.current_price, row.predicted_price
        if not (price and price > 0 and not np.isnan(pred)):
            values[row.date] = cash + units * (price if price > 0 else 0)
            continue

        exp_ret = (pred - price) / price
        held_long_enough = (units > 0 and (day_idx - buy_day_idx) >= HOLD_DAYS)

        if exp_ret >= buy_th and units == 0:
            units = cash * (1 - fee) / price
            buy_price = price
            buy_day_idx = day_idx
            cash = 0.0
            trades += 1
        elif exp_ret <= -sell_th and held_long_enough:
            cash = units * price * (1 - fee)
            closed.append(price * (1 - fee) - buy_price * (1 + fee))
            units = 0.0
        values[row.date] = cash + units * (price if price > 0 else 0)

    return values, trades, closed


# ============================================================
# 第 3 步: 组合回测 (方案 B: 单物品收益 cap +200%)
# ============================================================
def run_backtest(pred_df, capital=10_000.0, fee=0.0, buy_th=0.02, sell_th=0.02):
    """
    方案 B: 每件物品最终收益上限 +200% (即 3x)
    """
    # 方案 A: 统一日期网格
    aligned, common_dates = align_date_grid(pred_df)
    items = aligned["market_hash_name"].unique()
    n_items = len(items)
    budget = capital / n_items

    all_values, total_trades, all_closed = {}, 0, []
    item_finals = {}

    for name, group in aligned.groupby("market_hash_name"):
        group = group.sort_values("date")
        values, trades, closed = simulate_item(
            group, budget, fee, buy_th, sell_th
        )
        all_values[name] = values
        total_trades += trades
        all_closed += closed

        # 计算最终价值
        last_row = group.iloc[-1]
        final = values.get(last_row.date,
                           budget + 0 * last_row.current_price)  # fallback
        # Actually compute from the last value
        final = list(values.values())[-1] if values else budget

        # 方案 B: cap 单物品收益
        raw_ret = (final / budget - 1) * 100
        capped_ret = min(raw_ret, 200.0)
        capped_final = budget * (1 + capped_ret / 100)
        item_finals[name] = (raw_ret, capped_ret, capped_final)

    # 用 capped 值重新构建组合曲线
    # 方法: 每件物品的原始 values 按比例缩放到 capped 最终值
    capped_curves = {}
    for name, values in all_values.items():
        raw_final = list(values.values())[-1] if values else budget
        if raw_final <= budget:
            # 亏损或持平不缩放
            capped_curves[name] = values
        else:
            _, _, capped_final = item_finals[name]
            scale = (capped_final - budget) / (raw_final - budget) if raw_final > budget else 1.0
            capped_curves[name] = {
                d: budget + (v - budget) * scale for d, v in values.items()
            }

    # 对齐日期求和
    value_df = pd.DataFrame(capped_curves).sort_index().ffill().fillna(budget)
    curve = value_df.sum(axis=1)

    curve_df = pd.DataFrame({"date": curve.index, "capital": curve.values})

    # 指标
    peak = curve.cummax()
    n_capped = sum(1 for _, (raw, capped, _) in item_finals.items() if raw > capped)
    metrics = {
        "n_items": int(n_items),
        "n_common_dates": len(common_dates),
        "n_capped_items": n_capped,
        "returnPct": round((curve.iloc[-1] / capital - 1) * 100, 2),
        "maxDrawdownPct": round(((curve - peak) / peak).min() * 100, 2),
        "trades": int(total_trades),
        "winRate": round(float(np.mean([p > 0 for p in all_closed])) * 100, 1)
                   if all_closed else None,
    }
    return curve_df, metrics


# ============================================================
# 第 4 步: 买入持有基准
# ============================================================
def buy_hold_curve(pred_df, capital=10_000.0):
    """买入持有: 首日全买, 拿到最后 (统一日期网格)"""
    aligned, _ = align_date_grid(pred_df)
    items = aligned["market_hash_name"].unique()
    budget = capital / len(items)

    all_values = {}
    for name, group in aligned.groupby("market_hash_name"):
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
    parser = argparse.ArgumentParser(description="CSVest 回测引擎 V2 (方案D)")
    parser.add_argument("specs", nargs="+",
                        help="预测CSV, 形如 标签=路径")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fee", type=float, default=0.0)
    parser.add_argument("--buy-th", type=float, default=0.02)
    parser.add_argument("--sell-th", type=float, default=0.02)
    parser.add_argument("--outdir", default=str(OUTPUT_DIR))
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"回测引擎 V2 (方案 D: 统一日期 + cap 200% + 持满7天)")
    print(f"  资金 ${args.capital:,.0f} | 手续费 {args.fee:.1%} | "
          f"阈值 ±{args.buy_th:.0%} | 持有 ≥{HOLD_DAYS}天")
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
        win = f"{metrics['winRate']}%" if metrics.get("winRate") is not None else "—"
        print(f"  [{label:<10}] {metrics['n_items']:>3}件×{metrics['n_common_dates']}天 | "
              f"收益 {metrics['returnPct']:>+8.2f}% | "
              f"最大回撤 {metrics['maxDrawdownPct']:>6.2f}% | "
              f"交易 {metrics['trades']:>4}笔 | "
              f"胜率 {win} | "
              f"cap {metrics['n_capped_items']}件")

    # --- 买入持有基准 ---
    all_prices = (pd.concat(price_frames)
                    .drop_duplicates(["market_hash_name", "date"], keep="last"))
    bh_df = buy_hold_curve(all_prices, args.capital)
    curves["买入持有"] = bh_df
    bh_ret = round((bh_df["capital"].iloc[-1] / args.capital - 1) * 100, 2)
    results["买入持有"] = {"n_items": int(all_prices["market_hash_name"].nunique()),
                           "returnPct": bh_ret, "maxDrawdownPct": None,
                           "trades": None, "winRate": None}
    print(f"  [{'买入持有':<8}] {results['买入持有']['n_items']:>3}件 | "
          f"收益 {bh_ret:>+8.2f}%")

    # --- 前端 JSON ---
    all_dates = sorted(set().union(*[set(c["date"]) for c in curves.values()]))
    series = {}
    for label, cdf in curves.items():
        s = cdf.set_index("date")["capital"].reindex(all_dates).ffill()
        s = s.fillna(s.dropna().iloc[0])
        series[label] = (s / s.iloc[0] * 100).round(2).tolist()

    curves_json = {
        "dates": [f"{d.month}/{d.day}" for d in all_dates],
        "series": series,
    }
    with open(outdir / "backtest_curves.json", "w", encoding="utf-8") as f:
        json.dump(curves_json, f, ensure_ascii=False, indent=2)
    with open(outdir / "backtest_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n  ✅ {outdir / 'backtest_curves.json'}")
    print(f"  ✅ {outdir / 'backtest_results.json'}")
    print("\n" + "=" * 60)
    print("回测 V2 完成!")
    print("=" * 60)
    return curves, results


if __name__ == "__main__":
    curves, results = main()
