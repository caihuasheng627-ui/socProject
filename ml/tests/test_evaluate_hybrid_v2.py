import sys
from pathlib import Path

import numpy as np


ML_DIR = Path(__file__).resolve().parents[1]
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from evaluate_hybrid_v2 import evaluate_price_paths


def test_evaluation_reports_tail_error_direction_and_outlier_counts():
    current = np.array([100.0, 100.0])
    target = np.array(
        [
            [101, 102, 103, 104, 105, 106, 107],
            [99, 98, 97, 96, 95, 94, 93],
        ],
        dtype=float,
    )
    raw = np.array([[145.0] * 7, [55.0] * 7])
    calibrated = np.array(
        [
            [101, 102, 103, 104, 105, 106, 107],
            [99, 98, 97, 96, 95, 94, 93],
        ],
        dtype=float,
    )

    report = evaluate_price_paths(
        current,
        target,
        {"raw": raw, "calibrated": calibrated},
    )

    assert report["raw"]["over30Count"] == 14
    assert report["calibrated"]["over30Count"] == 0
    assert report["calibrated"]["mae"] == 0.0
    assert report["calibrated"]["directionAccuracy"] == 1.0
    assert report["raw"]["p95ApePct"] > report["calibrated"]["p95ApePct"]


def test_evaluation_does_not_count_exact_thirty_percent_boundary_as_over_limit():
    report = evaluate_price_paths(
        np.array([100.0]),
        np.array([[100.0] * 7]),
        {"bounded": np.array([[130.0] * 7])},
    )

    assert report["bounded"]["over30Count"] == 0
