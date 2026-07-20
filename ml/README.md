# CSVest ML — 模型训练 / 回测 / 产物说明

> 品牌对外：**CSVest** · 数据: `data/` (train/val/test, 日频)
> 环境: Python 3.11+ + TensorFlow + sklearn/xgboost/lightgbm
> **预测规范 v4（交付后维护合入）**: 见 [`FORECAST_CONTRACT.md`](FORECAST_CONTRACT.md)

## 目录

| 路径 | 内容 |
|---|---|
| `forecast_contract.py` | 七观测目标、60 步窗口、预测 CSV 契约、冷启动路由 |
| `feature_engineering.py` | 统一特征工程，入口 `build_features(df)` |
| `tree_features.py` | 树模型 split 加载与特征数组 |
| `train_lstm_c.py` `train_lstm_d.py` `train_gru.py` | 深度学习训练 |
| `backtest.py` | 公平回测（无事后 cap） |
| `make_predictions.py` | C/D/Hybrid/GRU 规范 val/test 预测导出 |
| `make_predictions_trees.py` | RF/LightGBM/XGBoost 规范预测导出 |
| `compare_lstm_cd.py` | C vs D 对垒 + Hybrid 路由写入 |
| `compare_models.py` | 六模型同样本比较 |
| `01_arima_baseline.py` ~ `04_*.py`, `run_all.py`, `utils.py` | 课程交付保留（含 ARIMA） |
| `models/` | `.keras` / `.pkl` + `lstm_hybrid_route.json` |
| `preds/` | `pred_*_{val,test}.csv` |
| `outputs/` | 比较 JSON / 回测 / SHAP |
| `tests/` | 单元测试（22 passed） |

## 预测规范 v4

```text
决策观测：t
输入窗口：t-59 ... t（含决策日，共 60 观测）
预测目标：同一物品后续第 7 个有效日频观测
```

CSV 列：`split,date,target_date,market_hash_name,current_price,actual_future_price,predicted_price,horizon_steps`

## 公平 test 比较（35,229 条 · 154 件 · 2026-07-20）

| 模型 | RMSE | MAE | MAPE | R² |
|---|---:|---:|---:|---:|
| LSTM-C | 44.3745 | 6.2668 | 10.95% | 0.9374 |
| LSTM-D | 52.0901 | 7.0234 | 7.72% | 0.9137 |
| Hybrid | 52.0900 | 7.0204 | 10.69% | 0.9137 |
| RF | 52.8212 | 7.2600 | **6.26%** | 0.9113 |
| LightGBM | 58.3566 | 8.4611 | 6.34% | 0.8917 |
| XGBoost | 61.7568 | 9.1675 | 7.93% | 0.8787 |

- **Hybrid val 路由**: low→LSTM-C，mid/high→LSTM-D（`models/lstm_hybrid_route.json`）
- test 上 LSTM-C 的 RMSE/MAE/R² 最优；RF MAPE 最低；Hybrid **未**全面超过 LSTM-C
- 旧版 val 口径（MAPE 5.49% 等）在 `_backup_pre_contract_20260720/`，汇报时勿混用

## 推理要点

1. 取物品最近 60 天（含决策日）→ `build_features`
2. scaler.transform → model.predict → `expm1` 还原美元价
3. Hybrid：查 `lstm_hybrid_route.json`；未知物品 LSTM-C 用 `__UNK__`，LSTM-D 按当前价 vs boundaries 分组

## 测试

```bash
pytest ml/tests -q   # 期望 22 passed
```

## 文档

- `docs/post-delivery/` — 设计、交接、原维护包 README
- `docs/post-delivery/INTEGRATION_GUIDE.md` — 接入说明

---
*CSVest ML · forecast-contract-v4 · 2026-07-20*
