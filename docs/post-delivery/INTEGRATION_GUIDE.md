# 项目结束后的接入指南

## 不要直接覆盖

维护包中的脚本是在 `SkinVest_project/notebooks/` 布局下运行的。团队仓库使用 `socProject/ml/` 布局，直接复制会导致部分数据和输出路径错误。

新模型与当前后端也存在接口差异，因此必须在独立分支中接入。

## 推荐接入顺序

### 1. 建立独立维护分支

以项目最终提交版本为基准创建分支，例如：

```text
post-delivery/forecast-contract-v4
```

不要直接在 `main` 上试验。

### 2. 迁移预测规范和特征工程

优先迁移：

- `forecast_contract.py`
- `feature_engineering.py`
- `tree_features.py`
- 对应测试

将路径统一改为：

```text
socProject/ml/data
socProject/ml/models
socProject/ml/preds
socProject/ml/outputs
```

### 3. 迁移训练和预测脚本

深度模型脚本与树模型脚本需要保留仓库已有的：

- ARIMA 导出
- 增量训练入口
- scheduler 参数
- 后端需要的兼容预测文件

不要直接用维护包的树模型脚本覆盖仓库旧脚本，因为维护包版本不包含 ARIMA。

### 4. 修改后端实时推理

必须同步处理：

1. LSTM 窗口改为包含决策观测。
2. Hybrid 从 `lstm_hybrid_route.json` 读取路由。
3. LSTM-C 未知物品使用 `__UNK__`。
4. LSTM-D 未知物品按当前价格和训练边界分组。
5. 树模型优先读取 `pred_*_test.csv`。
6. 模型比较 JSON 同时兼容新旧结构。
7. 回测 JSON 转换为前端需要的 `{dates, series}`。
8. scheduler 不得继续向不支持的脚本传 `--mode incremental`。

### 5. 重新生成输出

顺序：

```text
训练或加载模型
→ 导出 val C/D
→ 选择 Hybrid 路由
→ 导出 val/test 全模型预测
→ 生成六模型比较
→ 生成双手续费回测
→ 转换后端/前端兼容输出
```

### 6. 联调检查

至少验证：

- 后端健康检查
- 已知物品 Hybrid 预测
- 未知物品预测
- `/api/predict`
- `/api/models/comparison`
- `/api/models/backtest`
- 库存诊断
- 前端模型实验室
- 定时任务

## 采用标准

只有在以下条件同时满足时才考虑合入：

- 全部单元测试通过。
- 后端实时预测与离线预测窗口一致。
- 前端不因 JSON 结构变化而报错。
- ARIMA 和增量更新能力没有丢失。
- 新旧结果差异已在文档中解释。

