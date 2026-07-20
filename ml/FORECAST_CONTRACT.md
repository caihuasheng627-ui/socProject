# Forecast Contract v4 (post-delivery)

Integrated from `CSVest_post_delivery_maintenance_2026-07-20` on branch
`post-delivery/forecast-contract-v4`.

## Contract

```text
decision observation: t
input window:         t-59 ... t  (60 observations, includes decision day)
prediction target:    7th later valid daily observation for the same item
```

Canonical prediction CSV columns:

```text
split,date,target_date,market_hash_name,current_price,
actual_future_price,predicted_price,horizon_steps
```

## Fair test comparison (35,229 rows · 154 items)

| Model | RMSE | MAE | MAPE | R² |
|---|---:|---:|---:|---:|
| LSTM-C | 44.3745 | 6.2668 | 10.95% | 0.9374 |
| LSTM-D | 52.0901 | 7.0234 | 7.72% | 0.9137 |
| Hybrid | 52.0900 | 7.0204 | 10.69% | 0.9137 |
| RF | 52.8212 | 7.2600 | 6.26% | 0.9113 |
| LightGBM | 58.3566 | 8.4611 | 6.34% | 0.8917 |
| XGBoost | 61.7568 | 9.1675 | 7.93% | 0.8787 |

Hybrid val route: `low→C`, `mid/high→D` (see `models/lstm_hybrid_route.json`).

## Preserved course-delivery scripts

ARIMA and tree training entrypoints were **not** overwritten:

- `01_arima_baseline.py`
- `02_xgboost_reg_cls.py`
- `03_lightgbm_rf.py`
- `04_feature_importance.py`
- `run_all.py`
- `utils.py`

Pre-integration copies: `ml/_backup_pre_contract_20260720/`.

## Docs

See `docs/post-delivery/` for design specs, handoff notes, and the original package README.
