import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


ML_DIR = Path(__file__).resolve().parents[1]
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from train_hybrid_v2 import (
    CONTRACT_VERSION,
    assemble_adapter_samples,
    build_recent_windows,
    chronological_split,
    fit_adapter,
    save_adapter_atomic,
)


def _samples(days=40):
    rows = []
    for day in range(days):
        date = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day)
        for horizon in range(1, 8):
            target = 0.006 * horizon + day * 0.0001
            rows.append(
                {
                    "decision_date": date,
                    "target_date": date + pd.Timedelta(days=horizon),
                    "price_tier": "mid",
                    "horizon": horizon,
                    "c_return": target,
                    "d_return": target + 0.20,
                    "recent_return": 0.0,
                    "target_return": target,
                }
            )
    return pd.DataFrame(rows)


def test_chronological_split_has_strict_non_overlapping_dates():
    splits = chronological_split(_samples())

    assert splits["train"]["decision_date"].max() < splits["val"]["decision_date"].min()
    assert splits["val"]["decision_date"].max() < splits["test"]["decision_date"].min()
    assert splits["train"]["target_date"].max() < splits["val"]["decision_date"].min()
    assert splits["val"]["target_date"].max() < splits["test"]["decision_date"].min()
    assert sum(len(frame) for frame in splits.values()) < len(_samples())


def test_adapter_learns_convex_c_weight_without_using_validation_targets():
    samples = _samples()
    adapter = fit_adapter(samples, grid_step=0.1, min_tier_rows=20)
    d7 = adapter["weights"]["global"]["7"]

    assert d7["c"] >= 0.9
    assert d7["d"] <= 0.1
    assert abs(d7["c"] + d7["d"] + d7["recent"] - 1.0) < 1e-9

    changed = samples.copy()
    mask = changed["decision_date"] > chronological_split(samples)["train"]["decision_date"].max()
    changed.loc[mask, "target_return"] += 5.0
    changed_adapter = fit_adapter(changed, grid_step=0.1, min_tier_rows=20)
    assert changed_adapter["weights"] == adapter["weights"]


def test_adapter_falls_back_to_global_weights_for_small_tier():
    samples = _samples()
    tiny = samples.head(7).copy()
    tiny["price_tier"] = "high"
    adapter = fit_adapter(pd.concat([samples, tiny], ignore_index=True), min_tier_rows=100)

    assert "high" not in adapter["weights"]
    assert adapter["fallbackTier"] == "global"


def test_adapter_artifact_roundtrip_is_atomic_and_complete(tmp_path):
    adapter = fit_adapter(_samples(), grid_step=0.2, min_tier_rows=20)
    destination = tmp_path / "hybrid_v2_adapter.json"
    save_adapter_atomic(adapter, destination)
    loaded = json.loads(destination.read_text(encoding="utf-8"))

    assert loaded["contractVersion"] == CONTRACT_VERSION
    assert loaded["selectionSplit"] == "train"
    assert set(loaded["metrics"]) == {"train", "val", "test"}
    assert set(loaded["baselines"]) == {"LSTM-C", "LSTM-D"}
    assert isinstance(loaded["accepted"], bool)
    for split in loaded["metrics"].values():
        assert np.isfinite(split["mae"])
        assert np.isfinite(split["rmse"])


def test_adapter_publish_rejects_unaccepted_or_malformed_weights(tmp_path):
    destination = tmp_path / "hybrid_v2_adapter.json"
    destination.write_text('{"sentinel": true}', encoding="utf-8")
    adapter = fit_adapter(_samples(), grid_step=0.2, min_tier_rows=20)

    with pytest.raises(ValueError, match="accepted"):
        save_adapter_atomic(dict(adapter, accepted=False), destination)

    malformed = json.loads(json.dumps(adapter))
    malformed["weights"]["global"].pop("7")
    with pytest.raises(ValueError, match="horizon"):
        save_adapter_atomic(malformed, destination)

    assert json.loads(destination.read_text(encoding="utf-8")) == {"sentinel": True}


def test_recent_windows_never_use_future_prices_as_features():
    panel = pd.DataFrame(
        {
            "market_hash_name": ["item-a"] * 7,
            "date": pd.date_range("2026-01-01", periods=7),
            "price": [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0],
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
        }
    )
    windows, targets, metadata = build_recent_windows(
        panel,
        feature_cols=("feature",),
        boundaries=(12.0, 15.0),
        lookback=3,
        horizon=2,
    )

    assert windows.shape == (3, 3, 1)
    assert windows[0, :, 0].tolist() == [1.0, 2.0, 3.0]
    assert targets[0].tolist() == [13.0, 14.0]
    assert metadata.iloc[0]["decision_date"] == pd.Timestamp("2026-01-03")
    assert metadata.iloc[0]["target_dates"] == [
        pd.Timestamp("2026-01-04"),
        pd.Timestamp("2026-01-05"),
    ]
    assert metadata.iloc[0]["current_price"] == 12.0
    assert metadata.iloc[0]["recent_prices"] == [10.0, 11.0, 12.0]


def test_recent_windows_preserve_known_lstm_d_price_tier():
    panel = pd.DataFrame(
        {
            "market_hash_name": ["known-item"] * 5,
            "date": pd.date_range("2026-01-01", periods=5),
            "price": [10.0, 10.0, 10.0, 10.0, 10.0],
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    _windows, _targets, metadata = build_recent_windows(
        panel,
        feature_cols=("feature",),
        boundaries=(12.0, 15.0),
        known_groups={"known-item": "high"},
        lookback=3,
        horizon=2,
    )

    assert set(metadata["price_tier"]) == {"high"}


def test_recent_windows_separate_ultra_adapter_tier_from_high_model_group():
    panel = pd.DataFrame(
        {
            "market_hash_name": ["expensive-item"] * 5,
            "date": pd.date_range("2026-01-01", periods=5),
            "price": [1100.0, 1120.0, 1140.0, 1160.0, 1180.0],
            "feature": [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    _windows, _targets, metadata = build_recent_windows(
        panel,
        feature_cols=("feature",),
        boundaries=(20.0, 100.0),
        known_groups={"expensive-item": "high"},
        lookback=3,
        horizon=2,
    )

    assert set(metadata["model_group"]) == {"high"}
    assert set(metadata["price_tier"]) == {"ultra"}


def test_assemble_samples_converts_model_prices_to_current_anchored_returns():
    metadata = pd.DataFrame(
        {
            "decision_date": [pd.Timestamp("2026-01-03")],
            "target_dates": [[
                pd.Timestamp("2026-01-04"),
                pd.Timestamp("2026-01-05"),
            ]],
            "price_tier": ["mid"],
            "current_price": [100.0],
            "recent_daily_return": [0.01],
        }
    )
    frame = assemble_adapter_samples(
        metadata,
        c_prices=np.array([[110.0, 121.0]]),
        d_prices=np.array([[100.0, 110.0]]),
        target_prices=np.array([[105.0, 115.0]]),
    )

    assert frame["horizon"].tolist() == [1, 2]
    assert np.isclose(frame.iloc[0]["c_return"], np.log(1.10))
    assert np.isclose(frame.iloc[1]["recent_return"], 0.02)
    assert np.isclose(frame.iloc[1]["target_return"], np.log(1.15))


def test_ultra_training_samples_use_same_price_level_rebase_as_runtime():
    metadata = pd.DataFrame(
        {
            "decision_date": [pd.Timestamp("2026-01-03")],
            "target_dates": [[pd.Timestamp("2026-01-04"), pd.Timestamp("2026-01-05")]],
            "price_tier": ["ultra"],
            "model_group": ["high"],
            "current_price": [1800.0],
            "recent_daily_return": [0.0],
        }
    )
    frame = assemble_adapter_samples(
        metadata,
        c_prices=np.array([[900.0, 918.0]]),
        d_prices=np.array([[800.0, 816.0]]),
        target_prices=np.array([[1810.0, 1820.0]]),
    )

    assert np.isclose(frame.iloc[0]["c_return"], 0.0)
    assert np.isclose(frame.iloc[1]["c_return"], np.log(1.02))
    assert np.isclose(frame.iloc[1]["d_return"], np.log(1.02))


def test_adapter_learns_complete_ultra_weights_when_training_has_enough_rows():
    samples = _samples(60)
    samples["price_tier"] = "ultra"
    adapter = fit_adapter(samples, grid_step=0.2, min_tier_rows=20)

    assert set(adapter["weights"]["ultra"]) == {str(day) for day in range(1, 8)}
