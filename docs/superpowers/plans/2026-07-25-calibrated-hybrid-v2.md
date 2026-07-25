# Calibrated Hybrid V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build, evaluate, and deploy an automatically maintained Hybrid V2 forecast with coordinated seven-day and 30-day calibration and no greater-than-30% frontend warning.

**Architecture:** Frozen LSTM-C/D models feed a CS2-era convex fusion adapter. A pure backend calibration module consumes the two model paths and recent prices, returns one bounded explainable path, and anchors the 30-day Keras trend to its day-seven endpoint. The frontend renders only the calibrated API contract.

**Tech Stack:** Python 3.13, NumPy, pandas, TensorFlow/Keras inference, FastAPI, SQLite, pytest, Vue 3 CDN, ECharts.

---

### Task 1: Pure Forecast Calibration

**Files:**
- Create: `backend/forecast_calibration.py`
- Create: `backend/tests/test_forecast_calibration.py`

- [ ] Write tests for smooth bounding, finite positive prices, C/D disagreement shrinkage, deterministic explanations, and D1-D30 70%-130% limits.
- [ ] Run `D:/Anaconda/python -m pytest backend/tests/test_forecast_calibration.py -q` and confirm missing-module failure.
- [ ] Implement recent-context extraction, Hybrid V2 return blending, asymmetric smooth bounds, and 30-day handoff calibration.
- [ ] Re-run the focused tests and confirm they pass.

### Task 2: Hybrid V2 Adapter Artifact

**Files:**
- Create: `ml/train_hybrid_v2.py`
- Create: `ml/tests/test_hybrid_v2.py`
- Create: `ml/models/hybrid_v2_adapter.json`
- Create: `ml/outputs/hybrid_v2_results.json`

- [ ] Write tests for chronological splitting, convex weights, train-only selection, serialization, and deterministic fallback weights.
- [ ] Run the focused tests and confirm the new API is absent.
- [ ] Implement batch C/D prediction generation from CS2 price history and grid-searched horizon/tier fusion weights.
- [ ] Run a small synthetic smoke training test, then train the adapter on the available rolling 180-day database.
- [ ] Evaluate train/validation/test metrics and atomically publish the JSON artifact only when its contract validates.

### Task 3: Online Model Loader and API Integration

**Files:**
- Modify: `backend/model_loader.py`
- Modify: `backend/prediction_service.py`
- Modify: `backend/tests/test_live_model_loader.py`
- Modify: `backend/tests/test_prediction_service.py`
- Modify: `backend/tests/test_prediction_api_contract.py`

- [ ] Write failing tests requiring simultaneous C/D paths, Hybrid V2 metadata, calibrated authoritative prices, coordinated trend output, and no out-of-range warning.
- [ ] Add artifact loading and `predict_live_ensemble` while preserving the existing fallback contract.
- [ ] Calibrate before cache validation and include the calibration artifact identity in the model version.
- [ ] Calibrate the 30-day payload against the seven-day endpoint and preserve quantile ordering.
- [ ] Run all backend prediction tests.

### Task 4: Frontend Rendering and Explanation

**Files:**
- Modify: `app.js`
- Modify: `index.html`
- Modify: `i18n.js`
- Modify: `api-spec/openapi.yaml`
- Test: `backend/tests/test_prediction_api_contract.py`

- [ ] Write contract assertions that reject the old greater-than-30% warning and require calibration explanation fields.
- [ ] Remove out-of-range warning rendering and translations.
- [ ] Render the calibrated seven-day/30-day paths and a compact C/D/recent-trend explanation.
- [ ] Update OpenAPI schemas and examples without changing existing required response fields.
- [ ] Run JS syntax and API contract tests.

### Task 5: Evaluation and End-to-End Verification

**Files:**
- Create: `ml/evaluate_hybrid_v2.py`
- Create: `ml/outputs/hybrid_v2_calibration_report.json`
- Modify: `docs/model-training.md` if present, otherwise `README.md`

- [ ] Evaluate raw C, raw D, raw Hybrid V2, and calibrated Hybrid V2 on chronological held-out data.
- [ ] Report MAE, RMSE, MAPE, direction accuracy, P95 APE, raw outlier count, calibrated outlier count, and D7/D8 handoff.
- [ ] Run `D:/Anaconda/python -m pytest backend/tests ml/tests -q -p no:cacheprovider` using a system temporary directory.
- [ ] Run `node --check app.js`, `node --check i18n.js`, `git diff --check`, and a local API prediction smoke test.
- [ ] Inspect the frontend at desktop and mobile widths when the local server is available.

