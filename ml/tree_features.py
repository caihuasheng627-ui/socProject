"""Local tree-model feature preparation for prediction and SHAP analysis."""

from pathlib import Path

import numpy as np

from feature_engineering import fit_categoricals, transform_categoricals
from forecast_contract import load_feature_panel
from model_features import TREE_FEATURE_COLS


FEATURE_COLS = TREE_FEATURE_COLS
CLASS_THRESHOLD = 0.02


def load_tree_split(data_dir: str | Path, split: str):
    if split not in {"train", "val", "test"}:
        raise ValueError("split must be train, val, or test")
    panel = load_feature_panel(data_dir)
    encoders = fit_categoricals(panel[panel["_split"] == "train"])
    panel = transform_categoricals(panel, encoders)
    valid = panel[
        (panel["_split"] == split)
        & (panel["_target_split"] == split)
        & panel["Target"].notna()
    ].sort_values(["date", "market_hash_name"])
    return valid.reset_index(drop=True)


def regression_arrays(frame):
    return (
        frame[list(FEATURE_COLS)].to_numpy(),
        frame["Target"].to_numpy(),
        frame["date"].to_numpy(),
        frame["market_hash_name"].to_numpy(),
        frame["price"].to_numpy(),
    )


def classification_arrays(frame, threshold: float = CLASS_THRESHOLD):
    future_return = np.exp(frame["Target"].to_numpy() - frame["log_price"].to_numpy()) - 1
    labels = np.where(
        future_return >= threshold,
        2,
        np.where(future_return <= -threshold, 0, 1),
    )
    return (
        frame[list(FEATURE_COLS)].to_numpy(),
        labels,
        frame["date"].to_numpy(),
        frame["market_hash_name"].to_numpy(),
        frame["price"].to_numpy(),
    )


def assert_held_out(explanation_split: str, fit_split: str, allow_in_sample: bool = False):
    trained_splits = {value.strip() for value in str(fit_split).split("+")}
    if explanation_split in trained_splits and not allow_in_sample:
        raise ValueError(
            f"Refusing in-sample SHAP: model fit_split={fit_split}, explanation_split={explanation_split}"
        )

