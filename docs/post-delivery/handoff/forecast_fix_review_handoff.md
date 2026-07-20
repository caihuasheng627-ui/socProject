# CSVest 预测规范修复：完整逻辑与独立审查交接

> 用途：将本文直接交给另一个 AI 或人工审查者，检查本次修复是否合理。
>
> 范围：所有代码改动只发生在 `SkinVest_project/`。`socProject/` 未修改、未复制覆盖、未提交、未 push。
>
> 当前状态（2026-07-20）：LSTM-C、LSTM-D×3、GRU、RF、LightGBM、XGBoost 已按新预测规范完成训练或重训并通过加载检查；14 个 val/test 预测文件、最终 Hybrid 路由、完整六模型对比和双手续费回测均已生成。`socProject/` 未修改、未提交、未 push。

## 1. 原始系统的目标

项目使用 CS2 饰品日频数据，根据每件物品最近 60 个观测预测未来价格。深度模型包括：

- LSTM-C：全物品共享网络，加物品 Embedding。
- LSTM-D：按训练集价格分为 low/mid/high，训练三个独立 LSTM。
- GRU：训练集成交量最高的 10 件物品上的轻量对照模型。

原始标签在 `feature_engineering.py` 中定义为：

```python
Target = log_price.shift(-7)
```

这里的含义是“同一物品后续第 7 个有效观测”，不是严格自然日 `date + 7 days`。

## 2. 原始实现存在的问题

### 2.1 LSTM/GRU 输入与决策日期错位

原始序列循环：

```python
for i in range(LOOKBACK, len(group)):
    X.append(features[i - LOOKBACK:i])
    y.append(Target[i])
    current_price = price[i]
```

其实际语义是：

```text
模型最后看到：i-1
交易当前价：i
目标价格：i+7
```

因此从模型最后可见信息计算，实际跨越 8 个观测；但预测、展示和回测均把它称为 7 天预测。

### 2.2 模型比较使用了错误真值

原 `compare_models.py` 使用：

```python
reg_metrics(df["current_price"], df["predicted_price"])
```

即把决策日当前价当成未来真实价格。模型实际预测的是未来价格，所以主表的 RMSE、MAE、MAPE、R²均不是预测误差。

独立重算曾得到以下验证集指标，证明排名会变化：

| 模型 | RMSE | MAE | MAPE | R² |
|---|---:|---:|---:|---:|
| Hybrid | 10.8944 | 1.8905 | 5.43% | 0.9920 |
| LSTM-C | 10.8945 | 1.8935 | 6.58% | 0.9920 |
| LSTM-D | 11.3465 | 2.0157 | 5.51% | 0.9913 |
| RF | 13.8599 | 2.3907 | **4.24%** | 0.9869 |
| LightGBM | 15.9646 | 2.6082 | 4.57% | 0.9827 |
| XGBoost | 17.7392 | 2.9569 | 4.47% | 0.9786 |

因此“Hybrid 全指标严格最优”不成立；它在 RMSE、MAE、R²上领先，但 RF 的 MAPE 更低。

### 2.3 三种“7 天”定义混用

- 特征标签：同物品 `shift(-7)`，即第 7 个后续观测。
- 深度模型：最后输入为决策日前一个观测，实际跨 8 步。
- 方向指标：原脚本通过 `date + Timedelta(days=7)` 查找自然日价格。

这三者不是同一个预测任务。

### 2.4 验证集新物品处理不严谨

训练集 147 件，验证集 154 件，其中 7 件只出现在验证集。

原 LSTM-C 使用 train+val 物品名称创建 154 个 ID。验证独有物品拥有单独 Embedding，但训练中从未更新，因此是随机向量。

原 LSTM-D 使用验证集完整时间段的价格中位数为新物品分组，相当于使用未来验证期信息决定路由。

### 2.5 最终评估仍使用验证集

- EarlyStopping 使用 val。
- C/D 分组对比和 Hybrid 路由使用 val。
- 回归指标和回测仍使用 val。
- `test.csv` 没有进入最终深度模型评估。

因此模型选择和结果汇报复用同一数据，有选择偏差。

### 2.6 回测存在事后修改历史曲线

原回测先计算每件物品最终收益，如果收益超过 200%，再根据最终结果按比例缩放该物品整条历史权益曲线。

这属于使用未来最终收益反向修改历史净值，不是可执行交易规则，并会改变最大回撤。

另外：

- `trades` 只统计买入，不统计卖出。
- “持有 7 天”实际是 7 个观测序号。
- LSTM 与树模型覆盖的回测日期长度不同，却直接比较累计收益。

## 3. 采用的统一预测规范

用户确认采用“统一 7 个日频观测步”方案。

对每件物品的决策观测 `t`：

```text
决策日期：date[t]
当前价格：price[t]
输入窗口：t-59 ... t，共 60 个观测，包含决策日
目标日期：date[t+7]
未来真实价：price[t+7]
预测目标：price[t+7]
```

报告中如果简称“7 天预测”，必须注明“7 个有效日频观测”。

新的预测 CSV 规范：

```text
split,date,target_date,market_hash_name,current_price,
actual_future_price,predicted_price,horizon_steps
```

要求：

- `split` 只能为单一 `val` 或单一 `test`。
- `horizon_steps` 必须全部为 7。
- 不允许重复 `(market_hash_name, date)`。
- 三个价格列必须为有限正数。

## 4. 逐文件改动

### 4.1 新增 `notebooks/forecast_contract.py`

这是新的预测规范权威模块，主要功能如下。

#### `add_grouped_targets`

按 `market_hash_name` 分组生成：

- `Target`：第 7 个后续观测的 log 价格。
- `TargetPrice`：第 7 个后续观测的美元价格。
- `TargetDate`：目标观测日期。
- `_target_split`：目标所在数据 split。

四个字段由同一个 grouped shift 生成，避免指标脚本再次自行使用自然日计算标签。

#### `build_sequence_windows`

窗口改为：

```python
features[t - lookback + 1:t + 1]
```

因此包含决策行，并且长度严格为 60。

仅保留：

```python
row._split == sample_split
row._target_split == sample_split
```

这允许 val/test 窗口使用此前 split 的历史输入，但禁止标签跨 split。

#### `load_feature_panel`

按时间拼接 train、val、test 后一次计算滚动特征。目的不是用未来数据，而是让 val/test 开头的 MA90、Return、LSTM 60 步历史能够使用过去 split 已经发生的数据。

所有滚动函数是 backward-looking；标签另有 `_target_split` 限制。

#### 冷启动函数

- `build_training_item_map`：只从训练物品创建 ID，再增加 `__UNK__`。
- `encode_item_ids`：未见物品映射到 `__UNK__`。
- `assign_price_groups`：只从训练集计算边界和已知物品组。
- `route_price_group`：已知物品沿用训练组；未知物品只按决策时当前价格分组。

#### `validate_prediction_frame`

统一验证预测 CSV 的列、split、horizon、重复键和数值合法性。

### 4.2 修改 `notebooks/feature_engineering.py`

原来在特征循环内部单独生成 `Target`；现在调用：

```python
df = add_grouped_targets(df)
```

其他已有技术指标计算方式没有主动调整。

需要外部审查者关注：文件目前仍使用逐物品循环和 `df.loc` 写特征，全量面板构建约需 126 秒，正确性优先但性能可优化。

### 4.3 修改 `notebooks/train_lstm_c.py`

主要变化：

- 所有路径改为相对 `__file__`，不再依赖当前工作目录。
- 使用连续面板。
- 训练窗口和验证窗口使用统一 builder。
- 物品 ID 只从 train 建立，加一个 `__UNK__`。
- Scaler 仍只在训练窗口上 fit。
- 模型架构仍是 Embedding + 两层 LSTM，15 个数值特征。
- 原始训练参数已保持不变：batch 32、最多 100 轮、EarlyStopping patience 15。

Embedding 输入维度从原来的 154 个验证已知名称，变为 147 个训练名称加 1 个 `__UNK__`，共 148。

### 4.4 修改 `notebooks/train_lstm_d.py`

主要变化：

- 路径使用 `__file__`。
- 连续面板和决策日包含窗口。
- 分组边界只用训练集。
- 已知训练物品使用训练中位价固定组。
- 未见物品在推理/验证时使用决策行当前价格路由，不使用完整 val 中位价。
- 三个模型仍依次训练：low、mid、high。
- 模型结构和原始训练参数不变。

### 4.5 修改 `notebooks/train_gru.py`

主要变化：

- top10 仍只根据训练集成交量选择。
- 使用连续面板和决策日包含窗口。
- 路径使用 `__file__`。
- 模型结构和原始训练参数不变。

新 GRU 已训练完成：

| 指标 | 新结果 |
|---|---:|
| 训练轮数 | 23 |
| Val MAE | $0.0324 |
| Val RMSE | $0.0653 |
| Val MAPE | 7.21% |
| Val R² | 0.9701 |

旧 GRU MAPE 为 11.02%，新规范下有所改善，但仍只覆盖 top10，不能进入全量主表排名。

### 4.6 重写 `notebooks/make_predictions.py`

新增：

```powershell
--split val
--split test
```

输出分别命名为：

- `pred_lstm_c_val.csv` / `pred_lstm_c_test.csv`
- `pred_lstm_d_val.csv` / `pred_lstm_d_test.csv`
- `pred_lstm_hybrid_val.csv` / `pred_lstm_hybrid_test.csv`
- `pred_gru_val.csv` / `pred_gru_test.csv`

每行直接从窗口 metadata 获得未来真实价和目标日期，而不是事后查 `date+7`。

Hybrid 路由已仅根据新 val 结果冻结为：low 使用 C，mid/high 使用 D。test 只评估该冻结路由，不参与选择。

### 4.7 重写 `notebooks/compare_models.py`

变化：

- 指标真值改为 `actual_future_price`。
- 拒绝 val/test 混用。
- 拒绝非 7 步预测。
- 方向判断统一为未来真实价与当前价之差。
- 硬分类结果不再计算伪 ROC AUC，AUC 暂为 null。
- test 结果输出为 `compare_results_test.json`，并作为最终 `compare_results.json`。

如果组员 2 尚未提供树模型 test 预测，脚本只展示已有的深度模型，不能把树模型 val 结果补入 test 主表。

### 4.8 重写 `notebooks/backtest.py`

变化：

- 输入必须通过新预测规范验证。
- 多模型先取完全相同的 `(item, decision_date)` 交集。
- 删除 200% 事后 cap 和整条曲线反向缩放。
- 持有期明确为 7 个物品观测。
- 分别统计 `buy_count`、`sell_count`、总 `trades`、已平仓数、未平仓数。
- 胜率只使用已平仓头寸并报告其数量。
- 支持同时输出零手续费和非零手续费情景。
- Buy-and-hold 使用相同对齐数据。

外部审查者需关注：当前共同样本实现是所有模型 `(item,date)` 键的交集，但没有进一步强制形成完全矩形的“每件物品每天都有数据”面板；缺失日期通过组合层 `ffill`。这对公平性比原实现更好，但仍值得审查是否需要严格矩形网格。

## 5. 新增自动化测试

目录：`SkinVest_project/tests/`

已覆盖：

- 两件物品的目标不会跨物品。
- 有日期缺口时仍准确指向第 7 个后续观测。
- 目标日期、目标价格、log 目标同步。
- 60 步窗口包含决策行。
- 标签不能跨 split。
- 未见物品使用 `__UNK__`。
- D 未见物品使用当前价格路由。
- 预测规范拒绝错误 horizon 和混合 split。
- 回测不再将 300% 收益压成 200%。
- 手续费会降低最终收益。

最近一次执行结果：

```text
22 passed
```

尚建议补充：

- 多模型共同日期/物品交集的单元测试。
- Scaler 只 fit train 的直接测试。
- 未见物品完整推理测试。
- test 标签未参与模型选择的集成测试。
- 模型输出 CSV 行数和无 NaN 测试。

## 6. 为什么深度模型必须重新训练

旧模型学习：

```text
输入截至 t-1 -> 目标 t+7
```

新模型学习：

```text
输入截至 t -> 目标 t+7
```

序列每个时间位置的含义已经改变，Scaler 拟合样本也改变。LSTM-C 的 Embedding 数量和未知物品处理进一步改变，因此旧权重不能安全复用。

树模型不使用 60 步 LSTM 序列，标签 `shift(-7)` 没变，所以若其训练特征数值确认不变，通常不必重新训练；但必须重新导出 val/test 预测并遵守新预测规范。

## 7. 当前训练状态

- GRU：已完成并保存；val MAE $0.0324、RMSE $0.0653、MAPE 7.21%、R² 0.9701（仅 top10）。
- LSTM-C：54 轮完成；val MAE $2.1113、RMSE $11.4128、MAPE 7.04%、R² 0.9918。
- LSTM-D：low/mid/high 三组已全部完成；合并 val MAE $2.0850、RMSE $10.7332、MAPE 4.48%、R² 0.9928。
- Hybrid：val 路由冻结为 low→C、mid/high→D；val MAE $2.0849。独立 test 上 LSTM-C 优于 Hybrid，不能再声称 Hybrid 全指标最优。

独立 test 指标：LSTM-C MAE $6.2668、RMSE $44.3745、MAPE 10.95%、R² 0.9374；LSTM-D MAE $7.0234、RMSE $52.0901、MAPE 7.72%、R² 0.9137；Hybrid MAE $7.0204、RMSE $52.0900、MAPE 10.69%、R² 0.9137。

树模型独立 test 指标：RF MAE $7.2600、RMSE $52.8212、MAPE 6.26%、R² 0.9113；LightGBM MAE $8.4611、RMSE $58.3566、MAPE 6.34%、R² 0.8917；XGBoost MAE $9.1675、RMSE $61.7568、MAPE 7.93%、R² 0.8787。六模型主表状态为 `complete`，所有模型共享 35,229 条、154 件 test 样本。

最终结论不能写成 Hybrid 全指标最优：LSTM-C 的 test RMSE、MAE、R²最好，RF 的 MAPE 最低；零手续费和 2.5% 手续费回测均由 LSTM-C 收益最高。

所有训练保持原参数：

```text
batch_size = 32
epochs = 100
EarlyStopping patience = 15
ReduceLROnPlateau patience = 7
```

## 8. 剩余展示更新

代码、模型、预测、主表和回测链路已完成。以下事项仍需展示层处理：

1. 策划书、team_tasks、README 和最终展示仍含旧指标，必须改为本节的完整 test 结果，删除“Hybrid 全指标严格最优”。
2. 根目录 `AGENTS.md` 未修改；其中训练中状态和旧指标由用户验收后决定是否更新。
3. SHAP 脚本已有 held-out split 防护；新树模型 bundle 已记录 `fit_split=train+val` 和 23 列特征顺序，可在 test 之外的新数据到齐后生成严格 held-out 解释。
4. test 导出曾发现 LSTM-C 有 1 条负价格；现已按 train split 的最低价格设置推理下界，不读取 val/test 阈值，并有回归测试覆盖。
5. ARIMA 只覆盖少数代表物品、GRU 只覆盖 top10，因此保留附表，不与覆盖 154 件的六模型主表混排。

## 9. 建议外部 AI 重点审查的问题

请不要只检查代码能否运行，重点回答以下问题：

1. 将 horizon 定义为 7 个有效日频观测，而非自然日 +7，是否适合该数据和金融叙事？
2. 全量拼接后计算 backward-looking 特征，并通过 `_target_split` 限制标签，是否彻底避免泄漏？
3. LSTM-C 使用一个共享 `__UNK__` 是否优于为验证新物品保留随机专属 Embedding？
4. LSTM-D 已知物品固定组、未知物品按当前价动态分组，是否会在价格跨边界时产生部署不稳定？
5. Hybrid 路由是否应继续按 low/mid/high 固定，还是应仅在 val 上重新选择后冻结？
6. 回测共同键交集加 `ffill` 是否足够公平，还是应要求严格矩形日期网格？
7. 零手续费和 2.5% 手续费是否适合展示；是否还应加入其他市场手续费情景？
8. 现有 buy/sell 信号和至少持有 7 个观测的规则是否与预测 horizon 匹配？
9. 移除动态尖峰清洗后，仅依赖已清洗数据是否安全？是否需要在输入验证阶段统一剔除一套异常键？
10. 树模型是否需要重新训练，还是仅需重新导出 test 预测？应通过哪些数值 hash/抽样对比确认？

## 10. 最终成功标准

只有满足以下条件，修复才算完成：

- LSTM-C、LSTM-D×3、GRU 新模型全部正常加载。
- Hybrid 路由只在新 val 结果上选择并冻结。
- test 预测文件全部符合新预测规范。
- 主表只使用 `actual_future_price`。
- 所有参与排名的模型共享同一 test 样本范围。
- 回测无未来信息反向修改历史曲线。
- 自动化测试全部通过。
- JSON 指标经独立脚本复算一致。
- 文档不再声称 Hybrid “全指标严格最优”，除非新 test 结果确实支持。
- `socProject/` 保持未修改，直到用户明确授权搬运与 push。
