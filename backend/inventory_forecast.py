"""Aggregate authoritative per-skin forecasts into an inventory value path."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from prediction_service import predict_for_skin


def _dates(anchor: str, horizon: int) -> list[str]:
    start = datetime.fromisoformat(str(anchor)[:10])
    return [(start + timedelta(days=day)).date().isoformat() for day in range(1, horizon + 1)]


def aggregate_inventory_forecast(
    conn,
    *,
    user_id: int,
    loader: Any,
    now: datetime,
    ttl_hours: int,
    circuit_breaker_enabled: bool = True,
) -> dict[str, Any]:
    positions = conn.execute(
        """SELECT s.*, SUM(po.quantity) AS position_quantity
           FROM portfolio po JOIN skins s ON s.id=po.skin_id
           WHERE po.user_id=? AND po.holding_type='real'
           GROUP BY s.id ORDER BY s.id""",
        (user_id,),
    ).fetchall()
    if not positions:
        return {
            "predicted7Dates": [], "predicted7Values": [],
            "trend30Dates": [], "trend30Values": [],
            "forecastAnchorTotal": 0, "predictionCoverage": {
                "totalItems": 0, "predictedItems": 0, "trendItems": 0,
                "itemRatio": 0.0, "valueRatio": 0.0,
            }, "modelVersion": None,
        }

    seven_total = [0.0] * 7
    trend_total = [0.0] * 30
    total_value = predicted_value = anchor_total = 0.0
    predicted_items = trend_items = 0
    decision_dates: list[str] = []
    versions: list[str] = []

    for skin in positions:
        quantity = max(int(skin["position_quantity"] or 0), 0)
        latest = conn.execute(
            "SELECT date, price FROM price_history WHERE skin_id=? ORDER BY date DESC LIMIT 1",
            (skin["id"],),
        ).fetchone()
        if quantity == 0 or latest is None:
            continue
        current = float(latest["price"])
        total_value += current * quantity
        result = predict_for_skin(
            conn, skin, horizon=7, requested_models=None, loader=loader, now=now,
            ttl_hours=ttl_hours, circuit_breaker_enabled=circuit_breaker_enabled,
        )
        prediction = (result.get("predictions") or [None])[0]
        daily = prediction.get("dailyPrices") if isinstance(prediction, dict) else None
        available = result.get("status") == "available" and isinstance(daily, list) and len(daily) == 7
        anchor = float(result.get("forecastAnchorPrice") or current) if available else current
        anchor_total += anchor * quantity
        if available:
            predicted_items += 1
            predicted_value += current * quantity
            for index, value in enumerate(daily):
                seven_total[index] += float(value) * quantity
            decision_dates.append(str(result.get("decisionDate") or latest["date"]))
            if result.get("modelVersion"):
                versions.append(str(result["modelVersion"]))
        else:
            for index in range(7):
                seven_total[index] += current * quantity

        trend = result.get("trend30d") if available else None
        p50 = trend.get("p50") if isinstance(trend, dict) else None
        if isinstance(p50, list) and len(p50) == 30:
            trend_items += 1
            for index, value in enumerate(p50):
                trend_total[index] += float(value) * quantity
        else:
            carry = float(daily[-1]) if available else current
            for index in range(30):
                trend_total[index] += carry * quantity

    anchor_date = max(decision_dates) if decision_dates else str(now.date())
    total_items = len(positions)
    return {
        "predicted7Dates": _dates(anchor_date, 7),
        "predicted7Values": [round(value, 2) for value in seven_total],
        "trend30Dates": _dates(anchor_date, 30),
        "trend30Values": [round(value, 2) for value in trend_total],
        "forecastAnchorTotal": round(anchor_total, 2),
        "predictionCoverage": {
            "totalItems": total_items,
            "predictedItems": predicted_items,
            "trendItems": trend_items,
            "itemRatio": round(predicted_items / total_items, 4) if total_items else 0.0,
            "valueRatio": round(predicted_value / total_value, 4) if total_value else 0.0,
        },
        "modelVersion": versions[0] if versions and len(set(versions)) == 1 else (
            "mixed" if versions else None
        ),
    }

