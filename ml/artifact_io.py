"""Atomic persistence and validation helpers for model artifacts."""

from __future__ import annotations

import os
import pickle
import shutil
import uuid
from pathlib import Path

import numpy as np


def _temporary_path(path: Path) -> Path:
    return path.with_name(f".{path.stem}.tmp-{uuid.uuid4().hex}{path.suffix}")


def _remove_temporary(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


def save_keras_artifact_atomic(model, path: str | Path) -> Path:
    """Save a Keras model beside its destination, then atomically replace it."""
    path = Path(path)
    if path.suffix != ".keras":
        raise ValueError("Keras artifact path must use the .keras extension")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        model.save(temporary)
        if not temporary.exists():
            raise RuntimeError(f"Keras did not create expected artifact: {temporary}")
        os.replace(temporary, path)
    finally:
        _remove_temporary(temporary)
    return path


def save_pickle_atomic(payload, path: str | Path) -> Path:
    """Serialize a pickle to a temporary file and atomically replace its target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        with temporary.open("wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        _remove_temporary(temporary)
    return path


def save_joblib_artifact_atomic(payload, path: str | Path, *, validator=None):
    """Write and reload a joblib artifact before atomically publishing it."""
    import joblib

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(path)
    try:
        joblib.dump(payload, temporary)
        loaded = joblib.load(temporary)
        if validator is not None:
            validator(loaded)
        os.replace(temporary, path)
        return loaded
    finally:
        _remove_temporary(temporary)


def load_keras_artifact(
    path: str | Path,
    *,
    expected_input_shape: tuple[int, ...],
    expected_output_shape: tuple[int, ...],
    sample_input: np.ndarray | None = None,
):
    """Load a Keras artifact without compilation and validate its public contract."""
    from tensorflow import keras

    path = Path(path)
    model = keras.models.load_model(path, compile=False)
    actual_input = tuple(model.input_shape[1:])
    actual_output = tuple(model.output_shape[1:])
    if actual_input != tuple(expected_input_shape):
        raise ValueError(
            f"{path}: expected input {expected_input_shape}, got {actual_input}"
        )
    if actual_output != tuple(expected_output_shape):
        raise ValueError(
            f"{path}: expected output {expected_output_shape}, got {actual_output}"
        )

    if sample_input is not None:
        values = np.asarray(sample_input, dtype=np.float32)
        prediction = np.asarray(model.predict(values, verbose=0))
        expected = (len(values), *expected_output_shape)
        if prediction.shape != expected:
            raise ValueError(f"{path}: expected prediction {expected}, got {prediction.shape}")
        if not np.isfinite(prediction).all():
            raise ValueError(f"{path}: prediction contains non-finite values")
    return model


def promote_keras_checkpoint(
    checkpoint: str | Path,
    destination: str | Path,
    *,
    sample_inputs,
    expected_output_shape: tuple[int, ...],
):
    """Reload a best checkpoint, validate it, then atomically publish it."""
    from tensorflow import keras

    checkpoint = Path(checkpoint)
    destination = Path(destination)
    best_model = keras.models.load_model(checkpoint, compile=False)
    prediction = np.asarray(best_model.predict(sample_inputs, verbose=0))
    if prediction.shape != tuple(expected_output_shape):
        raise ValueError(
            f"{checkpoint}: expected prediction {expected_output_shape}, "
            f"got {prediction.shape}"
        )
    if not np.isfinite(prediction).all():
        raise ValueError(f"{checkpoint}: prediction contains non-finite values")

    save_keras_artifact_atomic(best_model, destination)
    published = keras.models.load_model(destination, compile=False)
    published_prediction = np.asarray(published.predict(sample_inputs, verbose=0))
    if published_prediction.shape != tuple(expected_output_shape):
        raise ValueError(
            f"{destination}: published prediction shape changed to "
            f"{published_prediction.shape}"
        )
    if not np.isfinite(published_prediction).all():
        raise ValueError(f"{destination}: published prediction is non-finite")
    checkpoint.unlink()
    return published
