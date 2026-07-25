"""Validated online prediction orchestration for the public API."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from typing import Any


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


def _validated_trend_30d(
    loader: Any,
    market_hash_name: str,
    current_price: float,
    decision_date: str,
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
    if not math.isclose(anchor, current_price, rel_tol=1e-9, abs_tol=1e-6):
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
    p10 = [round(max(low, median * 0.60), 4) for low, median in zip(p10, p50)]
    p50 = [round(median, 4) for median in p50]
    p90 = [round(min(high, median * 1.40), 4) for median, high in zip(p50, p90)]
    return {
        "decisionDate": decision_date,
        "model": str(raw.get("model") or "Keras-Seq2Seq-30D"),
        "horizon": 30,
        "p10": p10,
        "p50": p50,
        "p90": p90,
    }


def _attach_trend_30d(
    response: dict[str, Any],
    loader: Any,
    market_hash_name: str,
    current_price: float,
    decision_date: str,
) -> dict[str, Any]:
    response["trend30d"] = _validated_trend_30d(
        loader, market_hash_name, current_price, decision_date
    )
    return response


def _unavailable_with_trend(
    base: dict[str, Any],
    reason: str,
    loader: Any,
    market_hash_name: str,
    current_price: float,
    decision_date: str,
) -> dict[str, Any]:
    """Keep the seven-day failure explicit while exposing independent trend output."""
    response = _unavailable(base, reason)
    return _attach_trend_30d(
        response, loader, market_hash_name, current_price, decision_date
    )


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


def _available(
    base: dict[str, Any], prediction: dict[str, Any], warnings: list[str] | None = None
) -> dict[str, Any]:
    current = float(base["currentPrice"])
    change = float(prediction["change"])
    score = round(min(100.0, max(0.0, 50.0 + change * 8.0)), 1)
    level = (
        "very_high" if score >= 80 else "high" if score >= 65
        else "medium" if score >= 45 else "low"
    )
    return {
        **base,
        "status": "available",
        "reason": None,
        "warnings": warnings or [],
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
    model_version = loader.live_model_version()
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
            daily = json.loads(cached["daily_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            daily = None
        if isinstance(daily, list) and len(daily) == 7:
            prediction = {
                "model": "LSTM",
                "type": cached["type"] or "DL",
                "price": float(cached["predicted_price"]),
                "priceUsd": float(cached["predicted_price"]),
                "change": float(cached["change_pct"]),
                "confidence": float(cached["confidence"]),
                "decisionDate": decision_date,
                "dailyPrices": daily,
            }
            base["generatedAt"] = cached["generated_at"]
            out_of_range = any(
                abs(float(value) / current_price - 1.0) > 0.30
                for value in [cached["predicted_price"], *daily]
            )
            if out_of_range and circuit_breaker_enabled:
                return _unavailable_with_trend(
                    base,
                    "PREDICTION_OUT_OF_RANGE",
                    loader,
                    skin["market_hash_name"],
                    current_price,
                    decision_date,
                )
            warnings = ["PREDICTION_OUT_OF_RANGE"] if out_of_range else []
            return _attach_trend_30d(
                _available(base, prediction, warnings),
                loader,
                skin["market_hash_name"],
                current_price,
                decision_date,
            )

    raw = loader.predict_live_lstm(skin["market_hash_name"])
    if raw is None:
        return _unavailable(base, "MODEL_UNAVAILABLE")
    if raw.get("date") != decision_date:
        return _unavailable(base, "STALE_INPUT")
    try:
        anchor = float(raw["current_price"])
        predicted = float(raw["predicted_price"])
        daily = [float(value) for value in raw["daily_prices"]]
    except (KeyError, TypeError, ValueError):
        return _unavailable(base, "INVALID_PREDICTION")
    if not math.isclose(anchor, current_price, rel_tol=1e-9, abs_tol=1e-6):
        return _unavailable(base, "PRICE_ANCHOR_MISMATCH")
    if (
        len(daily) != 7
        or not all(math.isfinite(value) and value > 0 for value in [predicted, *daily])
    ):
        return _unavailable(base, "INVALID_PREDICTION")
    out_of_range = any(
        abs(value / current_price - 1.0) > 0.30 for value in [predicted, *daily]
    )
    if out_of_range and circuit_breaker_enabled:
        return _unavailable_with_trend(
            base,
            "PREDICTION_OUT_OF_RANGE",
            loader,
            skin["market_hash_name"],
            current_price,
            decision_date,
        )

    change = round((predicted - current_price) / current_price * 100.0, 2)
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
        "routeModel": raw.get("model"),
    }
    expires_at = (now + timedelta(hours=ttl_hours)).isoformat()
    conn.execute(
        """INSERT INTO predictions(
               skin_id, horizon, model, type, predicted_price, current_price,
               change_pct, confidence, generated_at, expires_at, daily_json,
               decision_date, model_version, data_through
           ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            skin["id"], horizon, "LSTM", "DL", predicted, current_price,
            change, confidence, now_iso, expires_at, json.dumps(daily),
            decision_date, model_version, decision_date,
        ),
    )
    conn.commit()
    warnings = ["PREDICTION_OUT_OF_RANGE"] if out_of_range else []
    return _attach_trend_30d(
        _available(base, prediction, warnings),
        loader,
        skin["market_hash_name"],
        current_price,
        decision_date,
    )
