# Calibrated Hybrid V2 Design

## Goal

Produce one explainable seven-day forecast and one naturally connected 30-day trend that cannot visibly drift more than 30% from the current observed price. The implementation must use only information available on the decision date.

## Architecture

The existing LSTM-C and LSTM-D models remain frozen historical base learners. A lightweight Hybrid V2 adapter is fitted on rolling CS2-era observations from the current SQLite price history and learns horizon- and price-tier-specific convex weights for LSTM-C, LSTM-D, and a recent-trend baseline. A deterministic backend calibration layer then applies model-disagreement shrinkage and a smooth horizon-aware return bound to the seven-day and 30-day paths.

The backend is the only source of calibrated prices. The frontend renders the calibrated response and exposes concise calibration evidence; it does not independently change prices.

## Data Safety

- Features use only observations at or before the decision date.
- Targets use the next 1-7 observations only during offline adapter training and evaluation.
- Adapter splits are global chronological splits; validation and test never select weights.
- The latest 180 calendar days are used when available.
- Daily volume is excluded from both training and inference.
- The original raw base-model outputs remain available in response metadata for auditability.

## Hybrid V2

For horizon `h`, convert every candidate path to a log return from the current price. Fit non-negative weights on a finite grid, constrained to sum to one:

`r_h = w_c * r_c,h + w_d * r_d,h + w_recent * r_recent,h + bias_h`

Weights and the median residual bias are selected using training data and accepted using validation MAE. Separate weights are allowed for low, mid, and high price tiers when a tier has enough samples; otherwise the global weights are used. The artifact is a JSON file containing the feature contract, training dates, weights, validation metrics, and test metrics.

## Runtime Calibration

Recent price statistics are derived from the last 60 observations. The raw hybrid return is shrunk toward a robust recent-trend baseline when LSTM-C and LSTM-D disagree. The cumulative return is then transformed with a smooth bound:

`r_corrected = cap_h * tanh(r_blended / cap_h)`

`cap_h` grows with horizon and recent realized volatility but never exceeds `log(1.30)` upward or `abs(log(0.70))` downward. This prevents discontinuous clipping and applies the correction to the whole path rather than one outlier point.

The calibrated 30-day median is anchored to calibrated day 7. Days 8-11 blend from the seven-day endpoint into the calibrated Keras trend. Days 12-30 retain the Keras direction while using the same smooth return bounds. P10/P90 are calibrated around the median and remain ordered.

## API Contract

The prediction response keeps the existing fields and adds:

- `predictions[0].rawDailyPrices`
- `predictions[0].dailyPrices` as the calibrated authoritative path
- `predictions[0].routeModel = "Hybrid-V2"`
- `calibration.applied`, `method`, `weights`, `modelOutputs`, `recentContext`, `reasonCodes`, and `maxDeviation`

The warning `PREDICTION_OUT_OF_RANGE` is removed from successful responses because the authoritative path is calibrated before validation. Invalid or non-finite model output still produces an unavailable response.

## Frontend

The orange seven-day line uses only calibrated `dailyPrices`. The green 30-day path uses the calibrated trend payload and keeps the existing visual style. Any visible banner or warning copy for a greater-than-30% prediction is removed. A compact explanation reports C/D weights, recent-trend adjustment, model disagreement, and whether smooth compression was applied.

## Evaluation Gates

- No displayed D1-D30 point lies outside 70%-130% of the current price.
- Hybrid V2 validation MAE must not exceed the better accepted candidate by more than 1%.
- P95 absolute percentage error and greater-than-30% raw outlier counts are reported before and after calibration.
- Direction accuracy, MAE, RMSE, MAPE, and D7/D8 handoff are reported.
- Existing API and model tests remain green.

