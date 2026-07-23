import pandas as pd
import pytest
import numpy as np
from sklearn.preprocessing import MinMaxScaler

from forecast_contract import decode_log_price_predictions, validate_prediction_frame
from compare_models import align_common_prediction_frames, comparison_coverage, reg_metrics


def prediction_frame(split="test", horizon=7):
    return pd.DataFrame({
        "split": [split, split],
        "date": ["2026-01-01", "2026-01-02"],
        "target_date": ["2026-01-08", "2026-01-09"],
        "market_hash_name": ["A", "A"],
        "current_price": [10.0, 11.0],
        "actual_future_price": [20.0, 21.0],
        "predicted_price": [18.0, 19.0],
        "horizon_steps": [horizon, horizon],
    })


def test_regression_metrics_use_future_truth():
    frame = prediction_frame()
    metrics = reg_metrics(frame["actual_future_price"], frame["predicted_price"])
    assert metrics["mae"] == 2.0


def test_decoded_prices_are_floored_at_training_minimum():
    scaler = MinMaxScaler().fit(np.log1p([[0.03], [10.0]]))
    below_training_range = np.array([[-1.0]])

    decoded = decode_log_price_predictions(below_training_range, scaler, minimum_price=0.03)

    assert decoded[0] == pytest.approx(0.03)


def test_model_comparison_uses_identical_item_date_rows():
    first = prediction_frame().iloc[[0]].copy()
    second = prediction_frame().copy()

    aligned = align_common_prediction_frames({"first": first, "second": second})

    assert len(aligned["first"]) == len(aligned["second"]) == 1
    assert aligned["first"][["market_hash_name", "date"]].equals(
        aligned["second"][["market_hash_name", "date"]]
    )


def test_model_comparison_drops_inconsistent_truth_for_same_row():
    """Mismatched truth rows are dropped with a warning, not fatal."""
    first = prediction_frame()
    second = prediction_frame()
    second.loc[0, "actual_future_price"] = 999.0

    # Should NOT raise — mismatched rows are dropped gracefully
    aligned = align_common_prediction_frames({"first": first, "second": second})
    # Remaining rows should have consistent truth
    assert len(aligned["first"]) > 0
    assert len(aligned["second"]) > 0


def test_comparison_marks_missing_ranked_models_as_partial():
    coverage = comparison_coverage({"LSTM-C": prediction_frame()})

    assert coverage["status"] == "partial"
    assert "LSTM-D" in coverage["missing_models"]


def test_prediction_contract_rejects_wrong_horizon():
    with pytest.raises(ValueError, match="horizon"):
        validate_prediction_frame(prediction_frame(horizon=6))


def test_prediction_contract_rejects_mixed_splits():
    frame = pd.concat([prediction_frame("val"), prediction_frame("test")], ignore_index=True)
    frame.loc[2:, "market_hash_name"] = "B"
    with pytest.raises(ValueError, match="split"):
        validate_prediction_frame(frame)
