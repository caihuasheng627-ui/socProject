import pandas as pd
import pytest

from backtest import align_common_prediction_frames, run_backtest


def make_prices(prices, predictions):
    dates = pd.date_range("2026-01-01", periods=len(prices))
    return pd.DataFrame({
        "split": "test",
        "date": dates,
        "target_date": dates + pd.Timedelta(days=7),
        "market_hash_name": "A",
        "current_price": prices,
        "actual_future_price": predictions,
        "predicted_price": predictions,
        "horizon_steps": 7,
    })


def test_backtest_does_not_cap_large_realized_gain():
    frame = make_prices([10.0] * 7 + [40.0], [40.0] * 7 + [1.0])
    _, metrics = run_backtest(frame, capital=100.0, fee=0.0)
    assert metrics["returnPct"] == 300.0
    assert metrics["buy_count"] == 1
    assert metrics["sell_count"] == 1
    assert metrics["trades"] == 2


def test_fee_reduces_backtest_value():
    frame = make_prices([10.0] * 7 + [20.0], [20.0] * 7 + [1.0])
    _, no_fee = run_backtest(frame, capital=100.0, fee=0.0)
    _, with_fee = run_backtest(frame, capital=100.0, fee=0.05)
    assert with_fee["returnPct"] < no_fee["returnPct"]


def test_backtest_rejects_when_no_common_rows_have_matching_contract_values():
    first = make_prices([10.0], [11.0])
    second = make_prices([10.0], [11.0])
    second.loc[0, "actual_future_price"] = 12.0

    with pytest.raises(ValueError, match="no common rows with matching contract values"):
        align_common_prediction_frames({"first": first, "second": second})


def test_backtest_drops_only_mismatched_contract_rows():
    first = make_prices([10.0, 20.0], [11.0, 21.0])
    second = make_prices([10.0, 20.0], [11.0, 21.0])
    second.loc[1, "actual_future_price"] = 999.0

    aligned = align_common_prediction_frames({"first": first, "second": second})

    assert {name: len(frame) for name, frame in aligned.items()} == {
        "first": 1,
        "second": 1,
    }
    assert all(frame.iloc[0]["date"] == pd.Timestamp("2026-01-01")
               for frame in aligned.values())


def test_backtest_drops_rows_with_different_target_dates():
    first = make_prices([10.0, 20.0], [11.0, 21.0])
    second = make_prices([10.0, 20.0], [11.0, 21.0])
    second.loc[1, "target_date"] += pd.Timedelta(days=1)

    aligned = align_common_prediction_frames({"first": first, "second": second})

    assert all(len(frame) == 1 for frame in aligned.values())
    assert all(frame.iloc[0]["target_date"] == pd.Timestamp("2026-01-08")
               for frame in aligned.values())
