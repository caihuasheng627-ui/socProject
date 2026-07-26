import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
sys.modules.pop("config", None)

import main
from fastapi.testclient import TestClient


class FakeLoader:
    def live_model_version(self):
        return "lstm-contract-v1"

    def predict_live_lstm(self, name):
        return {
            "current_price": 100.0,
            "predicted_price": 105.0,
            "daily_prices": [101, 102, 103, 104, 104, 105, 105],
            "model": "LSTM-C(__UNK__)",
            "date": "2026-07-22",
            "change_pct": 5.0,
            "confidence": 73.6,
        }

    def predict_live_ensemble(self, name):
        result = self.predict_live_lstm(name)
        return {
            "current_price": result["current_price"],
            "date": result["date"],
            "model": "Hybrid-V2",
            "confidence": result["confidence"],
            "price_tier": "global",
            "lstm_c_prices": result["daily_prices"],
            "lstm_d_prices": result["daily_prices"],
            "adapter": {
                "global": {
                    str(day): {"c": 0.5, "d": 0.5, "recent": 0.0, "bias": 0.0}
                    for day in range(1, 8)
                }
            },
        }

    def predict_live_trend_30d(self, name):
        return {
            "current_price": 100.0,
            "date": "2026-07-22",
            "model": "Keras-Seq2Seq-30D",
            "horizon": 30,
            "p10": [95.0] * 30,
            "p50": [105.0] * 30,
            "p90": [115.0] * 30,
        }


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE skins (
            id INTEGER PRIMARY KEY, slug TEXT, market_hash_name TEXT, source TEXT
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
        INSERT INTO price_history VALUES (1, 1, '2026-07-22', 100.0, 0);
        """
    )
    return conn


def test_predict_route_exposes_live_provenance_without_future_truth(monkeypatch):
    conn = make_conn()
    monkeypatch.setattr(main, "get_connection", lambda: conn)
    monkeypatch.setattr(
        main,
        "resolve_skin",
        lambda db, key: db.execute("SELECT * FROM skins WHERE id=1").fetchone(),
    )
    monkeypatch.setattr(main, "_loader", FakeLoader())

    result = main.predict(main.PredictReq(skinId="test-skin", horizon=7))

    for field in (
        "status", "reason", "decisionDate", "dataThrough", "modelVersion",
        "priceSource", "volumeCoverage", "warnings",
    ):
        assert field in result
    assert result["status"] == "available"
    assert result["forecastAnchorPrice"] == 100.0
    assert result["decisionDate"] == "2026-07-22"
    assert result["predictions"][0]["model"] == "LSTM"
    assert result["predictions"][0]["routeModel"] == "Hybrid-V2"
    assert result["calibration"]["method"] == "hybrid-v2-log-return-smooth-bound"
    assert result["warnings"] == []
    assert result["trend30d"]["horizon"] == 30
    assert len(result["trend30d"]["p50"]) == 30
    assert "actual_future_price" not in str(result)


def test_openapi_documents_nullable_thirty_day_quantile_trend():
    root = Path(__file__).resolve().parents[2]
    spec = (root / "api-spec" / "openapi.yaml").read_text(encoding="utf-8")

    assert "trend30d:" in spec
    assert "minItems: 30" in spec
    assert "maxItems: 30" in spec
    assert "Keras-Seq2Seq-30D" in spec
    assert "Hybrid-V2" in spec
    assert "calibration:" in spec
    assert "forecastAnchorPrice:" in spec
    assert "UNCONFIRMED_PRICE_SHOCK" in spec
    assert "PREDICTION_OUT_OF_RANGE" not in spec


def test_predict_route_rejects_thirty_day_request_at_validation_boundary():
    response = TestClient(main.app).post(
        "/api/predict", json={"skinId": "test-skin", "horizon": 30}
    )

    assert response.status_code == 422


def test_frontend_renders_backend_authoritative_trend_with_natural_handoff():
    root = Path(__file__).resolve().parents[2]
    app = (root / "app.js").read_text(encoding="utf-8")
    messages = (root / "i18n.js").read_text(encoding="utf-8")
    markup = (root / "index.html").read_text(encoding="utf-8")

    assert "predictionTrend30d.value = res.trend30d" in app
    assert "const TREND_WEIGHTS" not in app
    assert "buildCompositeTrendPath" not in app
    assert "trendPath.p50.slice(Math.max(exactHorizon - 1, 0))" in app
    assert "stack: 'trend-band'" not in app
    assert "prediction.chart.trend30d" in app
    assert "prediction.chart.trend30d" in messages
    assert "30天综合趋势" in messages
    assert "projectTrendPrice" not in app
    assert "const trendBridge" not in app
    assert "trendSeries(authoritativeTrend)" in app
    assert "lineStyle: { color: '#22c55e', width: 2, type: 'dashed' }" in app
    assert "rgba(34, 197, 94, 0.3)" in app
    assert "rgba(34, 197, 94, 0.05)" in app
    assert "predictedDates[Math.min(29, predictedDates.length - 1)]" in app
    assert "trendSeries(trendP10)" not in app
    assert "trendSeries(trendP90)" not in app
    assert "predictionCalibration.value = res.calibration || null" in app
    assert "forecastAnchorPrice" in app
    assert "const forecastAnchor = Number(res.forecastAnchorPrice" in app
    assert "prediction.calibrationTitle" in messages
    assert "prediction.calibrationWeights" in messages
    assert "predictionCalibration" in markup
    assert "PREDICTION_OUT_OF_RANGE" not in app
    assert "PREDICTION_OUT_OF_RANGE" not in messages
    assert "PREDICTION_OUT_OF_RANGE" not in markup
    assert "prediction.outOfRangeWarning" not in messages
