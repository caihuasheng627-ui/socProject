import json
import sqlite3
from datetime import datetime, timezone

from prediction_service import FORECAST_CALIBRATION_CONTRACT, predict_for_skin


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE skins (
            id INTEGER PRIMARY KEY, slug TEXT, market_hash_name TEXT,
            source TEXT
        );
        CREATE TABLE price_history (
            id INTEGER PRIMARY KEY, skin_id INTEGER, date TEXT, price REAL,
            daily_volume INTEGER
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, skin_id INTEGER, horizon INTEGER,
            model TEXT, type TEXT, predicted_price REAL, current_price REAL,
            change_pct REAL, confidence REAL, generated_at TEXT,
            expires_at TEXT, daily_json TEXT, decision_date TEXT,
            model_version TEXT, data_through TEXT
        );
        INSERT INTO skins VALUES (1, 'test-skin', 'Test Skin', 'buff');
        INSERT INTO price_history VALUES
            (1, 1, '2026-07-21', 98.0, 0),
            (2, 1, '2026-07-22', 100.0, 0);
        """
    )
    return conn


class FakeLoader:
    def __init__(self, result, trend_result=None):
        self.result = result
        self.trend_result = trend_result
        self.live_calls = 0
        self.trend_calls = 0

    def live_model_version(self):
        return "lstm-test-v1"

    def predict_live_lstm(self, name):
        self.live_calls += 1
        return self.result

    def predict_live_ensemble(self, name):
        self.live_calls += 1
        if self.result is None:
            return None
        return {
            "current_price": self.result["current_price"],
            "date": self.result["date"],
            "model": "Hybrid-V2",
            "confidence": self.result["confidence"],
            "price_tier": "global",
            "lstm_c_prices": self.result["daily_prices"],
            "lstm_d_prices": self.result["daily_prices"],
            "adapter": {
                "global": {
                    str(day): {"c": 0.5, "d": 0.5, "recent": 0.0, "bias": 0.0}
                    for day in range(1, 8)
                }
            },
        }

    def predict_live_trend_30d(self, name):
        self.trend_calls += 1
        return self.trend_result

    def predict_all_models(self, name, horizon):
        raise AssertionError("offline prediction adapter was called")


def live_result(price=105.0, date="2026-07-22", current=100.0):
    daily = [100.5, 101.0, 102.0, 103.0, 104.0, 104.5, price]
    return {
        "current_price": current,
        "predicted_price": price,
        "daily_prices": daily,
        "model": "LSTM-C(__UNK__)",
        "date": date,
        "change_pct": round((price - current) / current * 100, 2),
        "confidence": 73.6,
    }


def trend_result(date="2026-07-22", current=100.0, p50=105.0):
    return {
        "current_price": current,
        "date": date,
        "model": "Keras-Seq2Seq-30D",
        "horizon": 30,
        "p10": [p50 - 10.0] * 30,
        "p50": [p50] * 30,
        "p90": [p50 + 10.0] * 30,
    }


def call(conn, loader, horizon=7, models=None, circuit_breaker_enabled=True):
    skin = conn.execute("SELECT * FROM skins WHERE id=1").fetchone()
    return predict_for_skin(
        conn,
        skin,
        horizon,
        models,
        loader,
        NOW,
        ttl_hours=6,
        circuit_breaker_enabled=circuit_breaker_enabled,
    )


def test_service_returns_fresh_live_prediction():
    result = call(make_conn(), FakeLoader(live_result()))
    assert result["status"] == "available"
    assert result["decisionDate"] == "2026-07-22"
    assert result["currentPrice"] == 100.0
    assert result["predictions"][0]["price"] == 105.0
    assert result["predictions"][0]["routeModel"] == "Hybrid-V2"
    assert result["calibration"]["maxDeviation"] == 0.30
    assert result["trend30d"] is None


def test_service_attaches_thirty_day_trend_without_seven_day_breaker():
    loader = FakeLoader(live_result(), trend_result(p50=180.0))

    result = call(make_conn(), loader, circuit_breaker_enabled=True)

    assert result["status"] == "available"
    assert result["trend30d"]["decisionDate"] == "2026-07-22"
    assert result["trend30d"]["model"] == "Keras-Seq2Seq-30D"
    assert result["trend30d"]["horizon"] == 30
    assert result["trend30d"]["p50"][:7] == result["predictions"][0]["dailyPrices"]
    assert max(result["trend30d"]["p90"]) <= 130.0


def test_service_ignores_stale_thirty_day_trend_but_keeps_seven_day_result():
    loader = FakeLoader(live_result(), trend_result(date="2026-07-21"))

    result = call(make_conn(), loader)

    assert result["status"] == "available"
    assert result["predictions"][0]["price"] == 105.0
    assert result["trend30d"] is None


def test_service_ignores_invalid_thirty_day_quantile_order():
    trend = trend_result()
    trend["p10"][5] = 120.0
    loader = FakeLoader(live_result(), trend)

    result = call(make_conn(), loader)

    assert result["status"] == "available"
    assert result["trend30d"] is None


def test_service_clips_overwide_trend_edges_without_rejecting_median():
    trend = trend_result(p50=180.0)
    trend["p10"] = [5.0] * 30
    trend["p90"] = [500.0] * 30
    loader = FakeLoader(live_result(), trend)

    result = call(make_conn(), loader)

    assert all(
        70.0 <= low <= median <= high <= 130.0
        for low, median, high in zip(
            result["trend30d"]["p10"],
            result["trend30d"]["p50"],
            result["trend30d"]["p90"],
        )
    )


def test_service_rejects_stale_decision_date():
    result = call(make_conn(), FakeLoader(live_result(date="2023-05-19")))
    assert result["status"] == "unavailable"
    assert result["reason"] == "STALE_INPUT"
    assert result["predictions"] == []


def test_service_rejects_price_anchor_mismatch():
    result = call(make_conn(), FakeLoader(live_result(current=99.0)))
    assert result["status"] == "unavailable"
    assert result["reason"] == "PRICE_ANCHOR_MISMATCH"


def test_service_calibrates_move_over_thirty_percent_without_warning():
    result = call(
        make_conn(),
        FakeLoader(live_result(price=131.0), trend_result(p50=180.0)),
    )
    assert result["status"] == "available"
    assert 70.0 <= result["predictions"][0]["price"] <= 130.0
    assert result["warnings"] == []
    assert "SMOOTH_DEVIATION_COMPRESSION" in result["calibration"]["reasonCodes"]
    assert result["trend30d"]["horizon"] == 30


def test_service_calibration_is_independent_of_legacy_breaker_flag():
    result = call(
        make_conn(),
        FakeLoader(live_result(price=131.0)),
        circuit_breaker_enabled=False,
    )
    assert result["status"] == "available"
    assert result["predictions"][0]["price"] < 130.0
    assert result["warnings"] == []


def test_service_returns_unavailable_when_tensorflow_is_missing():
    result = call(make_conn(), FakeLoader(None))
    assert result["status"] == "unavailable"
    assert result["reason"] == "MODEL_UNAVAILABLE"


def test_service_rejects_unsupported_model_request():
    result = call(make_conn(), FakeLoader(live_result()), models=["XGBoost"])
    assert result["status"] == "unavailable"
    assert result["reason"] == "REQUESTED_MODEL_UNAVAILABLE"


def test_service_never_calls_predict_all_models():
    loader = FakeLoader(live_result())
    call(make_conn(), loader)
    assert loader.live_calls == 1


def test_cache_requires_unexpired_matching_date_price_and_version():
    conn = make_conn()
    conn.execute(
        """INSERT INTO predictions(
               skin_id, horizon, model, type, predicted_price, current_price,
               change_pct, confidence, generated_at, expires_at, daily_json,
               decision_date, model_version, data_through
           ) VALUES (1, 7, 'LSTM', 'DL', 105, 100, 5, 73.6, ?, ?, ?, ?, ?, ?)""",
        (
            "2026-07-24T11:00:00+00:00",
            "2026-07-24T18:00:00+00:00",
            json.dumps([100.5, 101, 102, 103, 104, 104.5, 105]),
            "2026-07-22",
            f"lstm-test-v1-{FORECAST_CALIBRATION_CONTRACT}",
            "2026-07-22",
        ),
    )
    loader = FakeLoader(live_result(), trend_result())
    result = call(conn, loader)
    assert result["status"] == "available"
    assert result["generatedAt"] == "2026-07-24T11:00:00+00:00"
    assert loader.live_calls == 0
    assert loader.trend_calls == 1
    assert result["trend30d"]["horizon"] == 30


def test_expired_cache_is_not_reused():
    conn = make_conn()
    conn.execute(
        """INSERT INTO predictions(
               skin_id, horizon, model, type, predicted_price, current_price,
               change_pct, confidence, generated_at, expires_at, daily_json,
               decision_date, model_version, data_through
           ) VALUES (1, 7, 'LSTM', 'DL', 105, 100, 5, 73.6, ?, ?, ?, ?, ?, ?)""",
        (
            "2026-07-24T01:00:00+00:00",
            "2026-07-24T11:59:59+00:00",
            json.dumps([100.5, 101, 102, 103, 104, 104.5, 105]),
            "2026-07-22",
            "lstm-test-v1",
            "2026-07-22",
        ),
    )
    loader = FakeLoader(live_result())
    call(conn, loader)
    assert loader.live_calls == 1


def test_legacy_out_of_range_cache_is_calibrated_instead_of_rejected():
    conn = make_conn()
    daily = [101, 105, 110, 115, 120, 126, 131]
    conn.execute(
        """INSERT INTO predictions(
               skin_id, horizon, model, type, predicted_price, current_price,
               change_pct, confidence, generated_at, expires_at, daily_json,
               decision_date, model_version, data_through
           ) VALUES (1, 7, 'LSTM', 'DL', 131, 100, 31, 73.6, ?, ?, ?, ?, ?, ?)""",
        (
            "2026-07-24T11:00:00+00:00",
            "2026-07-24T18:00:00+00:00",
            json.dumps(daily),
            "2026-07-22",
            "lstm-test-v1",
            "2026-07-22",
        ),
    )
    loader = FakeLoader(
        live_result(price=131.0),
        trend_result(p50=180.0),
    )

    result = call(conn, loader, circuit_breaker_enabled=True)

    assert result["status"] == "available"
    assert result["predictions"][0]["price"] < 130.0
    assert result["warnings"] == []
    assert result["trend30d"]["horizon"] == 30
    assert loader.live_calls == 1


def test_service_uses_recent_median_for_unconfirmed_single_price_jump():
    conn = make_conn()
    conn.execute("DELETE FROM price_history")
    prices = [67.0, 68.0, 66.5, 67.4, 67.2, 66.9, 67.3, 67.1,
              67.5, 66.8, 67.2, 67.0, 70.34, 100.84]
    conn.executemany(
        "INSERT INTO price_history(skin_id, date, price, daily_volume) VALUES(1, ?, ?, 0)",
        [(f"2026-07-{index + 9:02d}", price) for index, price in enumerate(prices)],
    )
    result = call(
        conn,
        FakeLoader(live_result(current=100.84, date="2026-07-22", price=68.0)),
    )

    assert result["currentPrice"] == 100.84
    assert result["forecastAnchorPrice"] == 67.2
    assert result["predictions"][0]["forecastAnchorPrice"] == 67.2
    assert "UNCONFIRMED_PRICE_SHOCK" in result["calibration"]["reasonCodes"]
    assert all(67.2 * 0.70 <= price <= 67.2 * 1.30
               for price in result["predictions"][0]["dailyPrices"])
