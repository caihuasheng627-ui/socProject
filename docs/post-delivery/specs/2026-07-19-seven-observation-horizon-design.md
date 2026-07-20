# Seven-Observation Forecast and Evaluation Design

## Goal

Make the CSVest ML pipeline use one auditable forecast contract: for each item,
features available at observation `t` predict the price at observation `t+7`.
Validation selects models and the Hybrid route; the held-out test split reports
final regression and backtest results.

## Scope and repository boundary

- Modify only `SkinVest_project/`.
- Do not modify, copy over, commit, or push files under `socProject/`.
- Retrain LSTM-C, LSTM-D, and GRU in the development workspace.
- Make comparison and backtest scripts validate their input contracts.
- Existing validation prediction and comparison files become historical results;
  regenerated test results replace them as final-report inputs.
- Tree-model test predictions remain an external input owned by member 2. The
  comparison script must reject validation/test mixtures rather than silently
  ranking incomparable files.

## Canonical forecast contract

The horizon is exactly seven later observations within the same
`market_hash_name`, not seven calendar days. Because the source is daily market
data, reports may call this a seven-day forecast only with the footnote
"seven valid daily observations".

For a decision row at item observation index `t`:

- `decision_date = date[t]`
- `current_price = price[t]`
- input sequence = features from `t-59` through `t`, inclusive
- `target_date = date[t+7]`
- `actual_future_price = price[t+7]`
- `predicted_price` estimates `actual_future_price`

Every prediction CSV must contain:

```text
split,date,target_date,market_hash_name,current_price,actual_future_price,predicted_price,horizon_steps
```

`split` must be `val` or `test`, and `horizon_steps` must equal `7` on every
row. Loaders fail with a clear error for missing columns, mixed splits,
duplicate item/decision-date pairs, nonpositive prices, or non-finite values.

## Continuous panel and leakage controls

Feature calculation uses the chronologically concatenated train, validation,
and test panel so rolling indicators and LSTM lookbacks retain prior history at
split boundaries. Each row keeps its source split and target split.

- A training sample may only have a target in `train`.
- A validation sample may only have a target in `val`.
- A test sample may only have a target in `test`.
- Scalers fit on training samples only.
- Early stopping and Hybrid route selection use validation samples only.
- Test labels are not read during training or route selection.

The seven-step target is produced per item with `shift(-7)`. `target_date` and
`actual_future_price` are produced by the same grouped shift, so metric code
never reconstructs labels using calendar arithmetic.

## LSTM and GRU sequence changes

The sequence builder starts at `t=LOOKBACK-1` and slices
`features[t-LOOKBACK+1:t+1]`. This includes the decision row and always returns
exactly 60 observations. Metadata and target come from that same decision row.

LSTM-C learns embeddings only for training items plus one explicit unknown
item ID. Items first seen in validation or test map to the unknown ID; their
names are never added as randomly initialized dedicated validation IDs.

LSTM-D keeps train-derived price boundaries. Known training items retain their
train-median group. An unseen item is routed at inference using only its
decision-row `current_price`, so no future validation median is used.

GRU continues to cover the ten items selected by training-set liquidity only.
Its results stay in a separate top-10 comparison and are not ranked in the
full-universe main table.

## Model selection and final evaluation

The pipeline has two explicit stages:

1. Train on `train`; use `val` for early stopping, C/D group comparison, and
   the fixed Hybrid rule.
2. Freeze models and routing; generate `test` predictions and report final
   metrics without changing the route or thresholds.

Hybrid remains `high -> C, low/mid -> D` unless the newly aligned validation
comparison disproves it. Any changed route must be selected from validation
only and saved as model metadata before test evaluation.

Regression metrics use `actual_future_price` as truth. MAPE uses a documented
USD floor of `$0.01` only in the denominator. Direction metrics compare
`actual_future_price - current_price` with
`predicted_price - current_price`. ROC AUC is omitted for hard binary direction
outputs unless a continuous score is available.

## Fair backtest

Backtest inputs must share `split`, `horizon_steps`, evaluation dates, and item
universe before their returns are ranked. The script computes the intersection
and reports the resulting date range, item count, and excluded rows.

- Signals use `(predicted_price-current_price)/current_price`.
- Minimum holding period is seven observations for that item, matching the
  forecast contract.
- The post-hoc 200% final-return cap and retroactive curve scaling are removed.
- `buy_count`, `sell_count`, closed positions, and open positions are reported
  separately; `trades` is the total number of executed buys and sells.
- Win rate uses closed positions and states its denominator.
- Zero-fee results and at least one nonzero fee sensitivity are separate output
  scenarios, never merged into one headline number.
- Buy-and-hold uses the exact same common date and item universe.

Raw uncapped results are retained. If risk limits are later required, they must
be implemented as forward-time trading rules and designed separately.

## Files and ownership

Production code remains under `SkinVest_project/notebooks/` to match the current
workspace layout. Tests live under `SkinVest_project/tests/`.

- `feature_engineering.py`: grouped target date/price and split-safe features.
- `train_lstm_c.py`: aligned windows and unknown embedding.
- `train_lstm_d.py`: aligned windows and leakage-free unseen routing.
- `train_gru.py`: aligned windows and training-only item selection.
- `make_predictions.py`: val/test export using the canonical CSV contract.
- `compare_lstm_cd.py`: validation-only route selection.
- `compare_models.py`: contract validation and future-price metrics.
- `backtest.py`: common-universe forward simulation without retroactive cap.
- `tests/`: horizon, window, cold-start, metric, contract, and backtest tests.

The duplicate feature-engineering files under `SkinVest_project/data/` are not
used as alternate implementations. They will become thin compatibility imports
of the canonical notebook module or be clearly deprecated without changing
external behavior.

## Tests and acceptance criteria

Automated tests must prove:

- grouped targets never cross items and point to the seventh later observation;
- target date, target price, and horizon metadata remain aligned across gaps;
- every 60-row sequence includes its decision row and excludes later rows;
- scalers are fit on training data only;
- unseen items use the unknown embedding or decision-time D routing;
- comparison metrics use `actual_future_price`, not `current_price`;
- mixed split/horizon prediction files are rejected;
- common-universe backtests give every model identical dates and items;
- holding period, fees, buy/sell counts, and open positions are correct;
- no retroactive cap or future-dependent equity scaling remains.

Completion requires all tests to pass, all deep-learning model artifacts to
load, validation and test prediction contracts to validate, and regenerated
JSON results to agree with independently recomputed metrics. Full retraining
commands and observed outputs must be recorded. Team-repository transfer and
push remain a later explicit user-authorized step.
