import pickle

import numpy as np
import tensorflow as tf
from sklearn.preprocessing import StandardScaler

import train_seq2seq_30d as training

from artifact_io import (
    load_keras_artifact,
    promote_keras_checkpoint,
    save_joblib_artifact_atomic,
    save_keras_artifact_atomic,
    save_pickle_atomic,
)
from train_seq2seq_30d import (
    FEATURE_COLS,
    LOOKBACK,
    SEQ_HORIZON,
    build_model,
    quantile_loss_with_crossing,
)


def test_keras_trend_model_has_canonical_input_and_quantile_output_shapes():
    model = build_model()

    assert len(FEATURE_COLS) == 13
    assert model.input_shape == (None, LOOKBACK, 13)
    assert model.output_shape == (None, SEQ_HORIZON, 3)


def test_quantile_loss_penalizes_crossing_and_is_finite():
    y_true = tf.constant([[1.0, 1.2]], dtype=tf.float32)
    ordered = tf.constant([[[0.8, 1.0, 1.2], [1.0, 1.2, 1.4]]], dtype=tf.float32)
    crossed = tf.constant([[[1.2, 1.0, 0.8], [1.4, 1.2, 1.0]]], dtype=tf.float32)

    ordered_loss = float(quantile_loss_with_crossing(y_true, ordered).numpy())
    crossed_loss = float(quantile_loss_with_crossing(y_true, crossed).numpy())

    assert np.isfinite(ordered_loss)
    assert np.isfinite(crossed_loss)
    assert crossed_loss > ordered_loss


def test_atomic_keras_and_pickle_artifacts_reload_and_validate(tmp_path):
    model = build_model(units=4, dropout=0.0)
    model_path = tmp_path / "trend.keras"
    scaler_path = tmp_path / "trend_scaler.pkl"

    save_keras_artifact_atomic(model, model_path)
    save_pickle_atomic({"feature_cols": FEATURE_COLS}, scaler_path)

    loaded = load_keras_artifact(
        model_path,
        expected_input_shape=(LOOKBACK, len(FEATURE_COLS)),
        expected_output_shape=(SEQ_HORIZON, 3),
        sample_input=np.zeros((2, LOOKBACK, len(FEATURE_COLS)), dtype=np.float32),
    )
    with scaler_path.open("rb") as handle:
        payload = pickle.load(handle)

    prediction = loaded.predict(
        np.zeros((2, LOOKBACK, len(FEATURE_COLS)), dtype=np.float32), verbose=0
    )
    assert prediction.shape == (2, SEQ_HORIZON, 3)
    assert np.isfinite(prediction).all()
    assert payload["feature_cols"] == FEATURE_COLS
    assert not list(tmp_path.glob("*.tmp-*"))


def test_one_epoch_training_smoke_reloads_final_artifacts(monkeypatch, tmp_path):
    samples = 4
    x_values = np.zeros((samples, LOOKBACK, len(FEATURE_COLS)), dtype=np.float32)
    y_values = np.log1p(
        np.tile(np.linspace(10.0, 12.0, SEQ_HORIZON), (samples, 1))
    ).astype(np.float32)
    x_scaler = StandardScaler().fit(
        np.zeros((LOOKBACK, len(FEATURE_COLS)), dtype=np.float32)
    )

    monkeypatch.setattr(training, "configure_device", lambda: None)
    monkeypatch.setattr(training, "load_data", lambda: object())
    monkeypatch.setattr(training, "_smoke_panel", lambda panel, limit: panel)

    def fake_sequences(panel, split, **kwargs):
        if kwargs.get("fit_scaler"):
            return x_values, y_values, None, x_scaler
        return x_values, y_values, None

    monkeypatch.setattr(training, "build_sequences", fake_sequences)
    monkeypatch.setattr(training, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(training, "MODEL_PATH", tmp_path / "seq2seq_30d.keras")
    monkeypatch.setattr(training, "SCALER_PATH", tmp_path / "seq2seq_30d_scaler.pkl")

    model, history, metrics = training.train(epochs=1, batch_size=2, sample_limit=4)

    assert model.input_shape == (None, LOOKBACK, len(FEATURE_COLS))
    assert len(history.history["loss"]) == 1
    assert metrics["rows"] == samples
    assert training.MODEL_PATH.exists()
    assert training.SCALER_PATH.exists()
    assert not list(tmp_path.glob(".seq2seq_30d.best-*.keras"))

    with training.SCALER_PATH.open("rb") as handle:
        scaler_bundle = pickle.load(handle)
    assert scaler_bundle["feature_contract_version"] == "volume-free-v1"
    assert np.isfinite(
        scaler_bundle["x_scaler"].transform(x_values.reshape(-1, len(FEATURE_COLS)))
    ).all()
    assert np.isfinite(scaler_bundle["y_scaler"].inverse_transform(y_values)).all()


def test_sequence_builder_converts_immutable_feature_contract_for_pandas(monkeypatch):
    observed = {}

    def fake_window_builder(panel, feature_cols, lookback, horizon, sample_split):
        observed["feature_cols"] = feature_cols
        x_values = np.zeros((1, lookback, len(feature_cols)), dtype=np.float32)
        y_values = np.zeros((1, horizon), dtype=np.float32)
        return x_values, y_values, None

    monkeypatch.setattr(training, "build_sequence_windows_multi", fake_window_builder)
    training.build_sequences(object(), "train", fit_scaler=True)

    assert isinstance(observed["feature_cols"], list)
    assert observed["feature_cols"] == list(FEATURE_COLS)


def test_promote_checkpoint_reloads_validates_and_atomically_writes_final(tmp_path):
    left = tf.keras.Input((2,), name="left")
    right = tf.keras.Input((1,), name="right")
    merged = tf.keras.layers.Concatenate()([left, right])
    model = tf.keras.Model([left, right], tf.keras.layers.Dense(2)(merged))
    checkpoint = tmp_path / ".best.keras"
    final = tmp_path / "final.keras"
    model.save(checkpoint)
    sample = [
        np.zeros((3, 2), dtype=np.float32),
        np.zeros((3, 1), dtype=np.float32),
    ]

    promoted = promote_keras_checkpoint(
        checkpoint,
        final,
        sample_inputs=sample,
        expected_output_shape=(3, 2),
    )

    assert final.exists()
    assert not checkpoint.exists()
    assert np.isfinite(promoted.predict(sample, verbose=0)).all()


def test_joblib_artifact_is_validated_before_atomic_publish(tmp_path):
    destination = tmp_path / "bundle.pkl"
    observed = []

    loaded = save_joblib_artifact_atomic(
        {"value": 7},
        destination,
        validator=lambda payload: observed.append(payload["value"]),
    )

    assert destination.exists()
    assert loaded == {"value": 7}
    assert observed == [7]
    assert not list(tmp_path.glob(".*.tmp-*.pkl"))
