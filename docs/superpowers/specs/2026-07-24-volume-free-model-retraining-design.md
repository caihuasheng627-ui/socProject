# Volume-Free Model Retraining Design

## Scope

Retrain and deploy every active prediction model without transaction-volume inputs.
All work stays on `fix/online-prediction-interface`; no merge to `main` is allowed.
The source CSV may retain `daily_volume` for auditability, but no model feature list,
scaler, training tensor, online tensor, or SHAP feature list may contain a volume field.

## Shared Feature Contract

Create one shared ML feature module with:

- 13 sequence features for LSTM-C, LSTM-D, GRU, and the 30-observation model;
- 21 tree features for RF, LightGBM, and XGBoost;
- a guard that rejects feature names containing `volume`;
- a serializable contract version included in model metadata.

Feature engineering may continue computing legacy volume columns because existing raw
datasets and tools reference them. Training and serving select only the shared
volume-free lists.

## Models

### Seven-Observation Models

- LSTM-C: Keras, 60-observation input, item embedding, Dense(7).
- LSTM-D: Keras, three train-only price groups, Dense(7).
- GRU: Keras, Dense(7), using a manually frozen list of ten representative liquid
  items. The list is no longer derived during training from `daily_volume`.
- Hybrid: route selected only on validation MAE, RMSE tie-breaker.

### Trees and Classifiers

Retrain RF, LightGBM, and XGBoost regressors against the same seventh-observation
target. Retrain XGBoost, LightGBM, and RF classifiers because their feature contract
also changes. Canonical val/test prediction files remain the source for fair model
comparison; classifiers remain model-lab artifacts and do not enter the regression
comparison table.

### Thirty-Observation Trend

Replace the PyTorch implementation with Keras. The model uses the same 60 x 13 input
tensor and outputs `(30, 3)` for P10/P50/P90. Training uses pinball loss plus a
quantile-crossing penalty. Online post-processing sorts quantiles, enforces positive
prices, and limits each interval edge to at most 40% from P50. P50 itself is not
subject to the seven-day 30% circuit breaker.

Artifacts:

- `models/seq2seq_30d.keras`
- `models/seq2seq_30d_scaler.pkl`
- `preds/pred_seq2seq_30d_{val,test}.csv`
- `outputs/trend_30d_results_{val,test}.json`

## Training Safety

Before full training, run a one-epoch reduced-data smoke workflow for every model
family. It must build windows, fit, save to a temporary artifact, reload, predict,
validate finite shapes, and write/read a sample prediction CSV. Full deep-model
training uses best-checkpoint persistence during training, then atomically replaces
the deployed artifact after reload validation. Scalers and maps are written before
training and validated against model input/output dimensions.

## Evaluation and Tuning

1. Generate canonical val/test predictions for all seven-observation regressors.
2. Freeze Hybrid routing using validation only.
3. Compare all regressors on identical test keys.
4. Evaluate 30-day P50 MAE/RMSE/MAPE/R2, P10-P90 coverage, average interval width,
   crossing rate, and day 7/14/21/30 metrics.
5. Run a separate 2026 database walk-forward evaluation, reporting known and unknown
   items separately. This modern window is evaluation-only initially.
6. Fine-tune only if the volume-free long-history model is materially unstable on
   modern data. Never train and report final metrics on the same modern rows.

## Serving Contract

`/api/predict` remains database-only. It returns the validated seven-observation LSTM
prediction plus nullable `trend30d` containing 30 P10/P50/P90 values. The 30-day model
uses the same database decision date and price anchor but has no 30% output breaker.
Invalid/non-finite/non-positive trend output is unavailable without suppressing a
valid seven-day result.

The frontend draws the existing seven-day line first, followed by the 30-day P50 line
and P10/P90 boundaries with a translucent band. A legend and compact state label make
clear that the latter is a probabilistic 30-observation trend, not an exact price
promise.

## Acceptance Criteria

- No active model feature metadata contains a volume field.
- All saved artifacts reload and produce finite outputs with contract-correct shapes.
- Canonical comparisons and 30-day evaluation files are regenerated.
- Online seven-day and 30-day inference use the latest database window only.
- The frontend displays both horizons on one chart with a visible trend interval.
- Backend, ML tests, JavaScript syntax checks, API smoke tests, and real-item inference
  pass on the feature branch.
