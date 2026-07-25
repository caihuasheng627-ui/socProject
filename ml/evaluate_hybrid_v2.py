"""Evaluate raw and calibrated Hybrid V2 paths on chronological CS2 holdout data."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from model_features import SEQUENCE_FEATURE_COLS
from train_hybrid_v2 import (
    DEFAULT_ARTIFACT_PATH,
    DEFAULT_DB_PATH,
    DEFAULT_MODEL_DIR,
    build_recent_windows,
    load_lstm_d_routing,
    load_recent_cs2_panel,
    predict_base_paths,
)


BASE_DIR = Path(__file__).resolve().parent
BACKEND_DIR = BASE_DIR.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
from forecast_calibration import calibrate_seven_day


DEFAULT_REPORT_PATH = BASE_DIR / "outputs" / "hybrid_v2_calibration_report.json"


def evaluate_price_paths(
    current_prices: np.ndarray,
    target_prices: np.ndarray,
    paths: dict[str, np.ndarray],
) -> dict[str, dict[str, float | int]]:
    current = np.asarray(current_prices, dtype=float)
    targets = np.asarray(target_prices, dtype=float)
    if targets.ndim != 2 or current.shape != (targets.shape[0],):
        raise ValueError("current prices and target paths are not aligned")
    if not np.isfinite(targets).all() or np.any(targets <= 0):
        raise ValueError("targets must be finite and positive")

    result = {}
    anchor = current[:, None]
    truth_direction = np.sign(targets - anchor)
    for name, values in paths.items():
        predicted = np.asarray(values, dtype=float)
        if predicted.shape != targets.shape:
            raise ValueError(f"{name} path shape does not match targets")
        error = predicted - targets
        absolute = np.abs(error)
        ape = absolute / np.maximum(targets, 0.01) * 100.0
        deviation = np.abs(predicted / anchor - 1.0)
        exceeds_display_limit = deviation > (0.30 + 1e-12)
        direction = np.sign(predicted - anchor)
        result[name] = {
            "rows": int(predicted.size),
            "decisions": int(len(predicted)),
            "mae": float(np.mean(absolute)),
            "rmse": float(np.sqrt(np.mean(np.square(error)))),
            "mapePct": float(np.mean(ape)),
            "p95ApePct": float(np.percentile(ape, 95)),
            "directionAccuracy": float(np.mean(direction == truth_direction)),
            "over30Count": int(np.sum(exceeds_display_limit)),
            "over30Rate": float(np.mean(exceeds_display_limit)),
            "d7Mae": float(np.mean(absolute[:, -1])),
            "d7P95ApePct": float(np.percentile(ape[:, -1], 95)),
        }
    return result


def _write_json_atomic(payload: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".json", dir=destination.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_evaluation(
    *,
    db_path: Path = DEFAULT_DB_PATH,
    model_dir: Path = DEFAULT_MODEL_DIR,
    adapter_path: Path = DEFAULT_ARTIFACT_PATH,
    report_path: Path = DEFAULT_REPORT_PATH,
    days: int = 180,
    batch_size: int = 512,
) -> dict[str, Any]:
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    if adapter.get("accepted") is not True:
        raise ValueError("only an accepted Hybrid V2 adapter may be evaluated")
    boundaries, known_groups = load_lstm_d_routing(model_dir)
    panel = load_recent_cs2_panel(db_path, days=days)
    windows, targets, metadata = build_recent_windows(
        panel,
        feature_cols=SEQUENCE_FEATURE_COLS,
        boundaries=boundaries,
        known_groups=known_groups,
    )
    test_range = adapter["dateRanges"]["test"]
    decision_dates = pd.to_datetime(metadata["decision_date"])
    mask = (
        (decision_dates >= pd.Timestamp(test_range["start"]))
        & (decision_dates <= pd.Timestamp(test_range["end"]))
    ).to_numpy()
    if not mask.any():
        raise ValueError("adapter test range has no matching recent windows")
    windows = windows[mask]
    targets = targets[mask]
    metadata = metadata.loc[mask].reset_index(drop=True)
    c_prices, d_prices = predict_base_paths(
        windows, metadata, model_dir=model_dir, batch_size=batch_size
    )

    raw_hybrid = np.empty_like(c_prices)
    calibrated = np.empty_like(c_prices)
    reason_counts: Counter[str] = Counter()
    for index, row in enumerate(metadata.itertuples(index=False)):
        result = calibrate_seven_day(
            current_price=float(row.current_price),
            lstm_c=c_prices[index],
            lstm_d=d_prices[index],
            recent_prices=row.recent_prices,
            adapter=adapter,
            price_tier=str(row.price_tier),
        )
        raw_hybrid[index] = result["raw_daily_prices"]
        calibrated[index] = result["daily_prices"]
        reason_counts.update(result["calibration"]["reasonCodes"])

    current = metadata["current_price"].to_numpy(dtype=float)
    metrics = evaluate_price_paths(
        current,
        targets,
        {
            "LSTM-C": c_prices,
            "LSTM-D": d_prices,
            "Hybrid-V2-Raw": raw_hybrid,
            "Hybrid-V2-Calibrated": calibrated,
        },
    )
    report = {
        "contractVersion": "hybrid-v2-calibration-evaluation-v1",
        "dataSource": "sqlite-price-history-rolling-180d",
        "split": "test",
        "dateRange": test_range,
        "items": int(metadata["market_hash_name"].nunique()),
        "decisions": int(len(metadata)),
        "metrics": metrics,
        "calibrationReasonCounts": dict(reason_counts),
        "acceptance": {
            "zeroDisplayedOver30": metrics["Hybrid-V2-Calibrated"]["over30Count"] == 0,
            "maeImprovedVsC": metrics["Hybrid-V2-Calibrated"]["mae"] < metrics["LSTM-C"]["mae"],
            "maeImprovedVsD": metrics["Hybrid-V2-Calibrated"]["mae"] < metrics["LSTM-D"]["mae"],
            "p95ImprovedVsRaw": (
                metrics["Hybrid-V2-Calibrated"]["p95ApePct"]
                < metrics["Hybrid-V2-Raw"]["p95ApePct"]
            ),
        },
    }
    _write_json_atomic(report, report_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    parser.add_argument("--adapter", type=Path, default=DEFAULT_ARTIFACT_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--batch-size", type=int, default=512)
    args = parser.parse_args()
    report = run_evaluation(
        db_path=args.db,
        model_dir=args.model_dir,
        adapter_path=args.adapter,
        report_path=args.report,
        days=args.days,
        batch_size=args.batch_size,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
