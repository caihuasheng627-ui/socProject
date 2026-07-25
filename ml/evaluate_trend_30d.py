"""Evaluate canonical 30-day P10/P50/P90 trend predictions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score


CHECKPOINT_DAYS = (7, 14, 21, 30)
OUTPUT_DIR = Path(__file__).resolve().parent / "outputs"


def _metric_block(y_true: np.ndarray, prediction: np.ndarray) -> dict[str, float]:
    p10, p50, p90 = (prediction[..., index] for index in range(3))
    denominator = np.maximum(y_true, 0.01)
    spread_denominator = np.maximum(p50, 0.01)
    truth_flat = y_true.ravel()
    p50_flat = p50.ravel()
    r2 = float(r2_score(truth_flat, p50_flat)) if np.ptp(truth_flat) > 0 else 0.0
    return {
        "rmse": float(np.sqrt(mean_squared_error(truth_flat, p50_flat))),
        "mae": float(mean_absolute_error(truth_flat, p50_flat)),
        "mape_pct": float(np.mean(np.abs((y_true - p50) / denominator)) * 100.0),
        "r2": r2,
        "coverage": float(((y_true >= p10) & (y_true <= p90)).mean()),
        "avg_spread_pct": float(np.mean((p90 - p10) / spread_denominator) * 100.0),
        "crossing_rate": float(((p10 > p50) | (p50 > p90)).mean()),
    }


def evaluate_trend_arrays(y_true, prediction) -> dict:
    """Compute P50 accuracy and interval diagnostics overall and at key horizons."""
    y_true = np.asarray(y_true, dtype=float)
    prediction = np.asarray(prediction, dtype=float)
    if y_true.ndim != 2 or y_true.shape[1] != 30:
        raise ValueError(f"truth must have shape (n, 30), got {y_true.shape}")
    if prediction.shape != (*y_true.shape, 3):
        raise ValueError(
            f"prediction must have shape {(*y_true.shape, 3)}, got {prediction.shape}"
        )
    if not np.isfinite(y_true).all() or not np.isfinite(prediction).all():
        raise ValueError("trend arrays must contain only finite values")
    if (y_true <= 0).any():
        raise ValueError("trend truth prices must be positive")

    return {
        "rows": int(len(y_true)),
        "horizon_steps": 30,
        "overall": _metric_block(y_true, prediction),
        "horizons": {
            f"d{day}": _metric_block(
                y_true[:, day - 1], prediction[:, day - 1, :]
            )
            for day in CHECKPOINT_DAYS
        },
    }


def evaluate_prediction_frame(frame: pd.DataFrame) -> dict:
    truth = frame[[f"actual_future_price_d{day}" for day in range(1, 31)]].to_numpy()
    prediction = np.stack(
        [
            frame[[f"trend_{quantile}_d{day}" for quantile in ("p10", "p50", "p90")]].to_numpy()
            for day in range(1, 31)
        ],
        axis=1,
    )
    result = evaluate_trend_arrays(truth, prediction)
    result["split"] = str(frame["split"].iloc[0])
    result["items"] = int(frame["market_hash_name"].nunique())
    return result


def save_split_metrics(metrics: dict, split: str, *, output_dir: Path = OUTPUT_DIR) -> Path:
    if split not in {"val", "test"}:
        raise ValueError("split must be val or test")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"trend_30d_results_{split}.json"
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prediction_csv", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    frame = pd.read_csv(args.prediction_csv)
    result = evaluate_prediction_frame(frame)
    output = args.output or save_split_metrics(result, result["split"])
    if args.output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"saved {output}")


if __name__ == "__main__":
    main()
