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

# 末端点用近期窗口中位数校验时,至少需要多少个前序/后序有效点
ENDPOINT_MIN_WINDOW = 3
ENDPOINT_LOOKBACK = 14


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


def _threshold_for(
    market_hash_name: str,
    series_median: float,
    *,
    ordinary_threshold: float,
    protected_threshold: float,
) -> float:
    lowered_name = (market_hash_name or "").casefold()
    is_pattern_sensitive = any(
        keyword in lowered_name for keyword in PATTERN_SENSITIVE_KEYWORDS
    )
    if is_pattern_sensitive or series_median < 1.0:
        return protected_threshold
    return ordinary_threshold


def _repair_endpoint(
    cleaned: list[CleanedPricePoint],
    *,
    index: int,
    window_prices: list[float],
    threshold: float,
    neighbour_ratio_limit: float,
) -> None:
    """Replace a first/last spike when the rest of the local window is stable."""
    if len(window_prices) < ENDPOINT_MIN_WINDOW:
        return
    current = cleaned[index].raw_price
    if current <= 0:
        return
    ref = float(median(window_prices))
    if ref <= 0:
        return
    deviation = max(current / ref, ref / current)
    if deviation < threshold:
        return

    neighbour_index = index - 1 if index == len(cleaned) - 1 else index + 1
    neighbour = cleaned[neighbour_index].price
    if neighbour <= 0:
        return
    # 邻点应与窗口中位数同量级;否则更像趋势拐头,留给人工
    neighbour_ratio = max(neighbour / ref, ref / neighbour)
    if neighbour_ratio > neighbour_ratio_limit:
        return

    cleaned[index] = CleanedPricePoint(
        date=cleaned[index].date,
        price=ref,
        raw_price=current,
        is_outlier=True,
        outlier_reason="endpoint_price_spike",
    )


def clean_price_points(
    market_hash_name: str,
    rows: Iterable[tuple[str, float]],
    *,
    ordinary_threshold: float = 2.0,
    protected_threshold: float = 5.0,
    neighbour_ratio_limit: float = 1.25,
    endpoint_threshold: float | None = None,
) -> list[CleanedPricePoint]:
    """Replace clear isolated spikes; also repair first/last endpoint spikes.

    阈值设计（经验验证）：
      - 普通饰品 2.0x：可捕获 Black Laminate MW $284→$132 类单日尖刺
      - 特殊图案/低价 5.0x：淬火/多普勒等真实图案溢价可达 3-4x
      - 邻点一致性 ≤1.25x：确保前后两点在同一量级，排除趋势转折误伤
      - 末端点单独用 ordinary 阈值(默认 2.0x)对照近期中位数：
        单日冲高写入 current_price 会污染预测;真实图案溢价通常会连续多日留存,
        仍由序列中部的 5x 规则保护
    """
    ordered = sorted((str(date_str), float(price)) for date_str, price in rows)
    if not ordered:
        return []

    raw_prices = [price for _, price in ordered]
    series_median = float(median(raw_prices))
    threshold = _threshold_for(
        market_hash_name,
        series_median,
        ordinary_threshold=ordinary_threshold,
        protected_threshold=protected_threshold,
    )
    end_threshold = (
        ordinary_threshold if endpoint_threshold is None else endpoint_threshold
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

    if len(cleaned) >= ENDPOINT_MIN_WINDOW + 1:
        prior = [point.price for point in cleaned[:-1] if point.price > 0]
        _repair_endpoint(
            cleaned,
            index=len(cleaned) - 1,
            window_prices=prior[-ENDPOINT_LOOKBACK:],
            threshold=end_threshold,
            neighbour_ratio_limit=neighbour_ratio_limit,
        )
        following = [point.price for point in cleaned[1:] if point.price > 0]
        _repair_endpoint(
            cleaned,
            index=0,
            window_prices=following[:ENDPOINT_LOOKBACK],
            threshold=end_threshold,
            neighbour_ratio_limit=neighbour_ratio_limit,
        )

    return cleaned
