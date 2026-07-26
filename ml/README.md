# CSVest ML — 模型训练 / 回测 / 产物说明

> 品牌对外：**CSVest** · 数据: `data/` (train/val/test, 日频)
> 环境: Python 3.11+ + TensorFlow + sklearn/xgboost/lightgbm
> **预测规范 v5（Seq2Seq 多步输出）**: 见 [`FORECAST_CONTRACT.md`](FORECAST_CONTRACT.md)

## 目录

| 路径 | 内容 |
|---|---|
| `forecast_contract.py` | 多步目标（1~7/30 天）、60 步窗口、预测 CSV 契约（单列 + 多列双格式）|
| `feature_engineering.py` | 统一特征工程，入口 `build_features(df)` |
| `tree_features.py` | 树模型 split 加载与特征数组 |
| `gpu_config.py` | GPU/CPU 自动检测 + `tf.data.Dataset` 流水线 |
| `train_lstm_c.py` | LSTM-C 面板 Embedding — **Dense(7) 输出 7 天每日价格** |
| `train_lstm_d.py` | LSTM-D 价格分层 3 组 — **Dense(7) 输出 7 天每日价格** |
| `train_gru.py` | GRU 变体 top10 — **Dense(7) 输出 7 天每日价格** |
| `train_seq2seq_30d.py` | 🆕 **Seq2Seq 30 天趋势模型** — 分位数回归 P10/P50/P90 |
| `backtest.py` | 公平回测（兼容新旧预测格式）|
| `make_predictions.py` | 导出 7 天多列 + 30 天趋势预测 CSV（`--model 7d/30d/all`）|
| `make_predictions_trees.py` | RF/LightGBM/XGBoost 规范预测导出 |
| `compare_lstm_cd.py` | C vs D 对垒 + Hybrid 路由写入（适配多列格式）|
| `compare_models.py` | 六模型同样本比较 + `--per-day` 逐日指标 |
| `01_arima_baseline.py` ~ `04_*.py`, `run_all.py`, `utils.py` | 课程交付保留（含 ARIMA）|
| `models/` | `.keras` / `.pkl` + `lstm_hybrid_route.json` + `seq2seq_30d.keras` |
| `preds/` | `pred_*_{val,test}.csv`（7 天模型多列 + 30 天模型 P10/P50/P90 列）|
| `outputs/` | 比较 JSON / 回测 / SHAP |
| `tests/` | 单元测试（22 passed）|

## 模型架构总览

### 7 天精确预测（Seq2Seq Dense(7)）

```
Input: (60, 15) 历史特征
  │
  ▼
LSTM(50) × 2 + Dropout(0.2)
  │
  ▼
Dense(7) → [day1, day2, ..., day7]  log_price
  │
  ▼
expm1 → 7 天每日 USD 价格
```

| 模型 | 特点 | 输出维度 |
|------|------|---------|
| LSTM-C | 双输入：价格序列 + 物品 Embedding(8)；全量 154 件 | `(n, 7)` |
| LSTM-D | 单输入；按价格三层分组独立训练 | `(n, 7)` |
| Hybrid | 路由：low→LSTM-C, mid/high→LSTM-D | `(n, 7)` |
| GRU | 单输入；仅 top10 高流动性物品 | `(n, 7)` |

### 30 天趋势预测（分位数回归）

```
Input: (60, 15) 历史特征
  │
  ▼
LSTM(64) × 2 + Dropout(0.2)
  │
  ▼
Dense(90) → Reshape(30, 3)
  │             ↑ 每天 [P10, P50, P90]
  ▼
Pinball loss (τ=0.10, 0.50, 0.90) + Spread penalty
```

## 预测规范 v5

```text
决策观测：t
输入窗口：t-59 ... t（含决策日，共 60 观测）
7 天模型目标：同一物品后续 1~7 天每日有效日频观测 [day1..day7]
30 天模型目标：同一物品后续 1~30 天每日有效日频观测 [P10, P50, P90]
```

### 7 天预测 CSV 列（多列格式）

`split, date, target_date, market_hash_name, current_price, actual_future_price, actual_future_price_d1..d7, predicted_price_d1..d7, horizon_steps`

### 30 天预测 CSV 列

`split, date, target_date, market_hash_name, current_price, actual_future_price, trend_p10_d1..d30, trend_p50_d1..d30, trend_p90_d1..d30, horizon_steps`

## 快速开始

```bash
# 1. 训练 7 天模型
python train_lstm_c.py
python train_lstm_d.py
python train_gru.py

# 2. 训练 30 天趋势模型
python train_seq2seq_30d.py

# 3. 导出预测 CSV
python make_predictions.py --model 7d --split val
python make_predictions.py --model 7d --split test
python make_predictions.py --model 30d --split val
python make_predictions.py --model 30d --split test

# 4. 生成 Hybrid 路由
python compare_lstm_cd.py

# 5. 全模型对比（含逐日指标）
python compare_models.py --split test --per-day

# 6. 回测
python backtest.py \
  "LSTM-C=preds/pred_lstm_c_test.csv" \
  "LSTM-D=preds/pred_lstm_d_test.csv" \
  "Hybrid=preds/pred_lstm_hybrid_test.csv" \
  --capital 10000
```

## 公平 test 比较（旧版 v4 单点口径 · 35,229 条 · 154 件 · 2026-07-20）

> ⚠️ 以下为旧版 Dense(1) 单点预测指标。新版 Dense(7) Seq2Seq 指标运行 `compare_models.py --per-day` 后更新。

| 模型 | RMSE | MAE | MAPE | R² |
|---|---:|---:|---:|---:|
| LSTM-C | 44.3745 | 6.2668 | 10.95% | 0.9374 |
| LSTM-D | 52.0901 | 7.0234 | 7.72% | 0.9137 |
| Hybrid | 52.0900 | 7.0204 | 10.69% | 0.9137 |
| RF | 52.8212 | 7.2600 | **6.26%** | 0.9113 |
| LightGBM | 58.3566 | 8.4611 | 6.34% | 0.8917 |
| XGBoost | 61.7568 | 9.1675 | 7.93% | 0.8787 |

- **Hybrid val 路由**: low→LSTM-C，mid/high→LSTM-D（`models/lstm_hybrid_route.json`）
- LSTM-C 的 RMSE/MAE/R² 最优；RF MAPE 最低

## 推理要点

### 7 天模型
1. 取物品最近 60 天（含决策日）→ `build_features`
2. `scaler.transform` → `model.predict` → 输出 `(1, 7)` log_price
3. `y_scaler.inverse_transform` → `expm1` → 7 天每日 USD 价格
4. Hybrid：查 `lstm_hybrid_route.json`；未知物品 LSTM-C 用 `__UNK__`，LSTM-D 按当前价 vs boundaries 分组

### 30 天模型
1. 同上取 60 天窗口
2. `model.predict` → 输出 `(1, 30, 3)` → `[P10, P50, P90]` 每天
3. 逐 quantile `y_scaler.inverse_transform` → `expm1` → USD

## 测试

```bash
pytest ml/tests -q   # 期望 22 passed
```

## 文档

- `docs/post-delivery/` — 设计、交接、原维护包 README
- `docs/post-delivery/INTEGRATION_GUIDE.md` — 接入说明

---
*CSVest ML · forecast-contract-v5 · Seq2Seq Dense(7) + 30d Quantile · 2026-07-23*
