"""Export aligned LSTM-C, LSTM-D, Hybrid, and GRU multi-step predictions.

Outputs per-day predicted_price_d1..predicted_price_d7 columns.
"""

import argparse
import hashlib
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from forecast_contract import (
    PREDICTION_COLUMNS_SEQ,
    HORIZON_STEPS,
    build_sequence_windows_multi,
    decode_log_price_predictions_multi,
    encode_item_ids,
    load_feature_panel,
    add_grouped_targets_multi,
    route_price_group,
    validate_prediction_frame_seq,
)
from train_lstm_c import FEATURE_COLS, LOOKBACK
from artifact_io import load_keras_artifact
from train_seq2seq_30d import FEATURE_COLS as TREND_FEATURE_COLS
from evaluate_trend_30d import evaluate_prediction_frame, save_split_metrics


sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_DIR = BASE_DIR / "models"
PRED_DIR = BASE_DIR / "preds"
SEQ_HORIZON = 7
GROUP_NAMES = ["low", "mid", "high"]


def scale_x(values, scaler):
    n_samples, n_steps, n_features = values.shape
    return scaler.transform(values.reshape(-1, n_features)).reshape(
        n_samples, n_steps, n_features
    )


def metric_block(y_true, y_pred):
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(np.sqrt(mean_squared_error(y_true, y_pred))),
        "mape": float(np.mean(np.abs((y_true - y_pred) / np.maximum(y_true, 0.01))) * 100),
        "r2": float(r2_score(y_true, y_pred)),
    }


def print_metrics(label, truth, prediction):
    metrics = metric_block(truth, prediction)
    print(
        f"  [{label:<12}] MAE ${metrics['mae']:.4f} | "
        f"RMSE ${metrics['rmse']:.4f} | MAPE {metrics['mape']:.2f}% | "
        f"R² {metrics['r2']:.4f}"
    )
    return metrics


def export_prediction_seq(meta, prediction, mask, path):
    """Export multi-step predictions with pred_day1..pred_day7 columns."""
    output = meta.loc[mask].copy()
    # Apply mask to prediction array (n_masked, 7)
    pred_masked = prediction[mask] if prediction.shape[0] == len(meta) else prediction
    for d in range(1, SEQ_HORIZON + 1):
        output[f"predicted_price_d{d}"] = pred_masked[:, d - 1]
    # actual_future_price_d1..d7 come from meta
    # Build required columns
    cols = (
        ["split", "date", "target_date", "market_hash_name", "current_price",
         "actual_future_price", "horizon_steps"]
        + [f"actual_future_price_d{d}" for d in range(1, SEQ_HORIZON + 1)]
        + [f"predicted_price_d{d}" for d in range(1, SEQ_HORIZON + 1)]
    )
    # Drop duplicate (item, date) rows — raw test.csv has 689 dupes
    before = len(output)
    output = output.drop_duplicates(["market_hash_name", "date"], keep="first")
    if len(output) < before:
        print(f"  dropped {before - len(output)} duplicate (item,date) rows")
    output = validate_prediction_frame_seq(output[cols], path)
    output.to_csv(path, index=False, date_format="%Y-%m-%d")
    print(f"  saved {path.name}: {len(output):,} rows, {output.market_hash_name.nunique()} items")


def load_hybrid_route(split):
    route_path = MODEL_DIR / "lstm_hybrid_route.json"
    if not route_path.exists():
        if split == "test":
            raise FileNotFoundError(
                "Hybrid route is not frozen. Generate val C/D predictions and run compare_lstm_cd.py first."
            )
        return None
    import json

    payload = json.loads(route_path.read_text(encoding="utf-8"))
    route = payload.get("route")
    if set(route or {}) != set(GROUP_NAMES):
        raise ValueError(f"Invalid Hybrid route metadata in {route_path}")
    return route


def main(split="val"):
    from tensorflow import keras

    if split not in {"val", "test"}:
        raise ValueError("split must be val or test")
    PRED_DIR.mkdir(parents=True, exist_ok=True)

    panel = load_feature_panel(DATA_DIR)
    panel = add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)
    train_price_floor = float(panel.loc[panel["_split"] == "train", "price"].min())

    x_values, y_log, meta = build_sequence_windows_multi(
        panel, list(FEATURE_COLS), LOOKBACK, SEQ_HORIZON, sample_split=split
    )
    # truth per day (USD) — only care about day 7 for legacy comparison
    truth_d7 = meta["actual_future_price"].to_numpy()
    print(f"{split}: X={x_values.shape}, y={y_log.shape}, {meta.market_hash_name.nunique()} items")

    # ---- LSTM-C ----
    model_c = keras.models.load_model(MODEL_DIR / "lstm_c.keras")
    with open(MODEL_DIR / "lstm_c_scaler.pkl", "rb") as handle:
        scaler_c = pickle.load(handle)
    with open(MODEL_DIR / "lstm_c_item_map.pkl", "rb") as handle:
        item_map = pickle.load(handle)

    item_ids = encode_item_ids(meta["market_hash_name"], item_map).reshape(-1, 1)
    pred_scaled_c = model_c.predict(
        [scale_x(x_values, scaler_c["x_scaler"]), item_ids], verbose=0, batch_size=512
    )  # (n, 7)
    pred_c = decode_log_price_predictions_multi(
        pred_scaled_c, scaler_c["y_scaler"], train_price_floor
    )  # (n, 7) USD

    print_metrics("LSTM-C d7", truth_d7, pred_c[:, -1])

    # ---- LSTM-D ----
    with open(MODEL_DIR / "lstm_d_scalers.pkl", "rb") as handle:
        scalers_d = pickle.load(handle)
    with open(MODEL_DIR / "lstm_d_group_map.pkl", "rb") as handle:
        group_map = pickle.load(handle)
    models_d = {
        group: keras.models.load_model(MODEL_DIR / f"lstm_d_{group}.keras")
        for group in GROUP_NAMES
    }
    routes = np.array([
        route_price_group(
            row.market_hash_name,
            row.current_price,
            group_map["item_group"],
            tuple(group_map["boundaries"]),
        )
        for row in meta.itertuples(index=False)
    ])
    pred_d = np.full((len(meta), SEQ_HORIZON), np.nan)
    for group in GROUP_NAMES:
        group_mask = routes == group
        group_scaled = models_d[group].predict(
            scale_x(x_values[group_mask], scalers_d[group]["x_scaler"]),
            verbose=0,
            batch_size=512,
        )  # (n_g, 7)
        pred_d[group_mask] = decode_log_price_predictions_multi(
            group_scaled, scalers_d[group]["y_scaler"], train_price_floor
        )
    if not np.isfinite(pred_d).all():
        raise RuntimeError("LSTM-D failed to cover all prediction rows")

    print_metrics("LSTM-D d7", truth_d7, pred_d[:, -1])

    # ---- Hybrid ----
    hybrid_route = load_hybrid_route(split)
    pred_hybrid = None
    if hybrid_route is not None:
        use_c = np.array([hybrid_route[group] == "LSTM-C" for group in routes])
        pred_hybrid = np.where(use_c[:, None], pred_c, pred_d)
        print_metrics("Hybrid d7", truth_d7, pred_hybrid[:, -1])
    else:
        print("  Hybrid route not frozen yet; exporting C/D val predictions only")

    # ---- GRU ----
    with open(MODEL_DIR / "gru_items.pkl", "rb") as handle:
        gru_items = pickle.load(handle)
    with open(MODEL_DIR / "gru_scaler.pkl", "rb") as handle:
        scaler_gru = pickle.load(handle)
    model_gru = keras.models.load_model(MODEL_DIR / "gru.keras")
    gru_mask = meta["market_hash_name"].isin(gru_items).to_numpy()
    gru_scaled = model_gru.predict(
        scale_x(x_values[gru_mask], scaler_gru["x_scaler"]),
        verbose=0,
        batch_size=512,
    )  # (n_gru, 7)
    pred_gru = np.full((len(meta), SEQ_HORIZON), np.nan)
    pred_gru[gru_mask] = decode_log_price_predictions_multi(
        gru_scaled, scaler_gru["y_scaler"], train_price_floor
    )
    print_metrics("GRU top10 d7", truth_d7[gru_mask], pred_gru[gru_mask, -1])

    # ---- Export ----
    all_rows = np.ones(len(meta), dtype=bool)
    export_prediction_seq(meta, pred_c, all_rows, PRED_DIR / f"pred_lstm_c_{split}.csv")
    export_prediction_seq(meta, pred_d, all_rows, PRED_DIR / f"pred_lstm_d_{split}.csv")
    if pred_hybrid is not None:
        export_prediction_seq(meta, pred_hybrid, all_rows, PRED_DIR / f"pred_lstm_hybrid_{split}.csv")
    export_prediction_seq(meta, pred_gru, gru_mask, PRED_DIR / f"pred_gru_{split}.csv")


# ============================================================
# 30 天趋势模型预测导出
# ============================================================

TREND_HORIZON = 30
TREND_QUANTILE_COLS = ["p10", "p50", "p90"]


def sanitize_trend_quantiles(
    prediction, *, minimum_price=0.01, max_band_fraction=0.40
):
    """Order and bound P10/P50/P90 without applying the seven-day breaker."""
    values = np.asarray(prediction, dtype=float)
    if values.ndim != 3 or values.shape[-1] != 3:
        raise ValueError(f"trend prediction must have shape (n, horizon, 3), got {values.shape}")
    if not np.isfinite(values).all():
        raise ValueError("trend prediction contains non-finite values")
    if minimum_price <= 0 or not 0 < max_band_fraction < 1:
        raise ValueError("minimum price and max band fraction must be valid positive bounds")

    ordered = np.sort(values, axis=-1)
    p50 = np.maximum(ordered[..., 1], minimum_price)
    p10 = np.maximum(ordered[..., 0], minimum_price)
    p90 = np.maximum(ordered[..., 2], p50)
    p10 = np.maximum(np.minimum(p10, p50), p50 * (1.0 - max_band_fraction))
    p90 = np.minimum(p90, p50 * (1.0 + max_band_fraction))
    return np.stack([p10, p50, p90], axis=-1)


def build_trend_prediction_frame(meta, prediction, *, model_version):
    """Build and validate the canonical 30-day quantile prediction frame."""
    values = sanitize_trend_quantiles(prediction)
    if len(meta) != len(values):
        raise ValueError("metadata and trend prediction row counts differ")
    required_meta = [
        "split", "date", "target_date", "market_hash_name", "current_price",
        "actual_future_price", "horizon_steps",
        *[f"actual_future_price_d{day}" for day in range(1, TREND_HORIZON + 1)],
    ]
    missing = [column for column in required_meta if column not in meta.columns]
    if missing:
        raise ValueError(f"trend metadata is missing columns: {missing}")

    output = meta[required_meta].copy()
    output["model_name"] = "Seq2Seq-30D-Quantile"
    output["model_version"] = str(model_version)
    output["horizon_steps"] = TREND_HORIZON
    for day in range(1, TREND_HORIZON + 1):
        for index, quantile in enumerate(TREND_QUANTILE_COLS):
            output[f"trend_{quantile}_d{day}"] = values[:, day - 1, index]

    output["date"] = np.asarray(output["date"], dtype="datetime64[ns]")
    output["target_date"] = np.asarray(output["target_date"], dtype="datetime64[ns]")
    splits = set(output["split"].dropna().astype(str))
    if len(splits) != 1 or not splits.issubset({"val", "test"}):
        raise ValueError(f"trend split must be exactly one of val/test, got {sorted(splits)}")

    numeric_columns = [
        "current_price", "actual_future_price",
        *[f"actual_future_price_d{day}" for day in range(1, TREND_HORIZON + 1)],
        *[
            f"trend_{quantile}_d{day}"
            for day in range(1, TREND_HORIZON + 1)
            for quantile in TREND_QUANTILE_COLS
        ],
    ]
    numeric = output[numeric_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy()).all() or (numeric <= 0).any().any():
        raise ValueError("trend actual and predicted prices must be finite and positive")
    output[numeric_columns] = numeric
    output = output.drop_duplicates(["market_hash_name", "date"], keep="first")
    return output.sort_values(["date", "market_hash_name"]).reset_index(drop=True)


def _trend_model_version(*paths):
    digest = hashlib.sha256()
    for path in paths:
        stat = Path(path).stat()
        digest.update(f"{Path(path).name}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return f"seq2seq-30d-keras-{digest.hexdigest()[:12]}"


def export_trend_30d(split="val"):
    """Load seq2seq_30d model and export P10/P50/P90 per-day predictions."""
    if split not in {"val", "test"}:
        raise ValueError("split must be val or test")
    PRED_DIR.mkdir(parents=True, exist_ok=True)

    panel = load_feature_panel(DATA_DIR)
    panel = add_grouped_targets_multi(panel, horizon_steps=TREND_HORIZON)
    model_path = MODEL_DIR / "seq2seq_30d.keras"
    scaler_path = MODEL_DIR / "seq2seq_30d_scaler.pkl"
    if not model_path.exists() or not scaler_path.exists():
        raise FileNotFoundError("30-day Keras model/scaler missing; train seq2seq_30d first")
    with open(scaler_path, "rb") as f:
        scalers = pickle.load(f)
    artifact_features = list(scalers.get("feature_cols", []))
    if artifact_features != list(TREND_FEATURE_COLS):
        raise ValueError(
            "30-day scaler feature contract does not match model_features.SEQUENCE_FEATURE_COLS"
        )

    x_values, _y_log, meta = build_sequence_windows_multi(
        panel, list(TREND_FEATURE_COLS), LOOKBACK, TREND_HORIZON, sample_split=split
    )
    x_scaled = scale_x(x_values, scalers["x_scaler"]).astype(np.float32)
    print(f"30d {split}: X={x_values.shape}, {meta.market_hash_name.nunique()} items")
    model = load_keras_artifact(
        model_path,
        expected_input_shape=(LOOKBACK, len(TREND_FEATURE_COLS)),
        expected_output_shape=(TREND_HORIZON, 3),
        sample_input=x_scaled[: min(2, len(x_scaled))],
    )

    y_scaler = scalers["y_scaler"]

    # Predict
    y_pred_scaled = model.predict(
        x_scaled, verbose=0, batch_size=512
    )  # (n, 30, 3)

    # Inverse transform: per-quantile
    y_pred_log = np.zeros_like(y_pred_scaled)
    for q in range(3):
        y_pred_log[:, :, q] = y_scaler.inverse_transform(y_pred_scaled[:, :, q])
    y_pred_price = sanitize_trend_quantiles(np.expm1(y_pred_log))
    output = build_trend_prediction_frame(
        meta,
        y_pred_price,
        model_version=_trend_model_version(model_path, scaler_path),
    )

    out_path = PRED_DIR / f"pred_seq2seq_30d_{split}.csv"
    output.to_csv(out_path, index=False, date_format="%Y-%m-%d")
    print(f"  saved {out_path.name}: {len(output):,} rows, {output.market_hash_name.nunique()} items")
    metrics = evaluate_prediction_frame(output)
    metrics_path = save_split_metrics(metrics, split)
    print(f"  saved {metrics_path.name}")

    # Quick stats
    pred_p50_d30 = output["trend_p50_d30"].to_numpy()
    pred_p10_d30 = output["trend_p10_d30"].to_numpy()
    pred_p90_d30 = output["trend_p90_d30"].to_numpy()
    spread_pct = float(((pred_p90_d30 - pred_p10_d30) / np.maximum(pred_p50_d30, 0.01)).mean() * 100)
    print(f"  Day 30 P50 mean: ${float(pred_p50_d30.mean()):.2f}")
    print(f"  Day 30 avg spread (P10-P90): {spread_pct:.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), default="val")
    parser.add_argument("--model", choices=("7d", "30d", "all"), default="7d",
                        help="Which model to export: 7d (LSTM-C/D/GRU), 30d (seq2seq), all (both)")
    args = parser.parse_args()
    if args.model in ("7d", "all"):
        main(args.split)
    if args.model in ("30d", "all"):
        export_trend_30d(args.split)
