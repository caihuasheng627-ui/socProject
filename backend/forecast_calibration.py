"""Leakage-free calibration for seven-day and 30-day price paths."""
from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ML_DIR = Path(__file__).resolve().parents[1] / "ml"
if str(ML_DIR) not in sys.path:
    sys.path.insert(0, str(ML_DIR))

from hybrid_v2_transform import rebase_price_path, should_rebase_price_level


MAX_DEVIATION = 0.30
UNCONFIRMED_SHOCK_THRESHOLD = math.log1p(0.35)
CONFIRMED_LEVEL_TOLERANCE = math.log1p(0.15)
UPPER_LOG_LIMIT = math.log1p(MAX_DEVIATION)
LOWER_LOG_LIMIT = abs(math.log1p(-MAX_DEVIATION))
DEFAULT_WEIGHTS = {"c": 0.40, "d": 0.50, "recent": 0.10, "bias": 0.0}


def _positive_finite(value: Any, label: str = "price") -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{label} values must be finite and positive")
    return number


def _price_path(values: Iterable[Any], expected: int, label: str) -> list[float]:
    path = [_positive_finite(value) for value in values]
    if len(path) != expected:
        raise ValueError(f"{label} must contain exactly {expected} prices")
    return path


def recent_price_context(prices: Iterable[Any], current_price: float) -> dict[str, float]:
    """Return robust statistics computed only from observations before prediction."""
    anchor = _positive_finite(current_price, "current_price")
    clean = []
    for value in prices:
        try:
            clean.append(_positive_finite(value))
        except (TypeError, ValueError):
            continue
    clean = clean[-60:]
    if len(clean) < 2:
        return {"dailyTrend": 0.0, "dailyVolatility": 0.005, "observations": len(clean)}

    returns = np.diff(np.log(np.asarray(clean, dtype=np.float64)))
    returns = returns[np.isfinite(returns)]
    if len(returns) == 0:
        return {"dailyTrend": 0.0, "dailyVolatility": 0.005, "observations": len(clean)}

    recent = returns[-14:]
    median = float(np.median(recent))
    mad = float(np.median(np.abs(recent - median)))
    volatility = max(0.005, 1.4826 * mad, float(np.std(returns[-30:])))
    clipped = np.clip(recent, median - 3.0 * volatility, median + 3.0 * volatility)
    weights = np.arange(1, len(clipped) + 1, dtype=np.float64)
    trend = float(np.average(clipped, weights=weights))
    trend = float(np.clip(trend, -0.04, 0.04))
    return {
        "dailyTrend": trend,
        "dailyVolatility": volatility,
        "observations": len(clean),
        "anchor": anchor,
    }


def forecast_anchor_context(
    current_price: float,
    recent_prices: Iterable[Any],
    *,
    lookback: int = 14,
) -> dict[str, Any]:
    """Choose a robust forecast anchor without changing the live market price."""
    current = _positive_finite(current_price, "current_price")
    values: list[float] = []
    for value in recent_prices:
        try:
            values.append(_positive_finite(value))
        except (TypeError, ValueError):
            continue
    if values and math.isclose(values[-1], current, rel_tol=1e-9, abs_tol=1e-6):
        values = values[:-1]
    previous = values[-max(lookback, 2):]
    if len(previous) < 3 or current >= 1000.0:
        return {
            "applied": False,
            "anchor": current,
            "currentPrice": current,
            "referenceMedian": current,
            "reason": None,
        }

    reference = float(np.median(previous))
    previous_jump = abs(math.log(current / previous[-1]))
    reference_jump = abs(math.log(current / reference))
    confirmed = len(previous) >= 2 and all(
        abs(math.log(value / current)) <= CONFIRMED_LEVEL_TOLERANCE
        for value in previous[-2:]
    )
    applied = (
        not confirmed
        and previous_jump >= UNCONFIRMED_SHOCK_THRESHOLD
        and reference_jump >= UNCONFIRMED_SHOCK_THRESHOLD
    )
    return {
        "applied": applied,
        "anchor": round(reference if applied else current, 4),
        "currentPrice": current,
        "referenceMedian": round(reference, 4),
        "reason": "UNCONFIRMED_PRICE_SHOCK" if applied else None,
    }


def _horizon_caps(horizon: int, context: dict[str, float]) -> tuple[float, float]:
    trend = abs(context["dailyTrend"]) * horizon
    support = trend + 2.5 * context["dailyVolatility"] * math.sqrt(horizon)
    minimum_fraction = min(MAX_DEVIATION, 0.025 + 0.01 * horizon)
    up = min(UPPER_LOG_LIMIT, max(math.log1p(minimum_fraction), support))
    down = min(LOWER_LOG_LIMIT, max(abs(math.log1p(-minimum_fraction)), support))
    return down, up


def _smooth_bound(log_return: float, lower_cap: float, upper_cap: float) -> float:
    cap = upper_cap if log_return >= 0 else lower_cap
    if cap <= 0:
        return 0.0
    magnitude = abs(log_return)
    shoulder = cap * 0.75
    if magnitude <= shoulder:
        return log_return
    remaining = cap - shoulder
    bounded = shoulder + remaining * math.tanh((magnitude - shoulder) / remaining)
    return math.copysign(bounded, log_return)


def _adapter_weights(
    adapter: dict[str, Any] | None, horizon: int, price_tier: str
) -> dict[str, float]:
    root = (adapter or {}).get("weights", adapter or {})
    tier = root.get(price_tier) or root.get("global") or {}
    raw = tier.get(str(horizon), DEFAULT_WEIGHTS)
    weights = {
        key: max(0.0, float(raw.get(key, DEFAULT_WEIGHTS[key])))
        for key in ("c", "d", "recent")
    }
    total = sum(weights.values())
    if total <= 0:
        weights = {key: DEFAULT_WEIGHTS[key] for key in ("c", "d", "recent")}
        total = sum(weights.values())
    weights = {key: value / total for key, value in weights.items()}
    weights["bias"] = float(raw.get("bias", 0.0))
    return weights


def calibrate_seven_day(
    current_price: float,
    lstm_c: Iterable[Any],
    lstm_d: Iterable[Any],
    recent_prices: Iterable[Any],
    adapter: dict[str, Any] | None = None,
    price_tier: str = "global",
) -> dict[str, Any]:
    """Blend C/D paths and return one bounded, explainable seven-day forecast."""
    anchor = _positive_finite(current_price, "current_price")
    raw_c_path = _price_path(lstm_c, 7, "seven-day LSTM-C path")
    raw_d_path = _price_path(lstm_d, 7, "seven-day LSTM-D path")
    rebased = should_rebase_price_level(anchor)
    c_path = rebase_price_path(anchor, raw_c_path)
    d_path = rebase_price_path(anchor, raw_d_path)
    context = recent_price_context(recent_prices, anchor)
    calibrated: list[float] = []
    raw_hybrid: list[float] = []
    weights_by_day: dict[str, dict[str, float]] = {}
    disagreements: list[float] = []
    compressed = False
    disagreement_applied = False

    for horizon, (c_price, d_price) in enumerate(zip(c_path, d_path), start=1):
        c_return = math.log(c_price / anchor)
        d_return = math.log(d_price / anchor)
        recent_return = context["dailyTrend"] * horizon
        weights = _adapter_weights(adapter, horizon, price_tier)
        blended = (
            weights["c"] * c_return
            + weights["d"] * d_return
            + weights["recent"] * recent_return
            + weights["bias"]
        )
        raw_hybrid.append(round(anchor * math.exp(blended), 4))

        disagreement = abs(c_return - d_return)
        disagreements.append(disagreement)
        disagreement_floor = max(
            0.08, 3.0 * context["dailyVolatility"] * math.sqrt(horizon)
        )
        excess = max(0.0, disagreement - disagreement_floor)
        reliability = math.exp(-excess / (disagreement_floor + 0.05))
        if excess > 0:
            disagreement_applied = True
        adjusted = recent_return + reliability * (blended - recent_return)

        lower_cap, upper_cap = _horizon_caps(horizon, context)
        bounded = _smooth_bound(adjusted, lower_cap, upper_cap)
        if abs(bounded - blended) > 0.005:
            compressed = True
        price = anchor * math.exp(bounded)
        price = min(anchor * 1.30, max(anchor * 0.70, price))
        calibrated.append(round(price, 4))
        weights_by_day[f"d{horizon}"] = {
            key: round(weights[key], 4) for key in ("c", "d", "recent")
        }

    reasons = []
    if rebased:
        reasons.append("PRICE_LEVEL_REBASE")
    if disagreement_applied:
        reasons.append("MODEL_DISAGREEMENT")
    if abs(context["dailyTrend"]) > 0.0005:
        reasons.append("RECENT_TREND_ADJUSTMENT")
    if compressed:
        reasons.append("SMOOTH_DEVIATION_COMPRESSION")

    return {
        "daily_prices": calibrated,
        "raw_daily_prices": raw_hybrid,
        "calibration": {
            "applied": bool(reasons),
            "method": "hybrid-v2-log-return-smooth-bound",
            "weights": weights_by_day,
            "modelOutputs": {"lstmC": raw_c_path, "lstmD": raw_d_path},
            "adjustedModelOutputs": {"lstmC": c_path, "lstmD": d_path},
            "recentContext": {
                "dailyTrend": round(context["dailyTrend"], 6),
                "dailyVolatility": round(context["dailyVolatility"], 6),
                "observations": int(context["observations"]),
            },
            "modelDisagreement": round(max(disagreements), 6),
            "reasonCodes": reasons,
            "maxDeviation": MAX_DEVIATION,
        },
    }


def calibrate_trend_30d(
    current_price: float,
    trend: dict[str, Any],
    seven_day_prices: Iterable[Any],
    recent_prices: Iterable[Any],
) -> dict[str, Any]:
    """Anchor a 30-day quantile path to calibrated day seven and bound all prices."""
    anchor = _positive_finite(current_price, "current_price")
    seven = _price_path(seven_day_prices, 7, "seven-day calibrated path")
    p10 = _price_path(trend.get("p10", []), 30, "30-day p10 path")
    p50 = _price_path(trend.get("p50", []), 30, "30-day p50 path")
    p90 = _price_path(trend.get("p90", []), 30, "30-day p90 path")
    context = recent_price_context(recent_prices, anchor)

    rebase_factor = 1.0
    if should_rebase_price_level(anchor):
        rebase_factor = anchor / p50[0]
        p10 = [value * rebase_factor for value in p10]
        p50 = [value * rebase_factor for value in p50]
        p90 = [value * rebase_factor for value in p90]

    median_path = list(seven)
    raw_day7_return = math.log(p50[6] / anchor)
    anchor_offset = math.log(seven[-1] / anchor) - raw_day7_return
    for horizon in range(8, 31):
        index = horizon - 1
        raw_return = math.log(p50[index] / anchor)
        decayed_offset = anchor_offset * math.exp(-(horizon - 7) / 10.0)
        adjusted = raw_return + decayed_offset
        if horizon <= 11:
            transition = (horizon - 7) / 4.0
            continuation = math.log(seven[-1] / anchor) + context["dailyTrend"] * (horizon - 7)
            adjusted = (1.0 - transition) * continuation + transition * adjusted
        lower_cap, upper_cap = _horizon_caps(horizon, context)
        bounded = _smooth_bound(adjusted, lower_cap, upper_cap)
        price = min(anchor * 1.30, max(anchor * 0.70, anchor * math.exp(bounded)))
        median_path.append(round(price, 4))

    low_path: list[float] = []
    high_path: list[float] = []
    for index, median in enumerate(median_path):
        raw_median = p50[index]
        low_spread = max(0.0, math.log(raw_median / p10[index]))
        high_spread = max(0.0, math.log(p90[index] / raw_median))
        low = median * math.exp(-min(low_spread, 0.10))
        high = median * math.exp(min(high_spread, 0.10))
        low_path.append(round(max(anchor * 0.70, min(median, low)), 4))
        high_path.append(round(min(anchor * 1.30, max(median, high)), 4))

    output = dict(trend)
    output.update(
        {
            "p10": low_path,
            "p50": [round(value, 4) for value in median_path],
            "p90": high_path,
            "calibration": {
                "applied": True,
                "method": "d7-anchored-log-return-smooth-bound",
                "handoffDay": 7,
                "maxDeviation": MAX_DEVIATION,
                "reasonCodes": (["PRICE_LEVEL_REBASE"] if rebase_factor != 1.0 else [])
                + ["D7_TREND_HANDOFF", "SMOOTH_DEVIATION_COMPRESSION"],
                "priceLevelRebaseFactor": round(rebase_factor, 6),
            },
        }
    )
    return output
