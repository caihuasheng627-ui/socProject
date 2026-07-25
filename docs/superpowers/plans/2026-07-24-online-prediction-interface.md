# Online Prediction Interface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/api/predict` return only fresh database-anchored LSTM inference, or an explicit unavailable response, without reading offline prediction CSV files or drawing fabricated frontend forecasts.

**Architecture:** Add a focused prediction service between FastAPI and `ModelLoader`. The loader gets a strict database-only inference entry point, while the service owns freshness validation, the 30% circuit breaker, response construction, and cache identity. Offline CSV adapters remain untouched for model-lab evaluation.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pandas/numpy, pytest, Vue 3 CDN, OpenAPI YAML.

---

### Task 1: Prediction Cache Schema

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/tests/test_price_quality_migration.py`

- [ ] **Step 1: Write the failing migration test**

Add an in-memory legacy `predictions` table and assert that migration adds `decision_date`, `model_version`, and `data_through`, then removes legacy rows whose identity fields are null.

```python
def test_prediction_cache_migration_adds_identity_and_drops_legacy_rows():
    from database import migrate_prediction_cache_contract

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, skin_id INTEGER, horizon INTEGER,
            model TEXT, current_price REAL, generated_at TEXT, expires_at TEXT
        );
        INSERT INTO predictions VALUES
            (1, 1, 7, 'LSTM', 100.0, '2026-07-24T00:00:00+00:00',
             '2026-07-24T06:00:00+00:00');
    """)
    migrate_prediction_cache_contract(conn)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(predictions)")}
    assert {"decision_date", "model_version", "data_through"} <= columns
    assert conn.execute("SELECT COUNT(*) FROM predictions").fetchone()[0] == 0
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `D:/Anaconda/python -m pytest backend/tests/test_price_quality_migration.py::test_prediction_cache_migration_adds_identity_and_drops_legacy_rows -q -p no:cacheprovider`

Expected: FAIL because `migrate_prediction_cache_contract` does not exist.

- [ ] **Step 3: Implement the migration**

Add the three columns to `SCHEMA_SQL`, implement an idempotent migration accepting an optional connection, and invoke it from `run_init`. Delete rows lacking identity fields so old 2022/2023 cache entries cannot survive deployment.

```python
def migrate_prediction_cache_contract(
    conn: sqlite3.Connection | None = None,
) -> None:
    owns_connection = conn is None
    db = conn or get_connection()
    try:
        for name, sql_type in (
            ("decision_date", "TEXT"),
            ("model_version", "TEXT"),
            ("data_through", "TEXT"),
        ):
            if not _column_exists(db, "predictions", name):
                db.execute(f"ALTER TABLE predictions ADD COLUMN {name} {sql_type}")
        db.execute(
            "DELETE FROM predictions "
            "WHERE decision_date IS NULL OR model_version IS NULL OR data_through IS NULL"
        )
        db.commit()
    finally:
        if owns_connection:
            db.close()
```

- [ ] **Step 4: Run the migration tests**

Run: `D:/Anaconda/python -m pytest backend/tests/test_price_quality_migration.py -q -p no:cacheprovider`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```text
git add backend/database.py backend/tests/test_price_quality_migration.py
git commit -m "fix: version prediction cache by data date"
```

### Task 2: Database-Only LSTM Inference

**Files:**
- Modify: `backend/model_loader.py`
- Create: `backend/tests/test_live_model_loader.py`

- [ ] **Step 1: Write the failing source-isolation test**

Monkeypatch `_skin_window_from_db` to return a known window and `_skin_window` to raise if called. Construct a loader without running TensorFlow initialization, stub `_predict_lstm_c`, and assert `predict_live_lstm` returns the database date and never touches the CSV panel.

```python
def test_live_lstm_uses_database_window_only(monkeypatch):
    import model_loader

    X = np.zeros((1, 60, 15), dtype=np.float32)
    monkeypatch.setattr(
        model_loader, "_skin_window_from_db",
        lambda name: (X, 100.0, "2026-07-22"),
    )
    monkeypatch.setattr(
        model_loader, "_skin_window",
        lambda name: (_ for _ in ()).throw(AssertionError("offline panel used")),
    )
    loader = object.__new__(model_loader.ModelLoader)
    loader.tf_available = True
    loader.item_map = {"__UNK__": 0}
    loader.group_map = {}
    loader.hybrid_route = {"low": "LSTM-C", "mid": "LSTM-D", "high": "LSTM-D"}
    loader.models = {}
    loader.scalers = {}
    loader._predict_lstm_c = lambda X, name: [101.0] * 7
    loader._predict_lstm_d = lambda X, name: None

    result = loader.predict_live_lstm("new item")
    assert result["date"] == "2026-07-22"
    assert result["predicted_price"] == 101.0
```

- [ ] **Step 2: Run the test and confirm failure**

Run: `D:/Anaconda/python -m pytest backend/tests/test_live_model_loader.py -q -p no:cacheprovider`

Expected: FAIL because `predict_live_lstm` does not exist.

- [ ] **Step 3: Implement the strict live entry point**

Add `predict_live_lstm` that calls only `_skin_window_from_db`, refuses Mock fallback when TensorFlow/models are unavailable, routes known items through the frozen Hybrid map, routes unknown items through LSTM-C `__UNK__`, and returns one seven-step path. Add `live_model_version` derived from relevant artifact names, sizes, and mtimes so cache identity changes when artifacts change.

- [ ] **Step 4: Run loader tests**

Run: `D:/Anaconda/python -m pytest backend/tests/test_live_model_loader.py ml/tests/test_cold_start.py -q -p no:cacheprovider`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```text
git add backend/model_loader.py backend/tests/test_live_model_loader.py
git commit -m "fix: add database-only LSTM inference"
```

### Task 3: Prediction Service and Contract Tests

**Files:**
- Create: `backend/prediction_service.py`
- Create: `backend/tests/test_prediction_service.py`

- [ ] **Step 1: Write failing service tests**

Use an in-memory database and fake loader to cover these exact cases:

```python
import json
import sqlite3
from datetime import datetime, timezone

from prediction_service import predict_for_skin


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
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
    """)
    return conn


class FakeLoader:
    def __init__(self, result):
        self.result = result
        self.live_calls = 0

    def live_model_version(self):
        return "lstm-test-v1"

    def predict_live_lstm(self, name):
        self.live_calls += 1
        return self.result

    def predict_all_models(self, name, horizon):
        raise AssertionError("offline prediction adapter was called")


def live_result(price=105.0, date="2026-07-22"):
    daily = [100.5, 101.0, 102.0, 103.0, 104.0, 104.5, price]
    return {
        "current_price": 100.0,
        "predicted_price": price,
        "daily_prices": daily,
        "model": "LSTM-C(__UNK__)",
        "date": date,
        "change_pct": round(price - 100.0, 2),
        "confidence": 73.6,
    }


def call(conn, loader, horizon=7, models=None):
    skin = conn.execute("SELECT * FROM skins WHERE id=1").fetchone()
    return predict_for_skin(
        conn, skin, horizon, models, loader, NOW, ttl_hours=6
    )


def test_service_returns_fresh_live_prediction():
    result = call(make_conn(), FakeLoader(live_result()))
    assert result["status"] == "available"
    assert result["decisionDate"] == "2026-07-22"
    assert result["currentPrice"] == 100.0
    assert result["predictions"][0]["price"] == 105.0


def test_service_rejects_stale_decision_date():
    result = call(make_conn(), FakeLoader(live_result(date="2023-05-19")))
    assert result["status"] == "unavailable"
    assert result["reason"] == "STALE_INPUT"
    assert result["predictions"] == []


def test_service_rejects_move_over_thirty_percent():
    result = call(make_conn(), FakeLoader(live_result(price=131.0)))
    assert result["status"] == "unavailable"
    assert result["reason"] == "PREDICTION_OUT_OF_RANGE"


def test_service_returns_unavailable_when_tensorflow_is_missing():
    result = call(make_conn(), FakeLoader(None))
    assert result["status"] == "unavailable"
    assert result["reason"] == "MODEL_UNAVAILABLE"


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
            "lstm-test-v1",
            "2026-07-22",
        ),
    )
    loader = FakeLoader(live_result())
    result = call(conn, loader)
    assert result["status"] == "available"
    assert result["generatedAt"] == "2026-07-24T11:00:00+00:00"
    assert loader.live_calls == 0
```

The successful fixture returns a 7-day path ending at `105.0` from a live price of `100.0`. The stale fixture returns a 2023 decision date. The circuit-breaker fixture returns `131.0`.

- [ ] **Step 2: Run tests and confirm failure**

Run: `D:/Anaconda/python -m pytest backend/tests/test_prediction_service.py -q -p no:cacheprovider`

Expected: FAIL because `prediction_service` does not exist.

- [ ] **Step 3: Implement service validation**

Implement `predict_for_skin(conn, skin, horizon, requested_models, loader, now, ttl_hours)` with these reason codes:

```text
NO_PRICE_HISTORY
UNSUPPORTED_HORIZON
REQUESTED_MODEL_UNAVAILABLE
MODEL_UNAVAILABLE
STALE_INPUT
PRICE_ANCHOR_MISMATCH
INVALID_PREDICTION
PREDICTION_OUT_OF_RANGE
```

Available responses use the latest database price for both `currentPrice` and `livePriceUsd`, include one `LSTM` prediction, and cache it with decision date and model version. Unavailable responses contain `predictions=[]`, `consensus=null`, `entryRange=null`, and `targetPrice=null`.

- [ ] **Step 4: Run service tests**

Run: `D:/Anaconda/python -m pytest backend/tests/test_prediction_service.py -q -p no:cacheprovider`

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```text
git add backend/prediction_service.py backend/tests/test_prediction_service.py
git commit -m "fix: validate fresh online predictions"
```

### Task 4: FastAPI and OpenAPI Integration

**Files:**
- Modify: `backend/main.py`
- Modify: `api-spec/openapi.yaml`
- Create: `backend/tests/test_prediction_api_contract.py`

- [ ] **Step 1: Write the failing API contract test**

Patch `main.get_connection`, `main.resolve_skin`, and the live loader, call the FastAPI route function with `PredictReq`, and assert `status`, `reason`, `decisionDate`, `dataThrough`, `modelVersion`, `priceSource`, and `volumeCoverage` exist. Also assert no canonical truth fields are returned.

- [ ] **Step 2: Run the test and confirm failure**

Run: `D:/Anaconda/python -m pytest backend/tests/test_prediction_api_contract.py -q -p no:cacheprovider`

Expected: FAIL because the current route still calls `predict_all_models` and lacks status fields.

- [ ] **Step 3: Replace route internals**

Keep request validation and 404 behavior in `main.py`, then delegate to `prediction_service.predict_for_skin`. Remove the old median-price consensus loop and all direct prediction cache SQL from the route.

- [ ] **Step 4: Update OpenAPI**

Document nullable `consensus`, `entryRange`, and `targetPrice`, the new status/provenance fields, `decisionDate` inside each prediction, and the unavailable reason codes. Keep canonical evaluation schemas unchanged.

- [ ] **Step 5: Run backend tests**

Run: `D:/Anaconda/python -m pytest backend/tests ml/tests -q -p no:cacheprovider`

Expected: all tests PASS.

- [ ] **Step 6: Commit**

```text
git add backend/main.py api-spec/openapi.yaml backend/tests/test_prediction_api_contract.py
git commit -m "fix: serve only live database predictions"
```

### Task 5: Frontend Unavailable State

**Files:**
- Modify: `app.js`
- Modify: `js/api.js`

- [ ] **Step 1: Add explicit prediction status state**

Add `predictionStatus` and `predictionReason` refs. In `loadPredictions`, retain backend `status`; clear prediction data when unavailable. Mark `_mockPredict` responses as `status: 'demo'` so static demo behavior remains explicit.

- [ ] **Step 2: Disable synthetic fallback after an unavailable response**

Change the chart fallback guard from:

```javascript
if (!modelPredictions.value.length) {
```

to:

```javascript
if (!modelPredictions.value.length && predictionStatus.value !== 'unavailable') {
```

Do not restore any volume chart or volume sorting.

- [ ] **Step 3: Run syntax checks**

Run: `node --check app.js`

Run: `node --check js/api.js`

Expected: both commands exit 0.

- [ ] **Step 4: Commit**

```text
git add app.js js/api.js
git commit -m "fix: suppress fabricated unavailable forecasts"
```

### Task 6: End-to-End Verification

**Files:**
- No new production files.

- [ ] **Step 1: Run the complete automated suite**

Run: `D:/Anaconda/python -m pytest backend/tests ml/tests -q -p no:cacheprovider`

Expected: all tests PASS.

- [ ] **Step 2: Start the local API**

Run from `backend`: `D:/Anaconda/python -m uvicorn main:app --host 127.0.0.1 --port 8000`

Expected: `/api/health` returns HTTP 200.

- [ ] **Step 3: Verify the reported Karambit case**

POST `/api/predict` for `★ Karambit | Doppler (Factory New)` and assert:

- no model has decision date 2022 or 2023;
- `currentPrice` equals the latest database price;
- response is either a valid database-date LSTM result or explicit `unavailable`;
- no ARIMA, XGBoost, LightGBM, RandomForest, or offline CSV prediction appears.

- [ ] **Step 4: Verify repository state**

Run: `git status --short`

Expected: clean worktree after the planned commits.
