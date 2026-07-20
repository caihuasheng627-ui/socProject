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


def test_backtest_rejects_inconsistent_contract_values_for_same_row():
    first = make_prices([10.0], [11.0])
    second = make_prices([10.0], [11.0])
    second.loc[0, "actual_future_price"] = 12.0

    with pytest.raises(ValueError, match="contract values"):
        align_common_prediction_frames({"first": first, "second": second})
