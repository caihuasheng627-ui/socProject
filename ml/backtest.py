"""Forward-only backtest for canonical seven-observation predictions."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from forecast_contract import validate_prediction_frame


sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "outputs" / "backtest"
HOLD_STEPS = 7


def load_prediction(path):
    return validate_prediction_frame(pd.read_csv(path), path)


def align_common_prediction_frames(frames):
    """Restrict every model to the same item/decision-date observations."""
    if not frames:
        raise ValueError("at least one prediction frame is required")
    normalized = {
        name: validate_prediction_frame(frame, name) for name, frame in frames.items()
    }
    splits = {frame["split"].iloc[0] for frame in normalized.values()}
    if len(splits) != 1:
        raise ValueError(f"all backtest inputs must share one split, got {sorted(splits)}")

    common_keys = None
    for frame in normalized.values():
        keys = set(zip(frame["market_hash_name"], frame["date"]))
        common_keys = keys if common_keys is None else common_keys & keys
    if not common_keys:
        raise ValueError("prediction frames have no common item/date observations")

    aligned = {}
    for name, frame in normalized.items():
        mask = [
            (item, date) in common_keys
            for item, date in zip(frame["market_hash_name"], frame["date"])
        ]
        aligned[name] = frame.loc[mask].sort_values(
            ["date", "market_hash_name"]
        ).reset_index(drop=True)

    contract_columns = [
        "split",
        "date",
        "target_date",
        "market_hash_name",
        "current_price",
        "actual_future_price",
        "horizon_steps",
    ]
    reference_name, reference = next(iter(aligned.items()))
    for name, frame in aligned.items():
        if not frame[contract_columns].equals(reference[contract_columns]):
            raise ValueError(
                f"{name} contract values differ from {reference_name} on common prediction rows"
            )
    return aligned


def simulate_item(group, budget, fee, buy_th, sell_th):
    cash = float(budget)
    units = 0.0
    buy_step = None
    entry_value = None
    values = {}
    closed_pnl = []
    buy_count = 0
    sell_count = 0

    for observation, row in enumerate(group.sort_values("date").itertuples(index=False)):
        price = float(row.current_price)
        expected_return = (float(row.predicted_price) - price) / price
        held_steps = observation - buy_step if buy_step is not None else 0

        if units == 0 and expected_return >= buy_th:
            entry_value = cash
            units = cash * (1 - fee) / price
            cash = 0.0
            buy_step = observation
            buy_count += 1
        elif units > 0 and held_steps >= HOLD_STEPS and expected_return <= -sell_th:
            cash = units * price * (1 - fee)
            closed_pnl.append(cash - entry_value)
            units = 0.0
            buy_step = None
            entry_value = None
            sell_count += 1

        values[pd.Timestamp(row.date)] = cash + units * price

    return {
        "values": values,
        "closed_pnl": closed_pnl,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "open_position": int(units > 0),
    }


def run_backtest(pred_df, capital=10_000.0, fee=0.0, buy_th=0.02, sell_th=0.02):
    frame = validate_prediction_frame(pred_df)
    items = sorted(frame["market_hash_name"].unique())
    budget = capital / len(items)

    curves = {}
    closed_pnl = []
    buy_count = sell_count = open_positions = 0
    for item, group in frame.groupby("market_hash_name"):
        result = simulate_item(group, budget, fee, buy_th, sell_th)
        curves[item] = result["values"]
        closed_pnl.extend(result["closed_pnl"])
        buy_count += result["buy_count"]
        sell_count += result["sell_count"]
        open_positions += result["open_position"]

    value_frame = pd.DataFrame(curves).sort_index().ffill().fillna(budget)
    equity = value_frame.sum(axis=1)
    peak = equity.cummax()
    curve = pd.DataFrame({"date": equity.index, "capital": equity.to_numpy()})
    metrics = {
        "split": frame["split"].iloc[0],
        "horizon_steps": 7,
        "fee": float(fee),
        "n_items": int(len(items)),
        "n_dates": int(frame["date"].nunique()),
        "start_date": str(frame["date"].min().date()),
        "end_date": str(frame["date"].max().date()),
        "returnPct": round(float((equity.iloc[-1] / capital - 1) * 100), 2),
        "maxDrawdownPct": round(float(((equity - peak) / peak).min() * 100), 2),
        "buy_count": int(buy_count),
        "sell_count": int(sell_count),
        "trades": int(buy_count + sell_count),
        "closed_positions": int(len(closed_pnl)),
        "open_positions": int(open_positions),
        "winRate": round(float(np.mean(np.asarray(closed_pnl) > 0) * 100), 1)
        if closed_pnl
        else None,
    }
    return curve, metrics


def buy_hold(frame, capital):
    items = sorted(frame["market_hash_name"].unique())
    budget = capital / len(items)
    curves = {}
    for item, group in frame.groupby("market_hash_name"):
        group = group.sort_values("date")
        units = budget / float(group["current_price"].iloc[0])
        curves[item] = {
            row.date: units * row.current_price for row in group.itertuples(index=False)
        }
    equity = pd.DataFrame(curves).sort_index().ffill().fillna(budget).sum(axis=1)
    return pd.DataFrame({"date": equity.index, "capital": equity.to_numpy()})


def run_models(frames, capital=10_000.0, fees=(0.0,)):
    aligned = align_common_prediction_frames(frames)
    results = {}
    curves = {}
    for fee in fees:
        scenario = f"fee_{fee:.4f}"
        results[scenario] = {}
        curves[scenario] = {}
        for model, frame in aligned.items():
            curve, metrics = run_backtest(frame, capital=capital, fee=fee)
            results[scenario][model] = metrics
            curves[scenario][model] = curve
    first_frame = next(iter(aligned.values()))
    curves["buy_hold"] = buy_hold(first_frame, capital)
    return curves, results


def main(specs, capital=10_000.0, fees=(0.0, 0.025)):
    frames = {}
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"prediction spec must be label=path, got {spec}")
        label, path = spec.split("=", 1)
        frames[label] = load_prediction(path)
    curves, results = run_models(frames, capital=capital, fees=fees)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "backtest_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    curve_payload = {}
    for scenario, scenario_curves in curves.items():
        if scenario == "buy_hold":
            curve_payload[scenario] = scenario_curves.assign(
                date=lambda value: value["date"].dt.strftime("%Y-%m-%d")
            ).to_dict("records")
        else:
            curve_payload[scenario] = {
                model: curve.assign(date=curve["date"].dt.strftime("%Y-%m-%d")).to_dict("records")
                for model, curve in scenario_curves.items()
            }
    (OUT_DIR / "backtest_curves.json").write_text(
        json.dumps(curve_payload, ensure_ascii=False), encoding="utf-8"
    )
    return curves, results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("specs", nargs="+")
    parser.add_argument("--capital", type=float, default=10_000.0)
    parser.add_argument("--fees", type=float, nargs="+", default=[0.0, 0.025])
    args = parser.parse_args()
    main(args.specs, args.capital, tuple(args.fees))
