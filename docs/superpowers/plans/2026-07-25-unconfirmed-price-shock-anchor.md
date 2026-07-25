# Unconfirmed Price Shock Anchor Implementation Plan

**Goal:** Prevent one unconfirmed live-price jump from causing large seven-day and 30-day forecast swings while preserving the true current market price in the API and chart history.

**Architecture:** Add a deterministic shock detector in the backend calibration layer. For non-ultra items, a latest observation that jumps at least 35% from both the prior observation and the preceding 14-observation median is treated as unconfirmed; forecast calculations use that median as their anchor and exclude the jump from recent-trend statistics. Existing `$1000+` `ultra` rebasing remains higher priority so the previous expensive-knife fix is preserved. The frontend consumes an explicit `forecastAnchorPrice` for forecast-path scaling while continuing to show the actual `currentPrice` in market data.

**Tech Stack:** Python, FastAPI, SQLite, NumPy, pytest, Vue/ECharts static frontend.

### Task 1: Lock the shock detector contract

**Files:**
- Modify: `backend/tests/test_forecast_calibration.py`
- Test: `backend/forecast_calibration.py`

- [ ] Add tests for a 43% M4A4-style jump and a 72% CZ75-style jump returning the previous 14-observation median as the forecast anchor.
- [ ] Add tests proving stable data returns the live price, two consecutive high observations confirm the new level, and `$1000+` items remain on the live-price/ultra path.
- [ ] Run the focused tests and verify they fail because the detector is absent.

### Task 2: Implement shock-aware calibration inputs

**Files:**
- Modify: `backend/forecast_calibration.py`
- Modify: `backend/prediction_service.py`
- Test: `backend/tests/test_prediction_service.py`

- [ ] Implement `forecast_anchor_context()` with a 14-observation median, 35% jump threshold, confirmation check, and reason metadata.
- [ ] Pass the effective anchor to seven-day and 30-day calibration and exclude the latest unconfirmed point from recent trend statistics.
- [ ] Keep `currentPrice` unchanged; add `forecastAnchorPrice` and `marketChange` metadata.
- [ ] Include the calibration contract in cache identity so old cached paths cannot bypass shock detection.
- [ ] Add tests for API payload fields, cache invalidation, and the two named items' expected stable forecast anchor.

### Task 3: Align frontend rendering

**Files:**
- Modify: `app.js`
- Test: `backend/tests/test_prediction_api_contract.py`

- [ ] Set the frontend prediction path base to `forecastAnchorPrice` when present, while retaining actual K-line/current price display.
- [ ] Keep the existing 7-day/30-day handoff and display the reason through existing calibration evidence without adding a warning banner.
- [ ] Verify the frontend does not rescale a shock-anchored path back to the unconfirmed current price.

### Task 4: Evaluate and verify

**Files:**
- Modify: `ml/outputs/hybrid_v2_calibration_report.json` only if evaluation code requires updated reporting.

- [ ] Run the two-item live API smoke test and confirm forecast paths remain near the median anchor.
- [ ] Run `backend/tests` and `ml/tests` with a workspace-local pytest temp directory.
- [ ] Run `git diff --check` and report the exact changed files; do not commit or merge.
