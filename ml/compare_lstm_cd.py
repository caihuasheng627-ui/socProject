"""Select the Hybrid C/D route using canonical validation predictions only."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from forecast_contract import route_price_group, validate_prediction_frame_seq


sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_DIR = BASE_DIR / "models"
PRED_DIR = BASE_DIR / "preds"
GROUP_NAMES = ["low", "mid", "high"]


def _resolve_pred_col(frame, suffix):
    """Resolve predicted_price column — works with old and new formats."""
    if f"predicted_price_d7{suffix}" in frame.columns:
        return f"predicted_price_d7{suffix}"
    if f"predicted_price{suffix}" in frame.columns:
        return f"predicted_price{suffix}"
    raise KeyError(f"frame has no predicted_price column (suffix={suffix})")


def metrics(truth, prediction):
    truth = np.asarray(truth, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    return {
        "n": int(len(truth)),
        "mae": float(mean_absolute_error(truth, prediction)),
        "rmse": float(np.sqrt(mean_squared_error(truth, prediction))),
        "mape": float(np.mean(np.abs((truth - prediction) / np.maximum(truth, 0.01))) * 100),
        "r2": float(r2_score(truth, prediction)),
    }


def load_aligned_val_predictions():
    c_path = PRED_DIR / "pred_lstm_c_val.csv"
    d_path = PRED_DIR / "pred_lstm_d_val.csv"
    c_raw = pd.read_csv(c_path)
    d_raw = pd.read_csv(d_path)
    c_frame = validate_prediction_frame_seq(c_raw, c_path)
    d_frame = validate_prediction_frame_seq(d_raw, d_path)
    if c_frame["split"].iloc[0] != "val" or d_frame["split"].iloc[0] != "val":
        raise ValueError("Hybrid route selection may only use split=val predictions")

    # Resolve column names
    c_pred_col = _resolve_pred_col(c_frame, "")
    d_pred_col = _resolve_pred_col(d_frame, "")
    # Rename to canonical names for merge
    c_frame = c_frame.rename(columns={c_pred_col: "predicted_price"})
    d_frame = d_frame.rename(columns={d_pred_col: "predicted_price"})

    keys = [
        "split", "date", "target_date", "market_hash_name",
        "current_price", "actual_future_price", "horizon_steps",
    ]
    merged = c_frame.merge(
        d_frame,
        on=keys,
        how="inner",
        suffixes=("_c", "_d"),
        validate="one_to_one",
    )
    if len(merged) != len(c_frame) or len(merged) != len(d_frame):
        raise ValueError("C and D validation predictions do not cover identical rows")
    return merged


def main():
    import pickle

    frame = load_aligned_val_predictions()
    with open(MODEL_DIR / "lstm_d_group_map.pkl", "rb") as handle:
        group_map = pickle.load(handle)

    frame["group"] = [
        route_price_group(
            row.market_hash_name,
            row.current_price,
            group_map["item_group"],
            tuple(group_map["boundaries"]),
        )
        for row in frame.itertuples(index=False)
    ]

    results = {}
    route = {}
    for group in GROUP_NAMES:
        subset = frame[frame["group"] == group]
        c_metrics = metrics(subset["actual_future_price"], subset["predicted_price_c"])
        d_metrics = metrics(subset["actual_future_price"], subset["predicted_price_d"])
        results[group] = {"LSTM-C": c_metrics, "LSTM-D": d_metrics}
        c_score = (c_metrics["mae"], c_metrics["rmse"])
        d_score = (d_metrics["mae"], d_metrics["rmse"])
        route[group] = "LSTM-C" if c_score <= d_score else "LSTM-D"

    results["ALL"] = {
        "LSTM-C": metrics(frame["actual_future_price"], frame["predicted_price_c"]),
        "LSTM-D": metrics(frame["actual_future_price"], frame["predicted_price_d"]),
    }
    payload = {
        "selection_split": "val",
        "horizon_steps": 7,
        "primary_metric": "mae",
        "tiebreaker": "rmse",
        "route": route,
        "group_results": results,
    }
    (MODEL_DIR / "lstm_cd_group_comparison.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (MODEL_DIR / "lstm_hybrid_route.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("Hybrid route selected on validation only:")
    for group in GROUP_NAMES:
        c = results[group]["LSTM-C"]
        d = results[group]["LSTM-D"]
        print(
            f"  {group:<4}: {route[group]} | "
            f"C MAE={c['mae']:.4f}, D MAE={d['mae']:.4f}"
        )
    return payload


if __name__ == "__main__":
    main()
