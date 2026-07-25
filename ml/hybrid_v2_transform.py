"""Shared price-level transforms for Hybrid V2 training and inference."""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


ULTRA_PRICE_THRESHOLD = 1000.0


def _positive_finite(value: Any, label: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} values must be finite and positive")
    return number


def should_rebase_price_level(current_price: Any) -> bool:
    return _positive_finite(current_price, "current_price") >= ULTRA_PRICE_THRESHOLD


def rebase_price_path(current_price: Any, path: Iterable[Any]) -> list[float]:
    """Anchor an ultra-price path at current USD while preserving relative shape."""
    anchor = _positive_finite(current_price, "current_price")
    values = [_positive_finite(value, "path") for value in path]
    if not values:
        raise ValueError("path must not be empty")
    if anchor < ULTRA_PRICE_THRESHOLD:
        return values
    factor = anchor / values[0]
    return [value * factor for value in values]


def rebase_price_matrix(current_prices: Any, paths: Any) -> np.ndarray:
    anchors = np.asarray(current_prices, dtype=float)
    values = np.asarray(paths, dtype=float)
    if values.ndim != 2 or anchors.shape != (values.shape[0],):
        raise ValueError("current prices and paths must align row-by-row")
    if not np.isfinite(anchors).all() or np.any(anchors <= 0):
        raise ValueError("current_price values must be finite and positive")
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("path values must be finite and positive")
    adjusted = values.copy()
    mask = anchors >= ULTRA_PRICE_THRESHOLD
    adjusted[mask] *= (anchors[mask] / adjusted[mask, 0])[:, None]
    return adjusted

