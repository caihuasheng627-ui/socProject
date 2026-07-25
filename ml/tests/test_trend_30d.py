import numpy as np
import pandas as pd
import json

from evaluate_trend_30d import evaluate_trend_arrays
from make_predictions import build_trend_prediction_frame, sanitize_trend_quantiles
from evaluate_trend_30d import save_split_metrics


def test_sanitize_trend_quantiles_orders_positive_bands_and_caps_width():
    raw = np.array([
        [[-2.0, 10.0, 30.0], [16.0, 12.0, 4.0]],
    ])

    clean = sanitize_trend_quantiles(raw, minimum_price=0.01, max_band_fraction=0.40)

    assert clean.shape == raw.shape
    assert np.isfinite(clean).all()
    assert (clean > 0).all()
    assert (clean[:, :, 0] <= clean[:, :, 1]).all()
    assert (clean[:, :, 1] <= clean[:, :, 2]).all()
    assert (clean[:, :, 0] >= clean[:, :, 1] * 0.60 - 1e-9).all()
    assert (clean[:, :, 2] <= clean[:, :, 1] * 1.40 + 1e-9).all()
    assert clean[0, 0, 1] == 10.0


def test_build_trend_frame_exports_metadata_actuals_quantiles_and_deduplicates():
    metadata = pd.DataFrame({
        "split": ["val", "val"],
        "date": pd.to_datetime(["2026-01-01", "2026-01-01"]),
        "target_date": pd.to_datetime(["2026-01-31", "2026-01-31"]),
        "market_hash_name": ["A", "A"],
        "current_price": [10.0, 10.0],
        "actual_future_price": [12.0, 12.0],
        "horizon_steps": [30, 30],
        **{f"actual_future_price_d{day}": [10.0 + day, 10.0 + day]
           for day in range(1, 31)},
    })
    prediction = np.tile(np.array([[[9.0, 10.0, 11.0]]]), (2, 30, 1))

    output = build_trend_prediction_frame(
        metadata,
        prediction,
        model_version="seq2seq-keras-test",
    )

    assert len(output) == 1
    assert output.loc[0, "horizon_steps"] == 30
    assert output.loc[0, "model_name"] == "Seq2Seq-30D-Quantile"
    assert output.loc[0, "model_version"] == "seq2seq-keras-test"
    for day in range(1, 31):
        assert output.loc[0, f"actual_future_price_d{day}"] == 10.0 + day
        assert output.loc[0, f"trend_p10_d{day}"] == 9.0
        assert output.loc[0, f"trend_p50_d{day}"] == 10.0
        assert output.loc[0, f"trend_p90_d{day}"] == 11.0


def test_evaluate_trend_arrays_reports_overall_and_checkpoint_metrics():
    truth = np.tile(np.arange(1.0, 31.0), (2, 1))
    p50 = truth + 1.0
    prediction = np.stack([truth - 1.0, p50, truth + 2.0], axis=-1)

    metrics = evaluate_trend_arrays(truth, prediction)

    assert metrics["overall"]["mae"] == 1.0
    assert metrics["overall"]["rmse"] == 1.0
    assert metrics["overall"]["coverage"] == 1.0
    assert metrics["overall"]["crossing_rate"] == 0.0
    assert set(metrics["horizons"]) == {"d7", "d14", "d21", "d30"}
    assert all("r2" in block for block in metrics["horizons"].values())


def test_save_split_metrics_uses_outputs_contract(tmp_path):
    metrics = {"split": "val", "rows": 3}

    path = save_split_metrics(metrics, "val", output_dir=tmp_path)

    assert path == tmp_path / "trend_30d_results_val.json"
    assert json.loads(path.read_text(encoding="utf-8")) == metrics
