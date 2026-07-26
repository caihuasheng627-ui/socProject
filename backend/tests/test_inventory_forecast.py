import sqlite3
from datetime import datetime

import inventory_forecast


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE skins (
            id INTEGER PRIMARY KEY, slug TEXT, market_hash_name TEXT, source TEXT
        );
        CREATE TABLE portfolio (
            id INTEGER PRIMARY KEY, skin_id INTEGER, holding_type TEXT,
            quantity INTEGER, user_id INTEGER
        );
        CREATE TABLE price_history (
            id INTEGER PRIMARY KEY, skin_id INTEGER, date TEXT, price REAL,
            daily_volume INTEGER
        );
        INSERT INTO skins VALUES (1, 'one', 'One', 'buff');
        INSERT INTO skins VALUES (2, 'two', 'Two', 'buff');
        INSERT INTO portfolio VALUES (1, 1, 'real', 2, 7);
        INSERT INTO portfolio VALUES (2, 2, 'real', 1, 7);
        INSERT INTO price_history VALUES (1, 1, '2026-07-22', 10, 0);
        INSERT INTO price_history VALUES (2, 2, '2026-07-22', 20, 0);
        """
    )
    return conn


def test_inventory_forecast_aggregates_authoritative_paths_and_carries_unavailable(monkeypatch):
    conn = make_conn()

    def fake_predict(db, skin, **kwargs):
        if skin["id"] == 2:
            return {"status": "unavailable", "currentPrice": 20.0}
        return {
            "status": "available",
            "currentPrice": 10.0,
            "forecastAnchorPrice": 9.0,
            "decisionDate": "2026-07-22",
            "modelVersion": "hybrid-v2-test",
            "predictions": [{"dailyPrices": [11, 12, 13, 14, 15, 16, 17]}],
            "trend30d": {"p50": list(range(18, 48))},
        }

    monkeypatch.setattr(inventory_forecast, "predict_for_skin", fake_predict)
    result = inventory_forecast.aggregate_inventory_forecast(
        conn, user_id=7, loader=object(), now=datetime(2026, 7, 23), ttl_hours=6
    )

    assert result["forecastAnchorTotal"] == 38.0
    assert result["predicted7Values"] == [42, 44, 46, 48, 50, 52, 54]
    assert result["trend30Values"][0] == 56
    assert result["trend30Values"][-1] == 114
    assert result["predictionCoverage"]["predictedItems"] == 1
    assert result["predictionCoverage"]["totalItems"] == 2
    assert result["predictionCoverage"]["valueRatio"] == 0.5
    assert result["modelVersion"] == "hybrid-v2-test"

