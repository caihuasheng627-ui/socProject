# Seven-Observation Horizon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make deep-model training, prediction export, model comparison, and backtesting consistently predict and evaluate the seventh later valid daily observation.

**Architecture:** Add one shared forecast-contract module for grouped targets, aligned sequence windows, prediction-file validation, and common-universe checks. Existing training scripts consume the shared functions while retaining their model architectures. Validation selects routing; test predictions drive final metrics and forward-only backtests.

**Tech Stack:** Python 3.13, pandas, NumPy, scikit-learn, TensorFlow/Keras, pytest

---

### Task 1: Forecast target contract

**Files:**
- Create: `SkinVest_project/notebooks/forecast_contract.py`
- Modify: `SkinVest_project/notebooks/feature_engineering.py`
- Create: `SkinVest_project/tests/test_forecast_contract.py`

- [ ] **Step 1: Write failing grouped-target tests**

Create tests with two items and nonconsecutive dates. Assert that `Target`,
`TargetDate`, and `TargetPrice` point to the seventh later observation within
the same item and never cross item boundaries.

```python
out = add_grouped_targets(panel, horizon_steps=7)
first = out[out.market_hash_name == "A"].iloc[0]
assert first.TargetDate == pd.Timestamp("2026-01-10")
assert first.TargetPrice == 17.0
assert first.Target == np.log1p(17.0)
```

- [ ] **Step 2: Run the target tests and verify RED**

Run: `D:/Anaconda/python -m pytest SkinVest_project/tests/test_forecast_contract.py -v`

Expected: FAIL because `forecast_contract` and grouped target metadata do not
exist.

- [ ] **Step 3: Implement the shared target contract**

Add constants `HORIZON_STEPS = 7` and `PREDICTION_COLUMNS`. Implement grouped
shifts for log price, raw price, date, and target split. Make
`build_features()` delegate target construction to this function.

- [ ] **Step 4: Run target tests and verify GREEN**

Run the same pytest command. Expected: all target tests PASS.

### Task 2: Split-safe continuous panel and aligned sequences

**Files:**
- Modify: `SkinVest_project/notebooks/forecast_contract.py`
- Modify: `SkinVest_project/notebooks/train_lstm_c.py`
- Modify: `SkinVest_project/notebooks/train_lstm_d.py`
- Modify: `SkinVest_project/notebooks/train_gru.py`
- Create: `SkinVest_project/tests/test_sequences.py`

- [ ] **Step 1: Write failing sequence and split tests**

Test a 61-row item with `lookback=60`. Assert the first window is rows 0..59,
its decision metadata comes from row 59, and no row 60 feature is present.
Test that train, validation, and test samples only use targets from their own
split while validation/test windows may use earlier-split history.

```python
X, y, meta = build_sequence_windows(panel, ["feature"], lookback=60)
assert X[0, :, 0].tolist() == list(range(60))
assert meta.iloc[0].date == panel.iloc[59].date
```

- [ ] **Step 2: Verify RED**

Run: `D:/Anaconda/python -m pytest SkinVest_project/tests/test_sequences.py -v`

Expected: FAIL because current windows exclude the decision row and no shared
split-safe loader exists.

- [ ] **Step 3: Implement continuous panel and shared sequence builder**

Implement `load_feature_panel(data_dir)` and
`build_sequence_windows(df, feature_cols, lookback, sample_split)`. Preserve
rolling history across split boundaries; restrict label split; fit scalers only
from returned training arrays. Change the three training scripts to use these
functions and `Path(__file__)`-relative paths.

- [ ] **Step 4: Verify GREEN**

Run target and sequence tests together. Expected: PASS.

### Task 3: Cold-start handling and Hybrid metadata

**Files:**
- Modify: `SkinVest_project/notebooks/train_lstm_c.py`
- Modify: `SkinVest_project/notebooks/train_lstm_d.py`
- Modify: `SkinVest_project/notebooks/compare_lstm_cd.py`
- Create: `SkinVest_project/tests/test_cold_start.py`

- [ ] **Step 1: Write failing cold-start tests**

Assert the item map is built from training items plus `__UNK__`; validation-only
items map to the unknown ID. Assert D routing uses train medians for known items
and current decision price for unknown items.

- [ ] **Step 2: Verify RED**

Run: `D:/Anaconda/python -m pytest SkinVest_project/tests/test_cold_start.py -v`

Expected: FAIL because the current map includes validation identities and D
uses full-validation medians.

- [ ] **Step 3: Implement cold-start behavior**

Save `{"item_map": ..., "unknown_id": ...}` for C. Save train-only D
boundaries and known-item groups. Make prediction routing use current price when
the name is absent. Save the validation-selected Hybrid route as explicit
metadata.

- [ ] **Step 4: Verify GREEN**

Run all contract, sequence, and cold-start tests. Expected: PASS.

### Task 4: Prediction export and correct metrics

**Files:**
- Modify: `SkinVest_project/notebooks/make_predictions.py`
- Modify: `SkinVest_project/notebooks/compare_models.py`
- Create: `SkinVest_project/tests/test_prediction_metrics.py`

- [ ] **Step 1: Write failing contract and metric tests**

Create a prediction frame where current price equals 10, future truth equals
20, and prediction equals 18. Assert MAE is 2, not 8. Test that mixed split,
wrong horizon, duplicate keys, and missing future truth raise `ValueError`.

- [ ] **Step 2: Verify RED**

Run: `D:/Anaconda/python -m pytest SkinVest_project/tests/test_prediction_metrics.py -v`

Expected: FAIL because comparison currently uses `current_price`.

- [ ] **Step 3: Implement canonical export and validation**

Make predictions accept `--split val|test`; export all canonical columns. Make
comparison discover input files, validate contracts, use
`actual_future_price`, omit hard-label AUC, and refuse mixed splits/horizons.

- [ ] **Step 4: Verify GREEN**

Run all non-backtest tests. Expected: PASS.

### Task 5: Fair forward-only backtest

**Files:**
- Modify: `SkinVest_project/notebooks/backtest.py`
- Create: `SkinVest_project/tests/test_backtest.py`

- [ ] **Step 1: Write failing backtest tests**

Test that two model frames are reduced to the same date/item intersection;
holding lasts seven item observations; buys and sells are counted separately;
fees reduce capital; open positions are reported; and a price gain above 200%
is not retroactively scaled.

- [ ] **Step 2: Verify RED**

Run: `D:/Anaconda/python -m pytest SkinVest_project/tests/test_backtest.py -v`

Expected: FAIL on cap removal, trade counts, and common-universe behavior.

- [ ] **Step 3: Implement the fair backtest**

Validate prediction contracts before simulation. Compute one common universe
for all ranked models. If source-level spike screening remains enabled, compute
one exclusion set from the source prices and apply it identically to every
model. Remove final-return curve scaling. Return buy/sell/open counts
and closed-position win denominator. Run explicit zero-fee and configured-fee
scenarios. Use the same universe for buy-and-hold.

- [ ] **Step 4: Verify GREEN**

Run: `D:/Anaconda/python -m pytest SkinVest_project/tests -v`

Expected: all tests PASS.

### Task 6: Retrain and regenerate deep-model artifacts

**Files:**
- Regenerate: `SkinVest_project/data/models/lstm_c.keras`
- Regenerate: `SkinVest_project/data/models/lstm_d_*.keras`
- Regenerate: `SkinVest_project/data/models/gru.keras`
- Regenerate: associated scaler, map, route, and comparison pickle/JSON files
- Regenerate: validation and test prediction CSV files under `data/preds/`

- [ ] **Step 1: Run aligned LSTM-C training**

Run: `D:/Anaconda/python SkinVest_project/notebooks/train_lstm_c.py`

Expected: model trains with 60-row decision-inclusive windows and saves C
artifacts in `SkinVest_project/data/models/`.

- [ ] **Step 2: Run aligned LSTM-D and GRU training**

Run both training scripts with `D:/Anaconda/python`. Expected: three D models
and one GRU model save successfully.

- [ ] **Step 3: Select route on validation and freeze it**

Run `compare_lstm_cd.py`. Expected: route metadata contains only validation
selection results and no test metrics.

- [ ] **Step 4: Export validation and test predictions**

Run `make_predictions.py --split val` and `--split test`. Expected: canonical
CSV columns validate, with no NaN or duplicate keys.

### Task 7: Regenerate reports and document honest scope

**Files:**
- Regenerate: `SkinVest_project/data/backtest/compare_results.json`
- Regenerate: `SkinVest_project/data/backtest/backtest_results.json`
- Regenerate: `SkinVest_project/data/backtest/backtest_curves.json`
- Modify: `SkinVest_project/docs/Project_Proposal_CSVest.md`
- Modify: `SkinVest_project/docs/team_tasks.md`
- Do not modify: workspace `AGENTS.md`; report corrected figures for a later explicit context update

- [ ] **Step 1: Run test-split comparison**

Use only prediction files with `split=test`. If member-2 tree test predictions
are unavailable, report deep-model test results separately and mark the full
eight-model test table blocked instead of mixing validation rows.

- [ ] **Step 2: Run common-universe backtests**

Generate zero-fee and nonzero-fee results. Verify all ranked models show the
same date range and universe.

- [ ] **Step 3: Update development documentation**

Replace false "all metrics best" language, state the seven-observation
contract, distinguish validation selection from test evaluation, and retain
clear pending markers for unavailable tree test results.

- [ ] **Step 4: Run final verification**

Run:

```powershell
D:/Anaconda/python -m pytest SkinVest_project/tests -v
D:/Anaconda/python -c "from tensorflow import keras; from pathlib import Path; [keras.models.load_model(p) for p in Path('SkinVest_project/data/models').glob('*.keras')]"
```

Expected: all tests pass and all Keras artifacts load.

- [ ] **Step 5: Review workspace boundaries**

Confirm `socProject` remains byte-for-byte unmodified and report generated
artifacts, commands, metrics, and any external dependency on member-2 test
predictions. Do not copy or push without explicit user authorization.
