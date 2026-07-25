# Volume-Free Full Model Retraining Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retrain, evaluate, and deploy every active CSVest model without volume features, including a Keras 30-observation quantile trend displayed beside the seven-observation forecast.

**Architecture:** Centralize feature lists and artifact validation, keep long-history chronological splits, use Keras for all sequence models, and preserve canonical CSV comparison boundaries. The public prediction service composes validated 7- and 30-observation inference from the same current database window.

**Tech Stack:** Python 3.13, TensorFlow/Keras, scikit-learn, XGBoost, LightGBM, FastAPI, SQLite, Vue 3 CDN, ECharts, pytest.

---

### Task 1: Shared Feature Contract

**Files:**
- Create: `ml/model_features.py`
- Modify: `ml/train_lstm_c.py`
- Modify: `ml/train_lstm_d.py`
- Modify: `ml/train_gru.py`
- Modify: `ml/train_seq2seq_30d.py`
- Modify: `ml/tree_features.py`
- Modify: `ml/utils.py`
- Modify: `ml/shap_analysis.py`
- Modify: `ml/shap_cls_analysis.py`
- Test: `ml/tests/test_model_feature_contract.py`

- [ ] Write tests asserting sequence/tree lists contain no volume field and every
  training module imports the same sequence list.
- [ ] Run the focused test and confirm RED.
- [ ] Implement shared immutable feature lists, manual GRU item list, and contract
  validation; update all consumers.
- [ ] Run focused and existing feature tests.

### Task 2: Keras 30-Observation Model and Safe Artifacts

**Files:**
- Replace: `ml/train_seq2seq_30d.py`
- Modify: `ml/make_predictions.py`
- Create: `ml/artifact_io.py`
- Test: `ml/tests/test_trend_30d.py`
- Test: `ml/tests/test_training_artifacts.py`

- [ ] Write tests for `(batch,30,3)` output, quantile loss, interval sanitization,
  `.keras` reload, and 30-day CSV schema.
- [ ] Confirm RED against the PyTorch implementation and mismatched exporter.
- [ ] Implement Keras quantile training, checkpoint-first persistence, reload
  validation, Keras prediction export, and trend metrics JSON.
- [ ] Run a reduced one-epoch end-to-end smoke workflow.

### Task 3: Dataset and All-Model Preflight

**Files:**
- Create: `ml/preflight_training.py`
- Create: `ml/outputs/volume_free_dataset_report.json` (generated)
- Modify: `ml/tests/test_sequences.py`
- Modify: `ml/tests/test_tree_features.py`

- [ ] Audit chronological split ranges, duplicates, finite features, same-split
  targets, feature dimensions, and manual GRU coverage.
- [ ] Exercise one batch through each sequence architecture and fit tiny tree models.
- [ ] Save/reload all temporary artifacts and sample CSVs.
- [ ] Abort full training unless every preflight check passes.

### Task 4: Full Retraining and Prediction Export

**Files:**
- Regenerate: `ml/models/*`
- Regenerate: `ml/preds/*_{val,test}.csv`
- Regenerate: `ml/outputs/*results*.json`

- [ ] Train LSTM-C, LSTM-D groups, GRU, and Keras 30-day model with monitored logs.
- [ ] Train RF/LightGBM/XGBoost regressors for val and test canonical exports.
- [ ] Retrain XGBoost/LightGBM/RF classifiers and persist fitted bundles.
- [ ] Export validation C/D, freeze Hybrid route, then export final val/test Hybrid.
- [ ] Export and validate 30-day val/test quantile predictions.

### Task 5: Comparison and Modern Evaluation

**Files:**
- Modify: `ml/compare_models.py`
- Create: `ml/evaluate_trend_30d.py`
- Create: `ml/evaluate_modern.py`
- Regenerate: `ml/outputs/compare_results_{val,test}.json`
- Generate: `ml/outputs/trend_30d_results_{val,test}.json`
- Generate: `ml/outputs/modern_180d_evaluation.json`

- [ ] Compare all seven-observation regressors on common test keys.
- [ ] Evaluate P50 accuracy, interval coverage/width/crossing, and horizon slices.
- [ ] Evaluate the 2026 database window by known/unknown item and price group.
- [ ] Tune only when metrics show instability, then repeat held-out evaluation.

### Task 6: Backend Deployment and Frontend Trend Band

**Files:**
- Modify: `backend/model_loader.py`
- Modify: `backend/prediction_service.py`
- Modify: `backend/tests/test_live_model_loader.py`
- Modify: `backend/tests/test_prediction_service.py`
- Modify: `api-spec/openapi.yaml`
- Modify: `app.js`
- Modify: `index.html`
- Modify: `i18n.js`

- [ ] Test database-only 30-day inference, anchor/date validation, and lack of 30%
  trend breaker.
- [ ] Load the Keras trend artifact and return `trend30d` without weakening seven-day
  validation.
- [ ] Render P10/P50/P90 after the seven-day forecast with distinct colors, a shaded
  interval, legend, and trend label.
- [ ] Run backend/ML tests, JS/YAML checks, API real-item smoke, and frontend HTTP smoke.

### Task 7: Branch Handoff

- [ ] Confirm no merge to `main` occurred.
- [ ] Record model metrics, training durations, artifact versions, known limitations,
  and exact local startup commands.
- [ ] Leave the completed feature branch and services stopped for user inspection.
