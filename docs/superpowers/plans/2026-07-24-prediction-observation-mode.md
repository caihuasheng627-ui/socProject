# Prediction Observation Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the old LSTM's raw database-anchored prediction while retaining an explicit out-of-range warning and an easy way to restore blocking.

**Architecture:** Add a configuration flag at the FastAPI composition boundary and pass it to the prediction service. The service keeps validation intact, converts only the 30% rejection into a warning when observation mode is active, and applies the same policy to cached results.

**Tech Stack:** Python 3.13, FastAPI, SQLite, pytest, Vue 3 CDN, OpenAPI YAML.

---

### Task 1: Configurable Circuit Breaker

**Files:**
- Modify: `backend/tests/test_prediction_service.py`
- Modify: `backend/prediction_service.py`
- Modify: `backend/config.py`
- Modify: `backend/main.py`

- [ ] **Step 1: Write the failing tests**

Add a service test that calls `predict_for_skin(..., circuit_breaker_enabled=False)`
with a 31% result and expects `status == "available"`, the raw price, and
`warnings == ["PREDICTION_OUT_OF_RANGE"]`. Retain the existing enabled-policy test.
Add a cache test proving an out-of-range result cached while disabled is rejected after
the breaker is enabled.

- [ ] **Step 2: Verify RED**

Run: `D:/Anaconda/python -m pytest backend/tests/test_prediction_service.py -q -p no:cacheprovider`

Expected: FAIL because `predict_for_skin` does not accept `circuit_breaker_enabled`.

- [ ] **Step 3: Implement minimal behavior**

Add `PREDICTION_CIRCUIT_BREAKER_ENABLED = os.getenv("PREDICTION_CIRCUIT_BREAKER_ENABLED", "0") == "1"`.
Pass it from `main.predict` to `predict_for_skin`. Add the optional service argument with
an enabled default for backwards compatibility. Return the warning instead of rejecting
when disabled, and revalidate cached paths before returning them.

- [ ] **Step 4: Verify GREEN**

Run: `D:/Anaconda/python -m pytest backend/tests/test_prediction_service.py backend/tests/test_prediction_api_contract.py -q -p no:cacheprovider`

Expected: all tests PASS.

### Task 2: Contract and Frontend Warning

**Files:**
- Modify: `api-spec/openapi.yaml`
- Modify: `app.js`
- Modify: `index.html`
- Modify: `i18n.js`

- [ ] **Step 1: Document `warnings`**

Add a required string-array `warnings` property to `PredictionResult`, with
`PREDICTION_OUT_OF_RANGE` as its documented value.

- [ ] **Step 2: Render the warning**

Store `res.warnings` in a `predictionWarnings` ref. Show a concise warning above the
model table while continuing to render the returned prediction and chart.

- [ ] **Step 3: Verify syntax and full tests**

Run: `node --check app.js; node --check i18n.js`

Run: `D:/Anaconda/python -m pytest backend/tests ml/tests -q -p no:cacheprovider`

Expected: JavaScript checks exit 0 and all Python tests PASS.

- [ ] **Step 4: Verify the real item**

POST `/api/predict` for `karambit-doppler-factory-new`. Expect `status=available`,
`warnings` to contain `PREDICTION_OUT_OF_RANGE`, the decision date to match the latest
database date, and exactly one LSTM prediction containing the raw seven-step path.
