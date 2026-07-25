import math
import subprocess
import sys
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
ML_DIR = BACKEND_DIR.parent / "ml"
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from forecast_calibration import (
    calibrate_seven_day,
    calibrate_trend_30d,
    forecast_anchor_context,
)
from hybrid_v2_transform import rebase_price_path


def _history():
    return [100.0 + math.sin(index * 0.55) * 1.8 + index * 0.08 for index in range(60)]


def test_seven_day_calibration_smoothly_bounds_extreme_model_paths():
    result = calibrate_seven_day(
        current_price=100.0,
        lstm_c=[145, 160, 175, 190, 210, 235, 260],
        lstm_d=[138, 150, 165, 180, 198, 220, 245],
        recent_prices=_history(),
    )

    assert len(result["daily_prices"]) == 7
    assert all(70.0 <= price <= 130.0 for price in result["daily_prices"])
    assert len(set(result["daily_prices"])) == 7
    assert result["daily_prices"][-1] < 130.0
    assert result["calibration"]["applied"] is True
    assert "SMOOTH_DEVIATION_COMPRESSION" in result["calibration"]["reasonCodes"]
    assert result["calibration"]["maxDeviation"] == 0.30


def test_seven_day_calibration_shrinks_large_c_d_disagreement():
    result = calibrate_seven_day(
        current_price=100.0,
        lstm_c=[130, 145, 170, 205, 240, 275, 310],
        lstm_d=[96, 91, 86, 80, 74, 68, 62],
        recent_prices=[100.0] * 60,
        adapter={"global": {str(day): {"c": 0.5, "d": 0.5, "recent": 0.0, "bias": 0.0}
                            for day in range(1, 8)}},
    )

    assert "MODEL_DISAGREEMENT" in result["calibration"]["reasonCodes"]
    assert abs(result["daily_prices"][-1] / 100.0 - 1.0) < 0.10
    assert result["calibration"]["modelDisagreement"] > 0.30
    assert result["calibration"]["weights"]["d7"] == {
        "c": 0.5,
        "d": 0.5,
        "recent": 0.0,
    }


def test_seven_day_calibration_preserves_modest_consensus_path():
    path = [100.5, 101.0, 102.0, 103.0, 104.0, 104.5, 105.0]
    result = calibrate_seven_day(
        current_price=100.0,
        lstm_c=path,
        lstm_d=path,
        recent_prices=[100.0] * 60,
        adapter={"global": {str(day): {"c": 0.5, "d": 0.5, "recent": 0.0, "bias": 0.0}
                            for day in range(1, 8)}},
    )

    assert result["daily_prices"] == path
    assert "SMOOTH_DEVIATION_COMPRESSION" not in result["calibration"]["reasonCodes"]


def test_trend_calibration_anchors_day_seven_and_bounds_every_quantile():
    seven = [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0]
    raw = {
        "decisionDate": "2026-07-22",
        "model": "Keras-Seq2Seq-30D",
        "horizon": 30,
        "p10": [70.0 + index for index in range(30)],
        "p50": [150.0 + index * 4.0 for index in range(30)],
        "p90": [240.0 + index * 8.0 for index in range(30)],
    }

    result = calibrate_trend_30d(
        current_price=100.0,
        trend=raw,
        seven_day_prices=seven,
        recent_prices=_history(),
    )

    assert result["p50"][:7] == seven
    assert result["p50"][6] == result["p50"][6]
    assert abs(result["p50"][7] - result["p50"][6]) < 8.0
    for low, median, high in zip(result["p10"], result["p50"], result["p90"]):
        assert 70.0 <= low <= median <= high <= 130.0
    assert result["calibration"]["handoffDay"] == 7
    assert result["calibration"]["maxDeviation"] == 0.30


def test_calibration_rejects_invalid_or_misaligned_inputs():
    try:
        calibrate_seven_day(100.0, [101.0] * 6, [101.0] * 7, _history())
    except ValueError as exc:
        assert "seven" in str(exc).lower()
    else:
        raise AssertionError("six-day path must be rejected")

    try:
        calibrate_seven_day(0.0, [101.0] * 7, [101.0] * 7, _history())
    except ValueError as exc:
        assert "current_price" in str(exc)
    else:
        raise AssertionError("non-positive anchor must be rejected")


def test_high_price_path_rebases_stale_absolute_level_and_preserves_shape():
    assert rebase_price_path(1800.0, [900.0, 918.0]) == [1800.0, 1836.0]
    assert rebase_price_path(999.99, [900.0, 918.0]) == [900.0, 918.0]

    with pytest.raises(ValueError, match="positive"):
        rebase_price_path(1800.0, [0.0, 918.0])


def test_high_price_calibration_uses_rebased_paths_but_preserves_raw_metadata():
    c_path = [900.0, 904.0, 908.0, 912.0, 916.0, 920.0, 924.0]
    d_path = [850.0, 854.0, 858.0, 862.0, 866.0, 870.0, 874.0]
    adapter = {
        "ultra": {
            str(day): {"c": 0.5, "d": 0.5, "recent": 0.0, "bias": 0.0}
            for day in range(1, 8)
        }
    }
    result = calibrate_seven_day(
        current_price=1800.0,
        lstm_c=c_path,
        lstm_d=d_path,
        recent_prices=[1800.0] * 60,
        adapter=adapter,
        price_tier="ultra",
    )

    assert result["daily_prices"][0] == 1800.0
    assert result["daily_prices"][-1] > 1800.0
    assert all(1260.0 <= value <= 2340.0 for value in result["daily_prices"])
    assert "PRICE_LEVEL_REBASE" in result["calibration"]["reasonCodes"]
    assert result["calibration"]["modelOutputs"] == {
        "lstmC": c_path,
        "lstmD": d_path,
    }
    assert result["calibration"]["adjustedModelOutputs"]["lstmC"][0] == 1800.0
    assert result["calibration"]["adjustedModelOutputs"]["lstmD"][0] == 1800.0


def test_high_price_trend_rebases_quantiles_with_one_factor_and_keeps_handoff():
    raw = {
        "p10": [720.0 + index for index in range(30)],
        "p50": [900.0 + index * 2.0 for index in range(30)],
        "p90": [1080.0 + index * 3.0 for index in range(30)],
    }
    seven = [1800.0 + index * 3.0 for index in range(7)]
    result = calibrate_trend_30d(1800.0, raw, seven, [1800.0] * 60)

    assert result["p50"][:7] == seven
    assert all(1260.0 <= low <= median <= high <= 2340.0
               for low, median, high in zip(result["p10"], result["p50"], result["p90"]))
    assert "PRICE_LEVEL_REBASE" in result["calibration"]["reasonCodes"]
    assert result["calibration"]["priceLevelRebaseFactor"] == 2.0


def test_high_price_single_observation_shock_remains_bounded():
    history = [900.0] * 59 + [1356.02]
    result = calibrate_seven_day(
        1356.02,
        [740.0 + index * 4.0 for index in range(7)],
        [760.0 + index * 3.0 for index in range(7)],
        history,
        price_tier="ultra",
    )

    assert all(1356.02 * 0.70 <= value <= 1356.02 * 1.30
               for value in result["daily_prices"])
    assert result["daily_prices"][-1] < 1356.02 * 1.20


def test_forecast_calibration_imports_without_model_loader_bootstrap():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'backend'); import forecast_calibration",
        ],
        cwd=BACKEND_DIR.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_unconfirmed_m4_style_jump_uses_pre_jump_median_anchor():
    history = [67.0, 68.0, 66.5, 67.4, 67.2, 66.9, 67.3, 67.1, 67.5, 66.8,
               67.2, 67.0, 70.34, 100.84]
    context = forecast_anchor_context(100.84, history)

    assert context["applied"] is True
    assert context["anchor"] == 67.2
    assert context["currentPrice"] == 100.84
    assert context["reason"] == "UNCONFIRMED_PRICE_SHOCK"


def test_unconfirmed_cz_style_jump_uses_pre_jump_median_anchor():
    history = [6.5, 6.7, 6.6, 6.8, 6.7, 6.6, 6.5, 6.8, 6.7, 6.6,
               6.7, 6.6, 6.63, 11.44]
    context = forecast_anchor_context(11.44, history)

    assert context["applied"] is True
    assert context["anchor"] == 6.63


def test_stable_and_confirmed_levels_keep_live_price_anchor():
    stable = forecast_anchor_context(68.0, [67.0, 68.0, 67.5, 68.0, 68.0])
    confirmed = forecast_anchor_context(
        100.0, [66.0, 68.0, 98.0, 101.0, 100.0]
    )
    ultra = forecast_anchor_context(1356.02, [900.0] * 13 + [1356.02])

    assert stable["applied"] is False
    assert stable["anchor"] == 68.0
    assert confirmed["applied"] is False
    assert confirmed["anchor"] == 100.0
    assert ultra["applied"] is False
    assert ultra["anchor"] == 1356.02
