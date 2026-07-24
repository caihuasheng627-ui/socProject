"""Daily aggregation and conservative isolated-price cleanup."""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable


PATTERN_SENSITIVE_KEYWORDS = (
    "case hardened",
    "heat treated",
    "fade",
    "doppler",
)


@dataclass(frozen=True)
class CleanedPricePoint:
    date: str
    price: float
    raw_price: float
    is_outlier: bool = False
    outlier_reason: str | None = None


def aggregate_daily_prices(rows: Iterable[tuple[str, float]]) -> list[tuple[str, float]]:
    """Reduce multiple observations per date with the median price."""
    by_date: dict[str, list[float]] = {}
    for date_str, price in rows:
        value = float(price)
        if not math.isfinite(value) or value <= 0:
            continue
        by_date.setdefault(str(date_str), []).append(value)
    return [(date_str, float(median(by_date[date_str]))) for date_str in sorted(by_date)]


def clean_price_points(
    market_hash_name: str,
    rows: Iterable[tuple[str, float]],
    *,
    ordinary_threshold: float = 5.0,
    protected_threshold: float = 10.0,
    neighbour_ratio_limit: float = 1.25,
) -> list[CleanedPricePoint]:
    """Replace only clear isolated spikes whose two neighbours agree."""
    ordered = sorted((str(date_str), float(price)) for date_str, price in rows)
    if not ordered:
        return []

    raw_prices = [price for _, price in ordered]
    series_median = float(median(raw_prices))
    lowered_name = (market_hash_name or "").casefold()
    is_pattern_sensitive = any(
        keyword in lowered_name for keyword in PATTERN_SENSITIVE_KEYWORDS
    )
    threshold = (
        protected_threshold
        if is_pattern_sensitive or series_median < 1.0
        else ordinary_threshold
    )

    cleaned = [
        CleanedPricePoint(date=date_str, price=price, raw_price=price)
        for date_str, price in ordered
    ]
    candidates: dict[int, float] = {}
    for index in range(1, len(ordered) - 1):
        previous = raw_prices[index - 1]
        current = raw_prices[index]
        following = raw_prices[index + 1]
        if min(previous, current, following) <= 0:
            continue

        neighbour_ratio = max(previous / following, following / previous)
        if neighbour_ratio > neighbour_ratio_limit:
            continue

        neighbour_price = math.sqrt(previous * following)
        deviation = max(current / neighbour_price, neighbour_price / current)
        if deviation < threshold:
            continue

        candidates[index] = neighbour_price

    for index, neighbour_price in candidates.items():
        if index - 1 in candidates or index + 1 in candidates:
            continue
        cleaned[index] = CleanedPricePoint(
            date=ordered[index][0],
            price=neighbour_price,
            raw_price=raw_prices[index],
            is_outlier=True,
            outlier_reason="isolated_price_spike",
        )
    return cleaned
