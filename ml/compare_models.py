"""Compare prediction files that share the canonical forecast contract."""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, mean_absolute_error, mean_squared_error, r2_score

from forecast_contract import validate_prediction_frame


sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
PRED_DIR = BASE_DIR / "preds"
OUT_DIR = BASE_DIR / "outputs"

MODEL_FILES = {
    "LSTM-C": "pred_lstm_c_{split}.csv",
    "LSTM-D": "pred_lstm_d_{split}.csv",
    "Hybrid": "pred_lstm_hybrid_{split}.csv",
    "RF": "pred_rf_{split}.csv",
    "LightGBM": "pred_lightgbm_{split}.csv",
    "XGBoost": "pred_xgboost_{split}.csv",
}


def comparison_coverage(frames):
    missing = [model for model in MODEL_FILES if model not in frames]
    return {
        "status": "partial" if missing else "complete",
        "missing_models": missing,
    }


def reg_metrics(y_true, y_pred):
    truth = np.asarray(y_true, dtype=float)
    prediction = np.asarray(y_pred, dtype=float)
    return {
        "rmse": round(float(np.sqrt(mean_squared_error(truth, prediction))), 4),
        "mae": round(float(mean_absolute_error(truth, prediction)), 4),
        "mape": round(float(np.mean(np.abs((truth - prediction) / np.maximum(truth, 0.01))) * 100), 2),
        "r2": round(float(r2_score(truth, prediction)), 4),
    }


def direction_metrics(frame):
    actual = (frame["actual_future_price"] > frame["current_price"]).astype(int)
    predicted = (frame["predicted_price"] > frame["current_price"]).astype(int)
    tp = int(((actual == 1) & (predicted == 1)).sum())
    fp = int(((actual == 0) & (predicted == 1)).sum())
    fn = int(((actual == 1) & (predicted == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {
        "accuracy": round(float(accuracy_score(actual, predicted)), 4),
        "precision": round(precision, 4) if precision is not None else None,
        "recall": round(recall, 4) if recall is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "auc": None,
        "n": int(len(frame)),
    }


def load_available_predictions(split):
    frames = {}
    for model, pattern in MODEL_FILES.items():
        path = PRED_DIR / pattern.format(split=split)
        if path.exists():
            frame = validate_prediction_frame(pd.read_csv(path), path)
            actual_split = frame["split"].iloc[0]
            if actual_split != split:
                raise ValueError(f"{path}: expected split={split}, got {actual_split}")
            frames[model] = frame
    return frames


def align_common_prediction_frames(frames):
    """Restrict ranked models to identical item/decision-date observations."""
    if not frames:
        raise ValueError("at least one prediction frame is required")

    normalized = {
        name: validate_prediction_frame(frame, name) for name, frame in frames.items()
    }
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
            ["market_hash_name", "date"]
        ).reset_index(drop=True)

    # 按共同 key 对齐后，丢掉真值/现价不一致的脏行（同名不同价）
    reference_name, reference = next(iter(aligned.items()))
    keep = np.ones(len(reference), dtype=bool)
    for name, frame in aligned.items():
        if name == reference_name:
            continue
        if len(frame) != len(reference):
            raise ValueError(f"{name}: aligned length mismatch vs {reference_name}")
        keep &= (
            np.isclose(
                frame["current_price"].to_numpy(dtype=float),
                reference["current_price"].to_numpy(dtype=float),
                rtol=0, atol=1e-6, equal_nan=False,
            )
            & np.isclose(
                frame["actual_future_price"].to_numpy(dtype=float),
                reference["actual_future_price"].to_numpy(dtype=float),
                rtol=0, atol=1e-6, equal_nan=False,
            )
            & (
                pd.to_datetime(frame["target_date"]).to_numpy()
                == pd.to_datetime(reference["target_date"]).to_numpy()
            )
        )
    if not keep.any():
        raise ValueError("no common rows with matching contract truth values")
    dropped = int((~keep).sum())
    if dropped:
        print(f"  dropped {dropped} mismatched-truth rows for fair compare", flush=True)
    for name, frame in list(aligned.items()):
        aligned[name] = frame.loc[keep].sort_values(
            ["market_hash_name", "date"]
        ).reset_index(drop=True)
    return aligned


def _get_predicted_col(frame):
    """Resolve the predicted_price column — works with both old and new formats."""
    if "predicted_price" in frame.columns:
        return "predicted_price"
    if "predicted_price_d7" in frame.columns:
        return "predicted_price_d7"
    raise KeyError("frame has neither 'predicted_price' nor 'predicted_price_d7'")


def evaluate_frames(frames):
    frames = align_common_prediction_frames(frames)
    results = {}
    for model, frame in frames.items():
        pred_col = _get_predicted_col(frame)
        metrics = reg_metrics(frame["actual_future_price"], frame[pred_col])
        # Direction metrics use the predicted_price column
        dir_frame = frame.copy()
        if pred_col != "predicted_price":
            dir_frame["predicted_price"] = dir_frame[pred_col]
        metrics.update({
            "items": int(frame["market_hash_name"].nunique()),
            "rows": int(len(frame)),
            "direction": direction_metrics(dir_frame),
        })
        results[model] = metrics
    return results


def print_results(results, split):
    print(f"\nCSVest regression comparison: split={split}, horizon=7 observations")
    print(f"{'Model':<12} {'Items':>6} {'Rows':>8} {'RMSE':>10} {'MAE':>10} {'MAPE':>9} {'R2':>9}")
    for model, metrics in results.items():
        print(
            f"{model:<12} {metrics['items']:>6} {metrics['rows']:>8} "
            f"{metrics['rmse']:>10.4f} {metrics['mae']:>10.4f} "
            f"{metrics['mape']:>8.2f}% {metrics['r2']:>9.4f}"
        )


def main(split="test"):
    frames = load_available_predictions(split)
    if not frames:
        raise FileNotFoundError(f"No canonical {split} prediction files found in {PRED_DIR}")
    results = evaluate_frames(frames)
    print_results(results, split)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    coverage = comparison_coverage(frames)
    payload = {
        "split": split,
        "horizon_steps": 7,
        **coverage,
        "models": results,
    }
    if coverage["status"] == "partial":
        print(f"WARNING: partial comparison; missing {', '.join(coverage['missing_models'])}")
    output = OUT_DIR / f"compare_results_{split}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if split == "test":
        (OUT_DIR / "compare_results.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"saved {output}")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), default="test")
    args = parser.parse_args()
    main(args.split)
