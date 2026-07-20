# Tree Prediction Contract Design

## Goal

Produce canonical validation and test predictions for Random Forest, LightGBM,
and XGBoost without modifying `socProject/`, then regenerate the complete
six-model comparison and backtest.

## Prediction specification

Each output row represents decision observation `t` and the seventh later
valid observation for the same item. Files contain exactly:

```text
split,date,target_date,market_hash_name,current_price,
actual_future_price,predicted_price,horizon_steps
```

The feature row includes information available through `t`. `TargetDate`,
`TargetPrice`, and `Target` must remain in the same split as the decision row.

## Training and selection

- Validation export: fit on train and predict val.
- Test export: fit on train+val and predict test after model settings are fixed.
- Categorical encoders are fit on train only through the canonical feature
  pipeline.
- Prediction prices are inverse-log transformed and floored at the minimum
  price observed in train.
- Hyperparameters match the existing member-2 export defaults so this work
  repairs the evaluation contract rather than introducing new tuning.

## Components

- `tree_features.py` prepares train/val/test once and exposes canonical arrays.
- `make_predictions_trees.py` trains the three models, exports canonical CSVs,
  and saves final train+val model bundles with feature and split metadata.
- `forecast_contract.py` remains the authority for output validation.
- Unit tests cover split selection, canonical output construction, and price
  flooring before real training is run.

## Final evaluation

After six tree CSVs exist, rerun the Hybrid route on the latest C/D val files,
regenerate Hybrid val/test predictions, and run comparison/backtest. The main
regression table contains LSTM-C, LSTM-D, Hybrid, RF, LightGBM, and XGBoost on
an identical test intersection. GRU top10 and ARIMA representative-item results
remain separate because their coverage is not comparable.

## Failure handling

Any missing column, mixed split, non-seven horizon, duplicate item/date,
non-positive price, inconsistent truth, or empty model prediction fails the
pipeline. A failed GPU option falls back to CPU; the default is CPU for
reproducibility.

