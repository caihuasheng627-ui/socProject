import numpy as np
import pandas as pd

from forecast_contract import build_sequence_windows


def test_sequence_includes_decision_row_and_has_exact_lookback():
    panel = pd.DataFrame({
        "market_hash_name": ["A"] * 61,
        "date": pd.date_range("2026-01-01", periods=61),
        "feature": np.arange(61, dtype=float),
        "Target": np.arange(61, dtype=float) + 100,
        "TargetDate": pd.date_range("2026-01-08", periods=61),
        "TargetPrice": np.arange(61, dtype=float) + 200,
        "_split": "val",
        "_target_split": "val",
        "price": np.arange(61, dtype=float) + 10,
    })

    x, y, meta = build_sequence_windows(panel, ["feature"], 60, sample_split="val")

    assert x.shape == (2, 60, 1)
    assert x[0, :, 0].tolist() == list(range(60))
    assert y[0] == 159.0
    assert meta.iloc[0]["date"] == panel.iloc[59]["date"]
    assert meta.iloc[0]["current_price"] == panel.iloc[59]["price"]


def test_sequence_requires_target_in_same_split():
    panel = pd.DataFrame({
        "market_hash_name": ["A"] * 4,
        "date": pd.date_range("2026-01-01", periods=4),
        "feature": np.arange(4, dtype=float),
        "Target": np.arange(4, dtype=float),
        "TargetDate": pd.date_range("2026-01-08", periods=4),
        "TargetPrice": np.arange(4, dtype=float) + 10,
        "_split": ["train", "train", "val", "val"],
        "_target_split": ["train", "val", "val", "test"],
        "price": np.arange(4, dtype=float) + 1,
    })

    x, _, meta = build_sequence_windows(panel, ["feature"], 2, sample_split="val")

    assert len(x) == 1
    assert meta.iloc[0]["date"] == panel.iloc[2]["date"]

