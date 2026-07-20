import numpy as np
import pandas as pd

from forecast_contract import PREDICTION_COLUMNS, validate_prediction_frame
from make_predictions_trees import build_model_bundle, build_prediction_frame, select_fit_frame


def split_frame(split, date, price):
    return pd.DataFrame({
        "_split": [split],
        "date": [pd.Timestamp(date)],
        "TargetDate": [pd.Timestamp(date) + pd.Timedelta(days=7)],
        "market_hash_name": ["A"],
        "price": [price],
        "TargetPrice": [price + 1.0],
        "Target": [np.log1p(price + 1.0)],
    })


def test_validation_fits_train_only_and_test_fits_train_plus_val():
    splits = {
        "train": split_frame("train", "2026-01-01", 10.0),
        "val": split_frame("val", "2026-01-02", 11.0),
        "test": split_frame("test", "2026-01-03", 12.0),
    }

    val_fit = select_fit_frame(splits, "val")
    test_fit = select_fit_frame(splits, "test")

    assert set(val_fit["_split"]) == {"train"}
    assert set(test_fit["_split"]) == {"train", "val"}


def test_tree_prediction_frame_uses_canonical_future_truth_and_price_floor():
    source = split_frame("test", "2026-01-03", 12.0)

    output = build_prediction_frame(
        source,
        predicted_log_prices=np.array([-1.0]),
        split="test",
        minimum_price=0.03,
    )

    assert list(output.columns) == PREDICTION_COLUMNS
    assert output.loc[0, "actual_future_price"] == 13.0
    assert output.loc[0, "target_date"] == pd.Timestamp("2026-01-10")
    assert output.loc[0, "predicted_price"] == 0.03
    validate_prediction_frame(output)


def test_tree_model_bundle_contains_train_only_encoders_and_feature_order():
    encoders = {"weapon_type_enc": ("weapon_type", object())}
    bundle = build_model_bundle(
        model=object(),
        label="RF",
        params={"n_estimators": 100},
        encoders=encoders,
        minimum_price=0.03,
    )

    assert bundle["encoders"] is encoders
    assert bundle["fit_split"] == "train+val"
    assert bundle["horizon_steps"] == 7
    assert len(bundle["feature_cols"]) == 23
