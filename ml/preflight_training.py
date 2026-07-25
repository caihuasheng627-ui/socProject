"""Fail-fast dataset and artifact checks before expensive model training."""

from __future__ import annotations

import argparse
import gc
import json
import pickle
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from model_features import assert_volume_free_feature_contract
from model_features import FROZEN_GRU_ITEMS, SEQUENCE_FEATURE_COLS, TREE_FEATURE_COLS


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
REPORT_PATH = BASE_DIR / "outputs" / "volume_free_dataset_report.json"
SPLITS = ("train", "val", "test")
KERAS_SMOKE_MODELS = ("lstm_c", "lstm_d", "gru", "trend_30d")
TREE_SMOKE_MODELS = (
    "rf_reg", "lightgbm_reg", "xgboost_reg",
    "rf_cls", "lightgbm_cls", "xgboost_cls",
)


def audit_split_frames(frames: dict[str, pd.DataFrame]) -> dict:
    """Validate unique, per-item chronological train/val/test source rows."""
    missing = [split for split in SPLITS if split not in frames]
    if missing:
        raise ValueError(f"missing dataset splits: {missing}")

    normalized = []
    split_report = {}
    for split in SPLITS:
        frame = frames[split].copy()
        required = {"market_hash_name", "date"}
        absent = sorted(required - set(frame.columns))
        if absent:
            raise ValueError(f"{split}: missing columns {absent}")
        frame["date"] = pd.to_datetime(frame["date"], errors="raise")
        if frame.duplicated(["market_hash_name", "date"]).any():
            raise ValueError(f"{split}: duplicate item/date rows")
        frame["_split"] = split
        normalized.append(frame[["market_hash_name", "date", "_split"]])
        split_report[split] = {
            "rows": int(len(frame)),
            "items": int(frame["market_hash_name"].nunique()),
            "date_min": frame["date"].min().date().isoformat(),
            "date_max": frame["date"].max().date().isoformat(),
        }

    combined = pd.concat(normalized, ignore_index=True)
    if combined.duplicated(["market_hash_name", "date"]).any():
        raise ValueError("item/date overlap exists across dataset splits")

    order = {split: index for index, split in enumerate(SPLITS)}
    chronological_violations = []
    for item, item_frame in combined.groupby("market_hash_name", sort=False):
        spans = {
            split: (part["date"].min(), part["date"].max())
            for split, part in item_frame.groupby("_split")
        }
        present = [split for split in SPLITS if split in spans]
        for left, right in zip(present, present[1:]):
            if order[left] >= order[right] or spans[left][1] >= spans[right][0]:
                chronological_violations.append(str(item))
                break
    if chronological_violations:
        preview = ", ".join(chronological_violations[:5])
        raise ValueError(f"per-item split chronology violation: {preview}")

    return {
        "status": "passed",
        "items": int(combined["market_hash_name"].nunique()),
        "duplicate_item_dates": 0,
        "chronological_violations": 0,
        "splits": split_report,
    }


def validate_feature_matrix(values, feature_columns, label: str) -> dict:
    """Validate dimension, finiteness, and the volume-free feature contract."""
    columns = assert_volume_free_feature_contract(feature_columns)
    matrix = np.asarray(values)
    if matrix.ndim < 2 or matrix.shape[-1] != len(columns):
        raise ValueError(
            f"{label}: expected final dimension {len(columns)}, got {matrix.shape}"
        )
    if not np.isfinite(matrix).all():
        raise ValueError(f"{label}: feature matrix must be finite")
    return {"shape": [int(value) for value in matrix.shape], "features": list(columns)}


def validate_smoke_report(report: dict) -> dict:
    """Require every deployable architecture and both CSV contracts to pass."""
    missing = []
    for name in KERAS_SMOKE_MODELS:
        if report.get("keras", {}).get(name, {}).get("status") != "passed":
            missing.append(name)
    for name in TREE_SMOKE_MODELS:
        if report.get("trees", {}).get(name, {}).get("status") != "passed":
            missing.append(name)
    for name in ("forecast_7d", "trend_30d"):
        if report.get("csv", {}).get(name) != "passed":
            missing.append(name)
    if missing:
        raise ValueError("training smoke checks missing or failed: " + ", ".join(missing))
    return {**report, "status": "passed"}


def load_raw_splits(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    return {
        split: pd.read_csv(data_dir / f"{split}.csv", parse_dates=["date"])
        for split in SPLITS
    }


def audit_feature_panel(data_dir: Path = DATA_DIR) -> tuple[dict, pd.DataFrame]:
    """Build the real panel once and validate both model feature matrices."""
    from feature_engineering import fit_categoricals, transform_categoricals
    from forecast_contract import load_feature_panel

    panel = load_feature_panel(data_dir)
    encoders = fit_categoricals(panel.loc[panel["_split"] == "train"])
    encoded = transform_categoricals(panel, encoders)
    sequence = validate_feature_matrix(
        encoded[list(SEQUENCE_FEATURE_COLS)].to_numpy(dtype=float),
        SEQUENCE_FEATURE_COLS,
        "sequence",
    )
    tree = validate_feature_matrix(
        encoded[list(TREE_FEATURE_COLS)].to_numpy(dtype=float),
        TREE_FEATURE_COLS,
        "tree",
    )
    train_items = set(encoded.loc[encoded["_split"] == "train", "market_hash_name"])
    missing_gru = sorted(set(FROZEN_GRU_ITEMS) - train_items)
    if missing_gru:
        raise ValueError("frozen GRU items missing from train: " + ", ".join(missing_gru))
    return {
        "rows": int(len(encoded)),
        "sequence": sequence,
        "tree": tree,
        "frozen_gru_items": list(FROZEN_GRU_ITEMS),
        "missing_gru_items": [],
    }, encoded


def _real_smoke_windows(panel: pd.DataFrame, sample_limit: int):
    from forecast_contract import add_grouped_targets_multi, build_sequence_windows_multi

    if sample_limit < 4:
        raise ValueError("sample_limit must be at least 4")
    trend_panel = add_grouped_targets_multi(panel, horizon_steps=30)
    counts = trend_panel.groupby(["market_hash_name", "_split"]).size().unstack(fill_value=0)
    candidates = counts.index[
        (counts.get("train", 0) >= 90) & (counts.get("val", 0) >= 90)
    ]
    if len(candidates) == 0:
        raise ValueError("no item has enough train and val history for smoke windows")
    subset = trend_panel[trend_panel["market_hash_name"].isin(candidates[:2])].copy()
    outputs = []
    for split in ("train", "val"):
        x_values, y_values, metadata = build_sequence_windows_multi(
            subset,
            list(SEQUENCE_FEATURE_COLS),
            60,
            30,
            sample_split=split,
        )
        if len(x_values) < sample_limit:
            raise ValueError(f"{split}: only {len(x_values)} smoke windows available")
        outputs.append((
            x_values[:sample_limit],
            y_values[:sample_limit],
            metadata.iloc[:sample_limit].reset_index(drop=True),
        ))
    return outputs


def _run_keras_smoke(panel: pd.DataFrame, artifact_dir: Path, sample_limit: int) -> dict:
    from sklearn.preprocessing import StandardScaler
    from tensorflow import keras

    from artifact_io import save_pickle_atomic
    import train_gru
    import train_lstm_c
    import train_lstm_d
    import train_seq2seq_30d

    (x_train, y30_train, _), (x_val, y30_val, _) = _real_smoke_windows(
        panel, sample_limit
    )
    x_scaler = StandardScaler().fit(x_train.reshape(-1, x_train.shape[-1]))

    def scale(values):
        return x_scaler.transform(values.reshape(-1, values.shape[-1])).reshape(values.shape)

    x_train = scale(x_train).astype(np.float32)
    x_val = scale(x_val).astype(np.float32)
    y7_scaler = StandardScaler().fit(y30_train[:, :7])
    y30_scaler = StandardScaler().fit(y30_train)
    y7_train = y7_scaler.transform(y30_train[:, :7]).astype(np.float32)
    y7_val = y7_scaler.transform(y30_val[:, :7]).astype(np.float32)
    y30_train_scaled = y30_scaler.transform(y30_train).astype(np.float32)
    y30_val_scaled = y30_scaler.transform(y30_val).astype(np.float32)
    item_train = np.zeros((sample_limit, 1), dtype=np.int32)
    item_val = np.zeros((sample_limit, 1), dtype=np.int32)

    cases = [
        ("lstm_c", train_lstm_c.build_model(2), [x_train, item_train],
         [x_val, item_val], y7_train, y7_val, (sample_limit, 7)),
        ("lstm_d", train_lstm_d.build_model("smoke"), x_train, x_val,
         y7_train, y7_val, (sample_limit, 7)),
        ("gru", train_gru.build_model(), x_train, x_val,
         y7_train, y7_val, (sample_limit, 7)),
        ("trend_30d", train_seq2seq_30d.build_model(units=4, dropout=0.0),
         x_train, x_val, y30_train_scaled, y30_val_scaled, (sample_limit, 30, 3)),
    ]
    results = {}
    for name, model, fit_x, val_x, fit_y, val_y, expected_shape in cases:
        path = artifact_dir / f"{name}.keras"
        checkpoint = keras.callbacks.ModelCheckpoint(
            path, monitor="val_loss", save_best_only=True, save_weights_only=False
        )
        model.fit(
            fit_x,
            fit_y,
            validation_data=(val_x, val_y),
            epochs=1,
            batch_size=4,
            callbacks=[checkpoint],
            verbose=0,
        )
        loaded = keras.models.load_model(path, compile=False)
        prediction = np.asarray(loaded.predict(val_x, verbose=0))
        if prediction.shape != expected_shape or not np.isfinite(prediction).all():
            raise ValueError(
                f"{name}: invalid reloaded prediction {prediction.shape}"
            )
        results[name] = {"status": "passed", "prediction_shape": list(prediction.shape)}
        del model, loaded
        keras.backend.clear_session()
        gc.collect()

    scaler_path = artifact_dir / "sequence_scalers.pkl"
    save_pickle_atomic(
        {"x_scaler": x_scaler, "y7_scaler": y7_scaler, "y30_scaler": y30_scaler},
        scaler_path,
    )
    with scaler_path.open("rb") as handle:
        reloaded_scalers = pickle.load(handle)
    if set(reloaded_scalers) != {"x_scaler", "y7_scaler", "y30_scaler"}:
        raise ValueError("sequence scaler roundtrip failed")
    return results


def _run_tree_smoke(panel: pd.DataFrame, artifact_dir: Path) -> dict:
    import joblib
    from lightgbm import LGBMClassifier, LGBMRegressor
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from xgboost import XGBClassifier, XGBRegressor

    from tree_features import classification_arrays, regression_arrays

    valid = panel[
        (panel["_split"] == "train")
        & (panel["_target_split"] == "train")
        & panel["Target"].notna()
    ].reset_index(drop=True)
    x_all, y_cls, *_ = classification_arrays(valid)
    _, y_reg, *_ = regression_arrays(valid)
    indices = np.concatenate([
        np.flatnonzero(y_cls == label)[:8] for label in (0, 1, 2)
    ])
    if len(indices) < 12 or set(y_cls[indices]) != {0, 1, 2}:
        raise ValueError("tree smoke data does not cover all three classes")
    x_values = np.asarray(x_all[indices], dtype=np.float32)
    reg_target = np.asarray(y_reg[indices], dtype=np.float32)
    cls_target = np.asarray(y_cls[indices], dtype=np.int32)

    cases = {
        "rf_reg": RandomForestRegressor(n_estimators=3, max_depth=3, random_state=42),
        "lightgbm_reg": LGBMRegressor(n_estimators=3, max_depth=3, random_state=42, verbose=-1),
        "xgboost_reg": XGBRegressor(n_estimators=3, max_depth=3, random_state=42, tree_method="hist"),
        "rf_cls": RandomForestClassifier(n_estimators=3, max_depth=3, random_state=42),
        "lightgbm_cls": LGBMClassifier(n_estimators=3, max_depth=3, random_state=42, verbose=-1),
        "xgboost_cls": XGBClassifier(
            n_estimators=3, max_depth=3, random_state=42,
            tree_method="hist", eval_metric="mlogloss",
        ),
    }
    results = {}
    for name, model in cases.items():
        target = cls_target if name.endswith("_cls") else reg_target
        model.fit(x_values, target)
        path = artifact_dir / f"{name}.pkl"
        joblib.dump({"model": model, "feature_cols": TREE_FEATURE_COLS}, path)
        bundle = joblib.load(path)
        if tuple(bundle["feature_cols"]) != TREE_FEATURE_COLS:
            raise ValueError(f"{name}: tree feature contract roundtrip failed")
        prediction = np.asarray(bundle["model"].predict(x_values))
        if prediction.shape != (len(x_values),) or not np.isfinite(prediction).all():
            raise ValueError(f"{name}: invalid reloaded prediction")
        results[name] = {"status": "passed", "prediction_shape": list(prediction.shape)}
    return results


def _run_csv_smoke(panel: pd.DataFrame, artifact_dir: Path, sample_limit: int) -> dict:
    from evaluate_trend_30d import evaluate_prediction_frame
    from forecast_contract import validate_prediction_frame_seq
    from make_predictions import build_trend_prediction_frame

    (_, _, _), (_, y30_val, metadata) = _real_smoke_windows(panel, sample_limit)
    truth = np.expm1(y30_val)

    forecast = metadata.copy()
    forecast["horizon_steps"] = 7
    forecast["target_date"] = forecast["date"]
    forecast["actual_future_price"] = truth[:, 6]
    for day in range(1, 8):
        forecast[f"actual_future_price_d{day}"] = truth[:, day - 1]
        forecast[f"predicted_price_d{day}"] = truth[:, day - 1]
    forecast = validate_prediction_frame_seq(forecast, "preflight 7d")
    forecast_path = artifact_dir / "forecast_7d.csv"
    forecast.to_csv(forecast_path, index=False)
    validate_prediction_frame_seq(pd.read_csv(forecast_path), forecast_path)

    trend_prediction = np.stack([truth * 0.9, truth, truth * 1.1], axis=-1)
    trend = build_trend_prediction_frame(
        metadata, trend_prediction, model_version="preflight"
    )
    trend_path = artifact_dir / "trend_30d.csv"
    trend.to_csv(trend_path, index=False)
    metrics = evaluate_prediction_frame(pd.read_csv(trend_path))
    if metrics["rows"] != sample_limit:
        raise ValueError("30-day CSV roundtrip changed row count")
    return {"forecast_7d": "passed", "trend_30d": "passed"}


def run_training_smoke(panel: pd.DataFrame, sample_limit: int = 8) -> dict:
    """Train/save/reload/predict every architecture without touching real artifacts."""
    temp_root = BASE_DIR / ".test-tmp"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="training-smoke-", dir=temp_root) as directory:
        artifact_dir = Path(directory)
        report = {
            "keras": _run_keras_smoke(panel, artifact_dir, sample_limit),
            "trees": _run_tree_smoke(panel, artifact_dir),
            "csv": _run_csv_smoke(panel, artifact_dir, sample_limit),
        }
        return validate_smoke_report(report)


def main() -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    parser.add_argument("--report", type=Path, default=REPORT_PATH)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=8)
    args = parser.parse_args()

    report = {"dataset": audit_split_frames(load_raw_splits(args.data_dir))}
    feature_report, panel = audit_feature_panel(args.data_dir)
    report["features"] = feature_report
    if args.smoke:
        report["training_smoke"] = run_training_smoke(panel, args.sample_limit)
    report["status"] = "passed"
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"preflight dataset audit passed: {args.report}", flush=True)
    return report


if __name__ == "__main__":
    main()
