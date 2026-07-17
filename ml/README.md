# SkinVest ML — 模型训练 / 回测 / 产物说明

> 数据: `data/` (train 147件 / val 154件 / test 154件, 2023-08 ~ 2026-06 日频)
> 环境: Python 3.13 + TensorFlow 2.21 + sklearn/xgboost/lightgbm, 详见 team_tasks.md 装包节

## 目录

| 路径 | 内容 | 负责 |
|---|---|---|
| `feature_engineering.py` | 统一特征工程 (31列), 入口 `build_features(df)` | 组员1+2 共用 |
| `train_lstm_c.py` `train_lstm_d.py` `train_gru.py` | 深度学习训练脚本 | 组员1 |
| `backtest.py` | 回测引擎 (逐日逐笔) | 组员1 |
| `make_predictions.py` | C/D/Hybrid/GRU 预测导出 CSV, **也是推理参考实现** | 组员1 |
| `compare_lstm_cd.py` | C vs D 分组对垒评估 (Hybrid 路由依据) | 组员1 |
| `01_arima_baseline.py` ~ `04_feature_importance.py`, `run_all.py`, `utils.py` | 树模型/统计基线管线 | 组员2 |
| `models/` | 训练产物 (.keras / .pkl), 组员3 从这里加载 | 组员1 |
| `outputs/` | 指标 JSON / SHAP 图 (树模型侧) | 组员2 |

## 深度学习模型指标 (val, 2026-07-17)

| 模型 | 覆盖 | MAE | RMSE | MAPE | R² |
|---|---|---|---|---|---|
| LSTM-C (面板Embedding) | 154件 | $2.2339 | $13.2489 | 6.65% | 0.9891 |
| LSTM-D (分3组独立) | 154件 | $2.3571 | $13.7070 | 5.57% | 0.9883 |
| **Hybrid (部署方案)** ⭐ | 154件 | **$2.2308** | **$13.2487** | **5.49%** | **0.9891** |
| GRU | 10件高流动性 | $0.0463 | $0.0803 | 11.02% | 0.9584 |

- **Hybrid = high 组走 LSTM-C, low/mid 组走 LSTM-D**, 四项指标严格优于任一单模型
- 分组边界 (train 逐物品中位价 55%/87% 分位): low < $0.16 ≤ mid ≤ $72.17 < high
- GRU 与 C/D 不同口径; 同 10 件公平对比: C 10.65% / D 10.81% / GRU 11.02% MAPE
- 树模型指标见 `outputs/*.json` (组员2 维护)

## models/ 文件清单 (组员3 加载用)

```
lstm_c.keras + lstm_c_scaler.pkl {x_scaler,y_scaler} + lstm_c_item_map.pkl {物品名→ID}
lstm_d_low/mid/high.keras + lstm_d_scalers.pkl {组:{x_scaler,y_scaler}}
lstm_d_group_map.pkl {boundaries:(q1,q2), item_group:{物品名→组}}   ← Hybrid 路由查表
gru.keras + gru_scaler.pkl + gru_items.pkl [10件物品名]
lstm_cd_group_comparison.json                                      ← C/D 对垒明细
```

**推理流程 (照 `make_predictions.py` 抄即可):**
1. 取该物品最近 60 天 → `build_features` → 15 特征列 (顺序见 `train_lstm_c.FEATURE_COLS`)
2. 用对应模型的 `x_scaler.transform` 标准化 → `model.predict`
3. `y_scaler.inverse_transform` → `np.expm1` 还原美元价 (预测的是 7 天后)
4. Hybrid 路由: `item_group` 查组 (新物品按中位价 vs boundaries 落组), high→C, 其余→D

## 回测

```bash
python backtest.py LSTM-C=preds/pred_lstm_c.csv XGBoost=<组员2的csv> ...
# 可选: --capital 10000 --fee 0.025 --buy-th 0.02
```

- 规则: 7天预测涨幅 ≥+2% 买入 / ≤-2% 卖出 / 之间不动; 等权分仓逐物品模拟
- 输出: `outputs/backtest/` — 每模型资金曲线 CSV + `backtest_curves.json` (前端格式, 归一化100, 自动附买入持有基准) + `backtest_results.json` (收益/回撤/胜率)

**预测 CSV 契约 (组员2 的四个模型也按此导出):**

```
date, market_hash_name, current_price, predicted_price
```

date = 决策日, predicted_price = 该日起 7 天后的预测价 (真实美元)。样例: 跑 `make_predictions.py` 生成的 `preds/pred_lstm_c.csv`。

## 红线约定

- **严禁跨物品拼序列**: 滑动窗口必须 `groupby(market_hash_name)` 后逐物品构建
- **价格 log1p 变换**: 深度模型在 log 空间训练, 推理后 `expm1` 还原
- Scaler 只在 train 上 fit, val/test/线上一律复用 (防泄漏)
- 树模型交叉验证用 `TimeSeriesSplit`, 严禁随机切分

---
*组员1 维护, 2026-07-17*
