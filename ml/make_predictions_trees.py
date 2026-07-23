"""Train tree regressors and export canonical seven-observation predictions."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from xgboost import XGBRegressor

from forecast_contract import PREDICTION_COLUMNS, validate_prediction_frame
from feature_engineering import fit_categoricals, transform_categoricals
from forecast_contract import load_feature_panel
from tree_features import FEATURE_COLS


sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_DIR = BASE_DIR / "models"
PRED_DIR = BASE_DIR / "preds"
MODEL_SPECS = {
    "rf": {
        "label": "RF",
        "filename": "rf",
        "model_file": "rf_reg.pkl",
        "factory": lambda: RandomForestRegressor(
            max_depth=12, n_estimators=100, random_state=42, n_jobs=-1
        ),
        "params": {"max_depth": 12, "n_estimators": 100},
    },
    "lightgbm": {
        "label": "LightGBM",
        "filename": "lightgbm",
        "model_file": "lightgbm_reg.pkl",
        "factory": lambda: LGBMRegressor(
            max_depth=6,
            n_estimators=200,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1,
            verbose=-1,
        ),
        "params": {"max_depth": 6, "n_estimators": 200, "learning_rate": 0.05},
    },
    "xgboost": {
        "label": "XGBoost",
        "filename": "xgboost",
        "model_file": "xgb_reg.pkl",
        "factory": lambda: XGBRegressor(
            max_depth=6,
            n_estimators=200,
            learning_rate=0.05,
            random_state=42,
            n_jobs=-1,
            tree_method="hist",
        ),
        "params": {"max_depth": 6, "n_estimators": 200, "learning_rate": 0.05},
    },
}


def select_fit_frame(splits: dict[str, pd.DataFrame], prediction_split: str) -> pd.DataFrame:
    """Use train for validation export and train+val for final test export."""
    if prediction_split == "val":
        return splits["train"].copy()
    if prediction_split == "test":
        return pd.concat([splits["train"], splits["val"]], ignore_index=True)
    raise ValueError("prediction_split must be val or test")


def build_prediction_frame(
    source: pd.DataFrame,
    predicted_log_prices,
    split: str,
    minimum_price: float,
) -> pd.DataFrame:
    """Build and validate one canonical tree-model prediction frame."""
    if len(source) != len(predicted_log_prices):
        raise ValueError("prediction count must match source rows")
    prediction = np.expm1(np.asarray(predicted_log_prices, dtype=float))
    prediction = np.maximum(prediction, minimum_price)
    output = pd.DataFrame({
        "split": split,
        "date": pd.to_datetime(source["date"]).to_numpy(),
        "target_date": pd.to_datetime(source["TargetDate"]).to_numpy(),
        "market_hash_name": source["market_hash_name"].to_numpy(),
        "current_price": source["price"].to_numpy(dtype=float),
        "actual_future_price": source["TargetPrice"].to_numpy(dtype=float),
        "predicted_price": prediction,
        "horizon_steps": 7,
    })
    return validate_prediction_frame(output[PREDICTION_COLUMNS])


def load_tree_splits() -> tuple[dict[str, pd.DataFrame], dict]:
    """Build one continuous feature panel and return valid target rows per split."""
    panel = load_feature_panel(DATA_DIR)
    encoders = fit_categoricals(panel.loc[panel["_split"] == "train"])
    panel = transform_categoricals(panel, encoders)
    splits = {}
    for split in ("train", "val", "test"):
        valid = panel[
            (panel["_split"] == split)
            & (panel["_target_split"] == split)
            & panel["Target"].notna()
        ].sort_values(["date", "market_hash_name"])
        # test.csv 存在同物品同日多行（历史清洗残留），预测契约要求唯一键
        before = len(valid)
        valid = valid.drop_duplicates(subset=["market_hash_name", "date"], keep="last")
        if len(valid) != before:
            print(f"  dedupe {split}: {before} -> {len(valid)} rows", flush=True)
        splits[split] = valid.reset_index(drop=True)
    return splits, encoders


def regression_metrics(frame: pd.DataFrame) -> dict:
    truth = frame["actual_future_price"].to_numpy(dtype=float)
    prediction = frame["predicted_price"].to_numpy(dtype=float)
    return {
        "rmse": float(np.sqrt(mean_squared_error(truth, prediction))),
        "mae": float(mean_absolute_error(truth, prediction)),
        "mape": float(np.mean(np.abs((truth - prediction) / np.maximum(truth, 0.01))) * 100),
        "r2": float(r2_score(truth, prediction)),
        "rows": int(len(frame)),
        "items": int(frame["market_hash_name"].nunique()),
    }


def build_model_bundle(model, label, params, encoders, minimum_price):
    """Package a fitted tree model with all preprocessing needed for inference."""
    return {
        "model": model,
        "name": label,
        "params": params,
        "feature_cols": FEATURE_COLS,
        "encoders": encoders,
        "categorical_encoding_fit_split": "train",
        "fit_split": "train+val",
        "predict_split": "test",
        "horizon_steps": 7,
        "minimum_price": minimum_price,
    }


def train_and_export(
    model_key: str,
    prediction_split: str,
    splits: dict[str, pd.DataFrame],
    encoders: dict,
    minimum_price: float,
) -> dict:
    spec = MODEL_SPECS[model_key]
    fit_frame = select_fit_frame(splits, prediction_split)
    prediction_source = splits[prediction_split]
    x_fit = fit_frame[FEATURE_COLS].to_numpy(dtype=np.float32)
    y_fit = fit_frame["Target"].to_numpy(dtype=np.float32)
    x_prediction = prediction_source[FEATURE_COLS].to_numpy(dtype=np.float32)

    print(
        f"[{spec['label']}] fit={'train' if prediction_split == 'val' else 'train+val'} "
        f"rows={len(fit_frame):,}; predict={prediction_split} rows={len(prediction_source):,}",
        flush=True,
    )
    started = time.time()
    model = spec["factory"]()
    model.fit(x_fit, y_fit)
    predicted_log = model.predict(x_prediction)
    output = build_prediction_frame(
        prediction_source, predicted_log, prediction_split, minimum_price
    )
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PRED_DIR / f"pred_{spec['filename']}_{prediction_split}.csv"
    output.to_csv(output_path, index=False, date_format="%Y-%m-%d")

    elapsed = time.time() - started
    metrics = regression_metrics(output)
    print(
        f"  saved {output_path.name}: RMSE={metrics['rmse']:.4f}, "
        f"MAE={metrics['mae']:.4f}, MAPE={metrics['mape']:.2f}%, "
        f"R2={metrics['r2']:.4f}, {elapsed:.1f}s",
        flush=True,
    )

    if prediction_split == "test":
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        bundle = build_model_bundle(
            model, spec["label"], spec["params"], encoders, minimum_price
        )
        joblib.dump(bundle, MODEL_DIR / spec["model_file"])
    return metrics


def parse_models(raw: str) -> list[str]:
    aliases = {"rf": "rf", "lightgbm": "lightgbm", "lgb": "lightgbm", "xgboost": "xgboost", "xgb": "xgboost"}
    models = []
    for token in raw.split(","):
        key = token.strip().lower()
        if key not in aliases:
            raise ValueError(f"unknown tree model: {token}")
        canonical = aliases[key]
        if canonical not in models:
            models.append(canonical)
    return models


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test", "both"), default="both")
    parser.add_argument("--models", default="rf,lightgbm,xgboost")
    args = parser.parse_args()

    models = parse_models(args.models)
    prediction_splits = ("val", "test") if args.split == "both" else (args.split,)
    print("Loading one canonical continuous feature panel...", flush=True)
    splits, encoders = load_tree_splits()
    minimum_price = float(splits["train"]["price"].min())
    print(
        "Rows: " + ", ".join(f"{name}={len(frame):,}" for name, frame in splits.items())
        + f"; train price floor=${minimum_price:.2f}",
        flush=True,
    )

    summary = {"horizon_steps": 7, "models": {}}
    for prediction_split in prediction_splits:
        summary["models"][prediction_split] = {}
        for model_key in models:
            summary["models"][prediction_split][MODEL_SPECS[model_key]["label"]] = train_and_export(
                model_key, prediction_split, splits, encoders, minimum_price
            )

    output_path = BASE_DIR / "outputs" / "tree_prediction_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved {output_path}", flush=True)


if __name__ == "__main__":
    main()
