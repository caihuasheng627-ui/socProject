"""Export aligned LSTM-C, LSTM-D, Hybrid, and GRU predictions."""

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from forecast_contract import (
    PREDICTION_COLUMNS,
    build_sequence_windows,
    decode_log_price_predictions,
    encode_item_ids,
    load_feature_panel,
    route_price_group,
    validate_prediction_frame,
)
from train_lstm_c import FEATURE_COLS, LOOKBACK


sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_DIR = BASE_DIR / "models"
PRED_DIR = BASE_DIR / "preds"
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


def export_prediction(meta, prediction, mask, path):
    output = meta.loc[mask].copy()
    output["predicted_price"] = prediction[mask]
    output = validate_prediction_frame(output[PREDICTION_COLUMNS], path)
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
    train_price_floor = float(panel.loc[panel["_split"] == "train", "price"].min())
    x_values, y_log, meta = build_sequence_windows(
        panel, FEATURE_COLS, LOOKBACK, sample_split=split
    )
    truth = meta["actual_future_price"].to_numpy()
    print(f"{split}: X={x_values.shape}, {meta.market_hash_name.nunique()} items")

    model_c = keras.models.load_model(MODEL_DIR / "lstm_c.keras")
    with open(MODEL_DIR / "lstm_c_scaler.pkl", "rb") as handle:
        scaler_c = pickle.load(handle)
    with open(MODEL_DIR / "lstm_c_item_map.pkl", "rb") as handle:
        item_map = pickle.load(handle)

    item_ids = encode_item_ids(meta["market_hash_name"], item_map).reshape(-1, 1)
    pred_scaled = model_c.predict(
        [scale_x(x_values, scaler_c["x_scaler"]), item_ids], verbose=0, batch_size=512
    ).ravel()
    pred_c = decode_log_price_predictions(pred_scaled, scaler_c["y_scaler"], train_price_floor)

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
    pred_d = np.full(len(meta), np.nan)
    for group in GROUP_NAMES:
        group_mask = routes == group
        group_scaled = models_d[group].predict(
            scale_x(x_values[group_mask], scalers_d[group]["x_scaler"]),
            verbose=0,
            batch_size=512,
        ).ravel()
        pred_d[group_mask] = decode_log_price_predictions(
            group_scaled, scalers_d[group]["y_scaler"], train_price_floor
        )
    if not np.isfinite(pred_d).all():
        raise RuntimeError("LSTM-D failed to cover all prediction rows")

    hybrid_route = load_hybrid_route(split)
    pred_hybrid = None
    if hybrid_route is not None:
        use_c = np.array([hybrid_route[group] == "LSTM-C" for group in routes])
        pred_hybrid = np.where(use_c, pred_c, pred_d)
    print_metrics("LSTM-C", truth, pred_c)
    print_metrics("LSTM-D", truth, pred_d)
    if pred_hybrid is not None:
        print_metrics("Hybrid", truth, pred_hybrid)
    else:
        print("  Hybrid route not frozen yet; exporting C/D val predictions only")

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
    ).ravel()
    pred_gru = np.full(len(meta), np.nan)
    pred_gru[gru_mask] = decode_log_price_predictions(
        gru_scaled, scaler_gru["y_scaler"], train_price_floor
    )
    print_metrics("GRU top10", truth[gru_mask], pred_gru[gru_mask])

    all_rows = np.ones(len(meta), dtype=bool)
    export_prediction(meta, pred_c, all_rows, PRED_DIR / f"pred_lstm_c_{split}.csv")
    export_prediction(meta, pred_d, all_rows, PRED_DIR / f"pred_lstm_d_{split}.csv")
    if pred_hybrid is not None:
        export_prediction(meta, pred_hybrid, all_rows, PRED_DIR / f"pred_lstm_hybrid_{split}.csv")
    export_prediction(meta, pred_gru, gru_mask, PRED_DIR / f"pred_gru_{split}.csv")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", choices=("val", "test"), default="val")
    args = parser.parse_args()
    main(args.split)
