"""
回测引擎 V3: 逐日逐笔模拟交易
==============================================
修复 (7/19):
  1. 数据层清洗 — 剔除薄流动性尖峰 (日收益>50% 且 daily_volume<10)
  2. 统一日期网格 — ≥80% 物品共有日期 (去稀疏跳空)
  3. 固定持有期 — BUY 后必须持有 7 天才允许 SELL (对齐预测 horizon)
  4. 单品收益 cap +200% — 安全网 (清洗后应极少触发)

交易规则:
  预测涨幅 >= +2% → 买入 (若无持仓)
  预测跌幅 <= -2% → 卖出 (若持仓且已持满 7 天)
  ±2% 之间        → 不动

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
VAL_PATH = Path("../data/val.csv")
REQUIRED_COLS = ["date", "market_hash_name", "current_price", "predicted_price"]
HOLD_DAYS = 7
SPIKE_RET_THRESHOLD = 0.50   # 日收益率绝对值 >50%
SPIKE_VOL_THRESHOLD = 10     # 且挂单量 <10


# ============================================================
# 第 1 步: 读预测 CSV + 数据层清洗尖峰
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


def clean_spikes(pred_df, val_path=VAL_PATH):
    """
    数据层清洗: 用 val.csv 的 daily_volume 识别并剔除薄流动性尖峰。
    规则: 日收益率绝对值 >50% 且当天挂单量 <10 → 标记为脏数据。
    剔除预测 DataFrame 中对应的 (item, date) 行。
    返回: (清洗后 DataFrame, 脏物品数, 剔除行数, 详情列表)
    """
    if not Path(val_path).exists():
        print(f"  ⚠️ {val_path} 不存在, 跳过数据清洗")
        return pred_df, 0, 0, []

    val = pd.read_csv(val_path, parse_dates=["date"])
    dirty_pairs = set()
    spike_details = []

    for name, group in val.groupby("market_hash_name"):
        group = group.sort_values("date")
        prices = group["price"].values
        volumes = group["daily_volume"].values
        dates = group["date"].values
        for i in range(1, len(prices)):
            if prices[i-1] > 0 and volumes[i] > 0:  # 有量才判 (vol=0 可能是元旦停服)
                ret = (prices[i] - prices[i-1]) / prices[i-1]
                if abs(ret) > SPIKE_RET_THRESHOLD and volumes[i] < SPIKE_VOL_THRESHOLD:
                    dirty_pairs.add((name, pd.Timestamp(dates[i])))
                    spike_details.append({
                        "item": name,
                        "date": str(dates[i])[:10],
                        "price": round(float(prices[i]), 2),
                        "prev_price": round(float(prices[i-1]), 2),
                        "ret_pct": round(float(ret * 100), 1),
                        "volume": int(volumes[i]),
                    })

    if not dirty_pairs:
        return pred_df, 0, 0, []

    before = len(pred_df)
    pred_dates = set(
        (r.market_hash_name, r.date)
        for r in pred_df.itertuples(index=False)
    )
    removed = pred_dates & dirty_pairs
    mask = pred_df.apply(
        lambda row: (row["market_hash_name"], row["date"]) not in removed, axis=1
    )
    cleaned = pred_df[mask].copy()
    after = len(cleaned)
    n_items = len(set(p[0] for p in removed))

    return cleaned, n_items, before - after, spike_details


def align_date_grid(pred_df, min_pct=0.80):
    """
    只保留 ≥min_pct 物品都有数据的日期。缺失物品用 ffill 补齐。
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
# 第 2 步: 单物品模拟 (强制持满 7 天)
# ============================================================
def simulate_item(group, budget, fee, buy_th, sell_th):
    """
    group: 单物品预测 df (已排序, 日期网格已对齐, 尖峰已清洗)
    BUY 后必须持有 HOLD_DAYS 天才允许 SELL
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
# 第 3 步: 组合回测
# ============================================================
def run_backtest(pred_df, capital=10_000.0, fee=0.0, buy_th=0.02, sell_th=0.02):
    """
    1. 统一日期网格 (≥80% 物品)
    2. 逐物品模拟 (持满 7 天)
    3. 单品收益 cap +200% (安全网)
    """
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

        final = list(values.values())[-1] if values else budget

        # 安全网: cap 单物品收益 +200%
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
    print(f"回测引擎 V3 (数据清洗 + 日期对齐 + 持满7天 + cap安全网)")
    print(f"  资金 ${args.capital:,.0f} | 手续费 {args.fee:.1%} | "
          f"阈值 ±{args.buy_th:.0%} | 持有 ≥{HOLD_DAYS}天")
    print(f"  尖峰清洗: 日收益>{SPIKE_RET_THRESHOLD:.0%} & 挂单量<{SPIKE_VOL_THRESHOLD}")
    print("=" * 60)

    curves, results, price_frames = {}, {}, []

    # 先跑一次清洗看有多少尖峰 (只对第一个模型做详细汇报)
    first_clean_done = False

    for spec in args.specs:
        label, _, path = spec.rpartition("=")
        if not label:
            label = Path(path).stem.removeprefix("pred_").removeprefix("predictions_")
        pred_df = load_predictions(path)

        # 数据层清洗
        cleaned, n_dirty_items, n_removed, spike_details = clean_spikes(pred_df)
        if not first_clean_done:
            first_clean_done = True
            if n_removed > 0:
                print(f"\n  🧹 数据清洗: 剔除 {n_removed} 行 ({n_dirty_items} 件物品的尖峰)")
                for s in spike_details[:5]:
                    print(f"     {s['date']}  {s['item'][:45]}  "
                          f"${s['prev_price']:.0f}→${s['price']:.0f} ({s['ret_pct']:+.0f}%)  "
                          f"vol={s['volume']}")
                if len(spike_details) > 5:
                    print(f"     ... 共 {len(spike_details)} 个尖峰")
            else:
                print(f"\n  🧹 数据清洗: 未发现尖峰 (全部通过)")

        price_frames.append(cleaned[["date", "market_hash_name", "current_price"]])

        curve_df, metrics = run_backtest(
            cleaned, args.capital, args.fee, args.buy_th, args.sell_th
        )
        # 补记清洗统计
        metrics["n_spikes_removed"] = n_removed
        metrics["n_dirty_items"] = n_dirty_items

        curves[label] = curve_df
        results[label] = metrics

        csv_path = outdir / f"backtest_curve_{label}.csv"
        curve_df.to_csv(csv_path, index=False)
        win = f"{metrics['winRate']}%" if metrics.get("winRate") is not None else "—"
        cap_info = f"cap {metrics['n_capped_items']}件" if metrics['n_capped_items'] > 0 else ""
        print(f"  [{label:<10}] {metrics['n_items']:>3}件×{metrics['n_common_dates']}天 | "
              f"收益 {metrics['returnPct']:>+8.2f}% | "
              f"最大回撤 {metrics['maxDrawdownPct']:>6.2f}% | "
              f"交易 {metrics['trades']:>4}笔 | "
              f"胜率 {win} | "
              f"{cap_info}")

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
    print("回测 V3 完成!")
    print("=" * 60)
    return curves, results


if __name__ == "__main__":
    curves, results = main()
