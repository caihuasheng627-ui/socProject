# Tree Prediction Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate canonical RF, LightGBM, and XGBoost val/test predictions and rebuild the complete comparable evaluation.

**Architecture:** Reuse the canonical continuous feature panel and target metadata. A dedicated exporter trains fixed tree configurations for val and test, validates every output through `forecast_contract.py`, and saves final deployment bundles locally.

**Tech Stack:** Python 3.13, pandas, NumPy, scikit-learn, LightGBM, XGBoost, pytest.

---

### Task 1: Lock the tree export contract with tests

**Files:**
- Create: `tests/test_tree_predictions.py`
- Create: `notebooks/make_predictions_trees.py`

- [ ] Write tests for train-only val fitting, train+val test fitting, eight-column output, target metadata, and positive price floor.
- [ ] Run `D:/Anaconda/python -m pytest SkinVest_project/tests/test_tree_predictions.py -q` and verify the missing API fails.
- [ ] Add the minimal pure helper functions required by the tests.
- [ ] Rerun the focused tests and verify they pass.

### Task 2: Implement model training and export

**Files:**
- Modify: `notebooks/tree_features.py`
- Modify: `notebooks/make_predictions_trees.py`

- [ ] Load and encode the continuous train/val/test panel once.
- [ ] Add fixed RF, LightGBM, and XGBoost factories matching member-2 defaults.
- [ ] Train on train for val and train+val for test.
- [ ] Export `pred_{rf,lightgbm,xgboost}_{val,test}.csv` through `validate_prediction_frame`.
- [ ] Save train+val model bundles with feature list, fit split, horizon, and train price floor.

### Task 3: Run the real tree pipeline

**Files:**
- Generate: `data/preds/pred_{rf,lightgbm,xgboost}_{val,test}.csv`
- Generate: `data/models/{rf_reg,lightgbm_reg,xgb_reg}.pkl`

- [ ] Run `D:/Anaconda/python -u SkinVest_project/notebooks/make_predictions_trees.py --split both`.
- [ ] Validate all six files for exact columns, split, horizon, duplicates, finite positive prices, and row coverage.
- [ ] Load all three saved model bundles and verify feature metadata.

### Task 4: Rebuild Hybrid and evaluation artifacts

**Files:**
- Generate: `data/models/lstm_hybrid_route.json`
- Generate: `data/preds/pred_lstm_hybrid_{val,test}.csv`
- Generate: `data/backtest/compare_results*.json`
- Generate: `data/backtest/backtest_{results,curves}.json`

- [ ] Rerun `compare_lstm_cd.py` on the latest val C/D predictions.
- [ ] Rerun `make_predictions.py --split val` and `--split test`.
- [ ] Rerun `compare_models.py` for val and test; require `status=complete`.
- [ ] Rerun test backtest for the six full-coverage models with fees 0 and 0.025.

### Task 5: Final verification and handoff

**Files:**
- Modify: `docs/forecast_fix_review_handoff.md`

- [ ] Run the full pytest suite with cache disabled.
- [ ] Parse every Python file and load every Keras/tree model artifact.
- [ ] Independently recompute CSV metrics and compare with JSON.
- [ ] Confirm `socProject/` remains clean and no push occurred.
- [ ] Update the handoff with final metrics, route, backtest, and exact push candidates.

