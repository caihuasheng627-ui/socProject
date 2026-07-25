import json

import numpy as np
import pandas as pd
import pytest

from preflight_training import (
    audit_split_frames,
    validate_feature_matrix,
    validate_smoke_report,
)


def _split_frame(item, dates):
    return pd.DataFrame({
        "market_hash_name": item,
        "date": pd.to_datetime(dates),
        "price": np.arange(len(dates), dtype=float) + 1.0,
    })


def test_audit_split_frames_accepts_per_item_chronological_splits():
    frames = {
        "train": _split_frame("A", ["2020-01-01", "2020-01-02"]),
        "val": _split_frame("A", ["2020-01-03", "2020-01-04"]),
        "test": _split_frame("A", ["2020-01-05", "2020-01-06"]),
    }

    report = audit_split_frames(frames)

    assert report["status"] == "passed"
    assert report["splits"]["train"]["rows"] == 2
    assert report["items"] == 1


def test_audit_split_frames_rejects_duplicate_or_overlapping_item_dates():
    frames = {
        "train": _split_frame("A", ["2020-01-01", "2020-01-02"]),
        "val": _split_frame("A", ["2020-01-02", "2020-01-03"]),
        "test": _split_frame("A", ["2020-01-04"]),
    }

    with pytest.raises(ValueError, match="overlap|chronological"):
        audit_split_frames(frames)


def test_validate_feature_matrix_rejects_nonfinite_and_volume_columns():
    with pytest.raises(ValueError, match="volume"):
        validate_feature_matrix(np.ones((2, 1)), ("daily_volume",), "sequence")

    with pytest.raises(ValueError, match="finite"):
        validate_feature_matrix(
            np.array([[1.0, np.nan]]), ("log_price", "MA_7"), "sequence"
        )


def test_validate_smoke_report_requires_every_architecture_and_csv_roundtrip():
    report = {
        "keras": {name: {"status": "passed"} for name in (
            "lstm_c", "lstm_d", "gru", "trend_30d"
        )},
        "trees": {name: {"status": "passed"} for name in (
            "rf_reg", "lightgbm_reg", "xgboost_reg",
            "rf_cls", "lightgbm_cls", "xgboost_cls",
        )},
        "csv": {"forecast_7d": "passed", "trend_30d": "passed"},
    }

    assert validate_smoke_report(report)["status"] == "passed"

    del report["trees"]["xgboost_cls"]
    with pytest.raises(ValueError, match="xgboost_cls"):
        validate_smoke_report(report)
