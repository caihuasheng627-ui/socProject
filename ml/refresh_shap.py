"""Refresh frontend SHAP data from current held-out tree models."""

import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from model_features import TREE_FEATURE_COLS
from tree_features import assert_held_out, load_tree_split, regression_arrays


BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
OUTPUT_PATH = BASE_DIR / "outputs" / "shap_features.json"
MODEL_PATHS = {
    "xgboost": MODEL_DIR / "xgb_reg.pkl",
    "lightgbm": MODEL_DIR / "lightgbm_reg.pkl",
}


def normalized_features(mean_abs_shap: np.ndarray) -> list[dict]:
    values = np.maximum(np.asarray(mean_abs_shap, dtype=float), 0.0)
    total = float(values.sum())
    if values.shape != (len(TREE_FEATURE_COLS),) or not np.isfinite(values).all() or total <= 0:
        raise ValueError("invalid SHAP importance vector")
    rows = [
        {"feature": feature, "importance": round(float(value / total), 8)}
        for feature, value in zip(TREE_FEATURE_COLS, values)
    ]
    return sorted(rows, key=lambda row: row["importance"], reverse=True)


def explain_model(bundle: dict, sample: np.ndarray, split: str) -> dict:
    fit_split = bundle.get("fit_split", "unknown")
    assert_held_out(split, fit_split)
    import shap

    raw = np.asarray(shap.TreeExplainer(bundle["model"]).shap_values(sample))
    if raw.ndim != 2 or raw.shape[1] != len(TREE_FEATURE_COLS):
        raise ValueError(f"unexpected SHAP shape: {raw.shape}")
    return {
        "modelFitSplit": fit_split,
        "explanationSplit": split,
        "samples": int(raw.shape[0]),
        "features": normalized_features(np.abs(raw).mean(axis=0)),
    }


def refresh(split: str = "test", sample_size: int = 2000, output: Path = OUTPUT_PATH) -> dict:
    frame = load_tree_split(BASE_DIR / "data", split)
    values, *_ = regression_arrays(frame)
    rng = np.random.default_rng(42)
    selected = rng.choice(len(values), min(sample_size, len(values)), replace=False)
    sample = values[selected]

    models = {
        name: explain_model(joblib.load(path), sample, split)
        for name, path in MODEL_PATHS.items()
    }
    average = []
    by_model = {
        name: {row["feature"]: row["importance"] for row in block["features"]}
        for name, block in models.items()
    }
    for feature in TREE_FEATURE_COLS:
        average.append({
            "feature": feature,
            "importance": round(sum(rows[feature] for rows in by_model.values()) / len(by_model), 8),
        })
    average.sort(key=lambda row: row["importance"], reverse=True)
    models["average"] = {
        "modelFitSplit": "train+val",
        "explanationSplit": split,
        "samples": int(len(sample)),
        "features": average,
    }
    payload = {
        "contractVersion": "tree-shap-v2",
        "method": "mean-absolute-shap",
        "models": models,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output)
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), default="test")
    parser.add_argument("--sample-size", type=int, default=2000)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    args = parser.parse_args()
    result = refresh(args.split, args.sample_size, args.output)
    print(f"wrote {args.output} ({', '.join(result['models'])})")
