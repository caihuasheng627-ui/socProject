import numpy as np
import pandas as pd

from forecast_contract import add_grouped_targets


def test_grouped_targets_use_seventh_later_observation_without_crossing_items():
    dates = pd.to_datetime(
        ["2026-01-01", "2026-01-02", "2026-01-03", "2026-01-05",
         "2026-01-06", "2026-01-08", "2026-01-09", "2026-01-12"]
    )
    panel = pd.concat(
        [
            pd.DataFrame({
                "market_hash_name": name,
                "date": dates,
                "price": np.arange(start, start + 8, dtype=float),
                "_split": "train",
            })
            for name, start in (("A", 10), ("B", 100))
        ],
        ignore_index=True,
    )
    panel["log_price"] = np.log1p(panel["price"])

    out = add_grouped_targets(panel)

    first_a = out[out["market_hash_name"] == "A"].iloc[0]
    first_b = out[out["market_hash_name"] == "B"].iloc[0]
    assert first_a["TargetDate"] == pd.Timestamp("2026-01-12")
    assert first_a["TargetPrice"] == 17.0
    assert np.isclose(first_a["Target"], np.log1p(17.0))
    assert first_b["TargetPrice"] == 107.0
    assert out.groupby("market_hash_name")["TargetPrice"].count().to_dict() == {"A": 1, "B": 1}


def test_grouped_targets_record_target_split():
    panel = pd.DataFrame({
        "market_hash_name": ["A"] * 8,
        "date": pd.date_range("2026-01-01", periods=8),
        "price": np.arange(8, dtype=float) + 1,
        "_split": ["train"] * 7 + ["val"],
    })
    panel["log_price"] = np.log1p(panel["price"])

    out = add_grouped_targets(panel)

    assert out.iloc[0]["_target_split"] == "val"

