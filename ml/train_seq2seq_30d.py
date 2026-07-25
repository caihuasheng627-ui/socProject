"""Train the Keras 30-day P10/P50/P90 trend model.

The model consumes the same volume-free 60-step feature contract as the
seven-day sequence models and writes a reloadable `.keras` artifact plus its
feature/target scalers.
"""

from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

from artifact_io import (
    load_keras_artifact,
    save_keras_artifact_atomic,
    save_pickle_atomic,
)
from evaluate_trend_30d import evaluate_trend_arrays
from forecast_contract import (
    add_grouped_targets_multi,
    build_sequence_windows_multi,
    load_feature_panel,
)
from gpu_config import configure_device
from model_features import FEATURE_CONTRACT_VERSION, SEQUENCE_FEATURE_COLS


LOOKBACK = 60
SEQ_HORIZON = 30
N_QUANTILES = 3
LSTM_UNITS = 64
DROPOUT = 0.2
BATCH_SIZE = 64
EPOCHS = 150
LEARNING_RATE = 1e-3
CROSSING_PENALTY = 0.25
EARLY_STOP_PATIENCE = 20
LR_PATIENCE = 10
SEED = 42

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
OUTPUT_DIR = BASE_DIR / "models"
MODEL_PATH = OUTPUT_DIR / "seq2seq_30d.keras"
SCALER_PATH = OUTPUT_DIR / "seq2seq_30d_scaler.pkl"

FEATURE_COLS = SEQUENCE_FEATURE_COLS


@keras.utils.register_keras_serializable(package="CSVest")
def quantile_loss_with_crossing(y_true, y_pred):
    """Pinball loss for P10/P50/P90 plus a quantile crossing penalty."""
    y_true = tf.cast(y_true, y_pred.dtype)
    y_true = tf.expand_dims(y_true, axis=-1)
    quantiles = tf.cast([0.1, 0.5, 0.9], y_pred.dtype)
    error = y_true - y_pred
    pinball = tf.maximum(quantiles * error, (quantiles - 1.0) * error)
    crossing = tf.nn.relu(y_pred[..., 0] - y_pred[..., 1])
    crossing += tf.nn.relu(y_pred[..., 1] - y_pred[..., 2])
    return tf.reduce_mean(pinball) + CROSSING_PENALTY * tf.reduce_mean(crossing)


def build_model(
    *, units: int = LSTM_UNITS, dropout: float = DROPOUT, learning_rate: float = LEARNING_RATE
):
    """Build `(60, 13) -> (30, 3)` Keras quantile sequence model."""
    inputs = keras.Input((LOOKBACK, len(FEATURE_COLS)), name="sequence")
    x = layers.LSTM(units, return_sequences=True, name="encoder_lstm_1")(inputs)
    x = layers.Dropout(dropout, name="dropout_1")(x)
    x = layers.LSTM(units, name="encoder_lstm_2")(x)
    x = layers.Dropout(dropout, name="dropout_2")(x)
    x = layers.Dense(SEQ_HORIZON * N_QUANTILES, name="quantile_projection")(x)
    outputs = layers.Reshape((SEQ_HORIZON, N_QUANTILES), name="quantiles")(x)
    model = keras.Model(inputs, outputs, name="Seq2Seq_30D_Quantile")
    model.compile(
        optimizer=keras.optimizers.Adam(learning_rate=learning_rate),
        loss=quantile_loss_with_crossing,
    )
    return model


def load_data():
    panel = load_feature_panel(DATA_DIR)
    return add_grouped_targets_multi(panel, horizon_steps=SEQ_HORIZON)


def _smoke_panel(panel, sample_limit: int | None):
    if sample_limit is None:
        return panel
    if sample_limit <= 0:
        raise ValueError("sample_limit must be positive")
    counts = panel.groupby(["market_hash_name", "_split"]).size().unstack(fill_value=0)
    candidates = counts.index[
        (counts.get("train", 0) >= LOOKBACK + SEQ_HORIZON)
        & (counts.get("val", 0) >= SEQ_HORIZON + 1)
    ]
    if len(candidates) == 0:
        raise ValueError("no item has enough train and val history for smoke training")
    return panel[panel["market_hash_name"].isin(candidates[:2])].copy()


def build_sequences(panel, split, *, x_scaler=None, fit_scaler=False, sample_limit=None):
    x_values, y_values, metadata = build_sequence_windows_multi(
        panel, list(FEATURE_COLS), LOOKBACK, SEQ_HORIZON, sample_split=split
    )
    if sample_limit is not None:
        x_values = x_values[:sample_limit]
        y_values = y_values[:sample_limit]
        metadata = metadata.iloc[:sample_limit].reset_index(drop=True)
    if len(x_values) == 0:
        raise ValueError(f"no {split} windows were generated")
    samples, steps, features = x_values.shape
    if fit_scaler or x_scaler is None:
        x_scaler = StandardScaler()
        scaled = x_scaler.fit_transform(x_values.reshape(-1, features))
    else:
        scaled = x_scaler.transform(x_values.reshape(-1, features))
    scaled = scaled.reshape(samples, steps, features).astype(np.float32)
    if fit_scaler:
        return scaled, y_values.astype(np.float32), metadata, x_scaler
    return scaled, y_values.astype(np.float32), metadata


def decode_quantiles(prediction_scaled, y_scaler):
    prediction_scaled = np.asarray(prediction_scaled)
    decoded_log = np.empty_like(prediction_scaled, dtype=float)
    for index in range(N_QUANTILES):
        decoded_log[..., index] = y_scaler.inverse_transform(
            prediction_scaled[..., index]
        )
    return np.maximum(np.expm1(decoded_log), 0.01)


def train(*, epochs=EPOCHS, batch_size=BATCH_SIZE, sample_limit=None):
    keras.utils.set_random_seed(SEED)
    configure_device()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    panel = _smoke_panel(load_data(), sample_limit)
    x_train, y_train, _train_meta, x_scaler = build_sequences(
        panel, "train", fit_scaler=True, sample_limit=sample_limit
    )
    x_val, y_val, _val_meta = build_sequences(
        panel, "val", x_scaler=x_scaler, sample_limit=sample_limit
    )
    y_scaler = StandardScaler().fit(y_train)
    y_train_scaled = y_scaler.transform(y_train).astype(np.float32)
    y_val_scaled = y_scaler.transform(y_val).astype(np.float32)

    model = build_model()
    checkpoint = OUTPUT_DIR / f".seq2seq_30d.best-{uuid.uuid4().hex}.keras"
    callbacks = [
        keras.callbacks.ModelCheckpoint(
            checkpoint, monitor="val_loss", save_best_only=True, verbose=1
        ),
        keras.callbacks.EarlyStopping(
            monitor="val_loss", patience=EARLY_STOP_PATIENCE, restore_best_weights=True
        ),
        keras.callbacks.ReduceLROnPlateau(
            monitor="val_loss", factor=0.5, patience=LR_PATIENCE, min_lr=1e-6
        ),
    ]

    started = time.time()
    history = model.fit(
        x_train,
        y_train_scaled,
        validation_data=(x_val, y_val_scaled),
        epochs=epochs,
        batch_size=batch_size,
        callbacks=callbacks,
        verbose=1,
    )
    best_model = load_keras_artifact(
        checkpoint,
        expected_input_shape=(LOOKBACK, len(FEATURE_COLS)),
        expected_output_shape=(SEQ_HORIZON, N_QUANTILES),
        sample_input=x_val[: min(2, len(x_val))],
    )
    scaler_bundle = {
        "x_scaler": x_scaler,
        "y_scaler": y_scaler,
        "feature_cols": FEATURE_COLS,
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "lookback": LOOKBACK,
        "horizon_steps": SEQ_HORIZON,
        "quantiles": [0.1, 0.5, 0.9],
        "framework": "keras",
        "epochs_ran": len(history.history["loss"]),
        "best_val_loss": float(min(history.history["val_loss"])),
    }
    save_keras_artifact_atomic(best_model, MODEL_PATH)
    save_pickle_atomic(scaler_bundle, SCALER_PATH)

    reloaded = load_keras_artifact(
        MODEL_PATH,
        expected_input_shape=(LOOKBACK, len(FEATURE_COLS)),
        expected_output_shape=(SEQ_HORIZON, N_QUANTILES),
        sample_input=x_val[: min(2, len(x_val))],
    )
    import pickle
    with SCALER_PATH.open("rb") as handle:
        reloaded_scalers = pickle.load(handle)
    if (
        tuple(reloaded_scalers.get("feature_cols", ())) != FEATURE_COLS
        or reloaded_scalers.get("feature_contract_version") != FEATURE_CONTRACT_VERSION
    ):
        raise ValueError("published 30-day scaler contract is invalid")
    x_probe = reloaded_scalers["x_scaler"].transform(
        x_val[:1].reshape(-1, len(FEATURE_COLS))
    )
    y_probe = reloaded_scalers["y_scaler"].inverse_transform(y_val_scaled[:1])
    if not np.isfinite(x_probe).all() or not np.isfinite(y_probe).all():
        raise ValueError("published 30-day scaler produces non-finite values")
    prediction_scaled = reloaded.predict(x_val, batch_size=batch_size, verbose=0)
    prediction_price = decode_quantiles(prediction_scaled, reloaded_scalers["y_scaler"])
    if checkpoint.exists():
        checkpoint.unlink()
    metrics = evaluate_trend_arrays(np.expm1(y_val), prediction_price)
    metrics["elapsed_seconds"] = float(time.time() - started)
    print(metrics)
    print(f"saved {MODEL_PATH}")
    print(f"saved {SCALER_PATH}")
    return reloaded, history, metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", type=int, default=EPOCHS)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--sample-limit", type=int)
    args = parser.parse_args()
    if args.epochs <= 0 or args.batch_size <= 0:
        parser.error("--epochs and --batch-size must be positive")
    train(epochs=args.epochs, batch_size=args.batch_size, sample_limit=args.sample_limit)


if __name__ == "__main__":
    main()
