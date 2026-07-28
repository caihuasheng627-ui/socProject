"""Validated online prediction orchestration for the public API."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Any

from forecast_calibration import (
    calibrate_seven_day,
    calibrate_trend_30d,
    forecast_anchor_context,
)


FORECAST_CALIBRATION_CONTRACT = "shock-anchor-v1"


def _volume_coverage(conn: sqlite3.Connection, skin_id: int) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT daily_volume FROM price_history WHERE skin_id=? "
        "ORDER BY date DESC LIMIT 60",
        (skin_id,),
    ).fetchall()
    observed = sum(1 for row in rows if row["daily_volume"] not in (None, 0))
    total = len(rows)
    return {
        "windowObservations": total,
        "observed": observed,
        "ratio": round(observed / total, 4) if total else 0.0,
    }


def _recent_prices(conn: sqlite3.Connection, skin_id: int) -> list[float]:
    rows = conn.execute(
        "SELECT price FROM price_history WHERE skin_id=? "
        "ORDER BY date DESC LIMIT 60",
        (skin_id,),
    ).fetchall()
    return [float(row["price"]) for row in reversed(rows)]


def _base_response(
    skin: sqlite3.Row,
    horizon: int,
    current_price: float | None,
    decision_date: str | None,
    model_version: str,
    generated_at: str,
    coverage: dict[str, Any],
) -> dict[str, Any]:
    source = skin["source"] if "source" in skin.keys() else None
    return {
        "skinId": skin["slug"],
        "horizon": horizon,
        "currency": "USD",
        "currentPrice": current_price,
        "forecastAnchorPrice": current_price,
        "currentPriceUsd": current_price,
        "livePriceUsd": current_price,
        "decisionDate": decision_date,
        "dataThrough": decision_date,
        "modelVersion": model_version,
        "priceSource": source,
        "volumeSource": None,
        "volumeCoverage": coverage,
        "generatedAt": generated_at,
        "trend30d": None,
    }


def _prices_aligned(model_price: float, db_price: float) -> bool:
    """Model/DB anchors must match; allow 4dp float noise (not 2¢ rounding drift)."""
    return math.isclose(
        round(float(model_price), 4),
        round(float(db_price), 4),
        rel_tol=0.0,
        abs_tol=1e-4,
    )


def _validated_trend_30d(
    loader: Any,
    market_hash_name: str,
    current_price: float,
    decision_date: str,
    seven_day_prices: list[float],
    recent_prices: list[float],
    forecast_anchor: float,
) -> dict[str, Any] | None:
    predictor = getattr(loader, "predict_live_trend_30d", None)
    if not callable(predictor):
        return None
    try:
        raw = predictor(market_hash_name)
    except Exception:
        return None
    if not isinstance(raw, dict) or raw.get("date") != decision_date:
        return None
    try:
        anchor = float(raw["current_price"])
        horizon = int(raw["horizon"])
        p10 = [float(value) for value in raw["p10"]]
        p50 = [float(value) for value in raw["p50"]]
        p90 = [float(value) for value in raw["p90"]]
    except (KeyError, TypeError, ValueError):
        return None
    if not _prices_aligned(anchor, current_price):
        return None
    if horizon != 30 or any(len(values) != 30 for values in (p10, p50, p90)):
        return None
    if not all(
        math.isfinite(value) and value > 0
        for values in (p10, p50, p90)
        for value in values
    ):
        return None
    if not all(low <= median <= high for low, median, high in zip(p10, p50, p90)):
        return None
    validated = {
        "decisionDate": decision_date,
        "model": str(raw.get("model") or "Keras-Seq2Seq-30D"),
        "horizon": 30,
        "p10": p10,
        "p50": p50,
        "p90": p90,
    }
    try:
        return calibrate_trend_30d(
            forecast_anchor, validated, seven_day_prices, recent_prices
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _attach_trend_30d(
    response: dict[str, Any],
    loader: Any,
    market_hash_name: str,
    current_price: float,
    decision_date: str,
    seven_day_prices: list[float],
    recent_prices: list[float],
    forecast_anchor: float,
) -> dict[str, Any]:
    response["trend30d"] = _validated_trend_30d(
        loader, market_hash_name, current_price, decision_date,
        seven_day_prices, recent_prices, forecast_anchor,
    )
    return response


def _unavailable(base: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        **base,
        "status": "unavailable",
        "reason": reason,
        "warnings": [],
        "predictions": [],
        "consensus": None,
        "entryRange": None,
        "targetPrice": None,
    }


def _equal_weight_adapter() -> dict[str, Any]:
    return {
        "global": {
            str(day): {"c": 0.5, "d": 0.5, "recent": 0.0, "bias": 0.0}
            for day in range(1, 8)
        }
    }


def _available(
    base: dict[str, Any], prediction: dict[str, Any], calibration: dict[str, Any]
) -> dict[str, Any]:
    current = float(base["forecastAnchorPrice"] or base["currentPrice"])
    change = float(prediction["change"])
    # Consensus measures confidence / decisiveness, not bullishness.
    # Old formula `50 + change*8` collapsed to 0% on modest drawdowns.
    confidence = float(prediction.get("confidence") or 0.0)
    if confidence > 0:
        score = round(min(96.0, max(42.0, confidence)), 1)
    else:
        magnitude = min(abs(change), 10.0)
        score = round(min(92.0, max(48.0, 58.0 + magnitude * 2.4)), 1)
    level = (
        "very_high" if score >= 80 else "high" if score >= 65
        else "medium" if score >= 45 else "low"
    )
    return {
        **base,
        "status": "available",
        "reason": None,
        "warnings": [],
        "calibration": calibration,
        "predictions": [prediction],
        "consensus": {"score": score, "level": level},
        "entryRange": {
            "low": round(current * 0.97, 2),
            "high": round(current * 0.99, 2),
        },
        "targetPrice": prediction["price"],
    }


def predict_for_skin(
    conn: sqlite3.Connection,
    skin: sqlite3.Row,
    horizon: int,
    requested_models: list[str] | None,
    loader: Any,
    now: datetime,
    ttl_hours: int,
    circuit_breaker_enabled: bool = True,
) -> dict[str, Any]:
    """Return one fresh database-anchored LSTM prediction or an explicit failure."""
    latest = conn.execute(
        "SELECT date, price FROM price_history WHERE skin_id=? ORDER BY date DESC LIMIT 1",
        (skin["id"],),
    ).fetchone()
    model_version = f"{loader.live_model_version()}-{FORECAST_CALIBRATION_CONTRACT}"
    now_iso = now.isoformat()
    coverage = _volume_coverage(conn, skin["id"])
    current_price = float(latest["price"]) if latest else None
    decision_date = str(latest["date"]) if latest else None
    base = _base_response(
        skin, horizon, current_price, decision_date, model_version, now_iso, coverage
    )

    if latest is None:
        return _unavailable(base, "NO_PRICE_HISTORY")
    if horizon != 7:
        return _unavailable(base, "UNSUPPORTED_HORIZON")
    if requested_models and not any(
        "lstm" in model.lower() for model in requested_models
    ):
        return _unavailable(base, "REQUESTED_MODEL_UNAVAILABLE")

    recent_prices = _recent_prices(conn, skin["id"])
    anchor_context = forecast_anchor_context(current_price, recent_prices)
    forecast_anchor = float(anchor_context["anchor"])
    context_prices = recent_prices[:-1] if anchor_context["applied"] else recent_prices
    base["forecastAnchorPrice"] = round(forecast_anchor, 4)

    cached = conn.execute(
        """SELECT * FROM predictions
           WHERE skin_id=? AND horizon=? AND model='LSTM'
             AND expires_at>? AND decision_date=? AND data_through=?
             AND model_version=? AND ABS(current_price - ?) < 0.000001
           ORDER BY generated_at DESC LIMIT 1""",
        (
            skin["id"], horizon, now_iso, decision_date, decision_date,
            model_version, current_price,
        ),
    ).fetchone()
    if cached is not None:
        try:
            cache_payload = json.loads(cached["daily_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            cache_payload = None
        if isinstance(cache_payload, dict):
            daily = cache_payload.get("dailyPrices")
            raw_daily = cache_payload.get("rawDailyPrices", daily)
            calibration = cache_payload.get("calibration")
        else:
            daily = cache_payload
            raw_daily = cache_payload
            calibration = None
        if isinstance(daily, list) and len(daily) == 7:
            if not isinstance(calibration, dict):
                try:
                    calibrated = calibrate_seven_day(
                        forecast_anchor, daily, daily, context_prices,
                        adapter=_equal_weight_adapter(),
                    )
                except (TypeError, ValueError, OverflowError):
                    calibrated = None
                if calibrated is None:
                    return _unavailable(base, "INVALID_PREDICTION")
                daily = calibrated["daily_prices"]
                raw_daily = calibrated["raw_daily_prices"]
                calibration = calibrated["calibration"]
            predicted = float(daily[-1])
            change = round((predicted - forecast_anchor) / forecast_anchor * 100.0, 2)
            prediction = {
                "model": "LSTM",
                "type": cached["type"] or "DL",
                "price": round(predicted, 2),
                "priceUsd": round(predicted, 2),
                "change": change,
                "confidence": float(cached["confidence"]),
                "decisionDate": decision_date,
                "dailyPrices": daily,
                "rawDailyPrices": raw_daily,
                "routeModel": "Hybrid-V2",
                "forecastAnchorPrice": round(forecast_anchor, 4),
                "marketChange": round((predicted - current_price) / current_price * 100.0, 2),
            }
            base["generatedAt"] = cached["generated_at"]
            return _attach_trend_30d(
                _available(base, prediction, calibration),
                loader,
                skin["market_hash_name"],
                current_price,
                decision_date,
                daily,
                recent_prices,
                forecast_anchor,
            )

    ensemble_predictor = getattr(loader, "predict_live_ensemble", None)
    raw = (
        ensemble_predictor(skin["market_hash_name"])
        if callable(ensemble_predictor)
        else loader.predict_live_lstm(skin["market_hash_name"])
    )
    if raw is None:
        return _unavailable(base, "MODEL_UNAVAILABLE")
    if raw.get("date") != decision_date:
        return _unavailable(base, "STALE_INPUT")
    try:
        anchor = float(raw["current_price"])
        if "lstm_c_prices" in raw and "lstm_d_prices" in raw:
            c_path = [float(value) for value in raw["lstm_c_prices"]]
            d_path = [float(value) for value in raw["lstm_d_prices"]]
        else:
            legacy_path = [float(value) for value in raw["daily_prices"]]
            c_path = legacy_path
            d_path = legacy_path
    except (KeyError, TypeError, ValueError):
        return _unavailable(base, "INVALID_PREDICTION")
    if not _prices_aligned(anchor, current_price):
        return _unavailable(base, "PRICE_ANCHOR_MISMATCH")
    if (
        len(c_path) != 7
        or len(d_path) != 7
        or not all(
            math.isfinite(value) and value > 0 for value in [*c_path, *d_path]
        )
    ):
        return _unavailable(base, "INVALID_PREDICTION")

    try:
        calibrated = calibrate_seven_day(
            current_price=forecast_anchor,
            lstm_c=c_path,
            lstm_d=d_path,
            recent_prices=context_prices,
            adapter=raw.get("adapter") or _equal_weight_adapter(),
            price_tier=str(raw.get("price_tier") or "global"),
        )
    except (TypeError, ValueError, OverflowError):
        return _unavailable(base, "INVALID_PREDICTION")
    daily = calibrated["daily_prices"]
    predicted = float(daily[-1])
    calibration = calibrated["calibration"]
    fallback_models = raw.get("fallbackModels") or []
    if fallback_models:
        calibration["fallbackModels"] = list(fallback_models)

    change = round((predicted - forecast_anchor) / forecast_anchor * 100.0, 2)
    confidence = float(raw.get("confidence", 0.0))
    prediction = {
        "model": "LSTM",
        "type": "DL",
        "price": round(predicted, 2),
        "priceUsd": round(predicted, 2),
        "change": change,
        "confidence": confidence,
        "decisionDate": decision_date,
        "dailyPrices": daily,
        "rawDailyPrices": calibrated["raw_daily_prices"],
        "routeModel": "Hybrid-V2",
        "forecastAnchorPrice": round(forecast_anchor, 4),
        "marketChange": round((predicted - current_price) / current_price * 100.0, 2),
    }
    calibration["forecastAnchorPrice"] = round(forecast_anchor, 4)
    calibration["currentPrice"] = round(current_price, 4)
    if anchor_context["applied"]:
        calibration["applied"] = True
        calibration["reasonCodes"] = list(dict.fromkeys(
            [anchor_context["reason"], *calibration.get("reasonCodes", [])]
        ))
        calibration["anchorReferenceMedian"] = anchor_context["referenceMedian"]
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat()
    conn.execute(
        """INSERT INTO predictions(
               skin_id, horizon, model, type, predicted_price, current_price,
               change_pct, confidence, generated_at, expires_at, daily_json,
               decision_date, model_version, data_through
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            skin["id"], horizon, "LSTM", "DL", predicted, current_price,
            change, confidence, now_iso, expires_at, json.dumps({
                "dailyPrices": daily,
                "rawDailyPrices": calibrated["raw_daily_prices"],
                "calibration": calibration,
                "forecastAnchorPrice": round(forecast_anchor, 4),
            }),
            decision_date, model_version, decision_date,
        ),
    )
    conn.commit()
    return _attach_trend_30d(
        _available(base, prediction, calibration),
        loader,
        skin["market_hash_name"],
        current_price,
        decision_date,
        daily,
        recent_prices,
        forecast_anchor,
    )
