import sqlite3

import main


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
        "priceSource", "volumeCoverage",
    ):
        assert field in result
    assert result["status"] == "available"
    assert result["decisionDate"] == "2026-07-22"
    assert result["predictions"][0]["model"] == "LSTM"
    assert "actual_future_price" not in str(result)
