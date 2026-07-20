# CSVest 交付后优化与维护包

> 状态：仅供项目结束后的研究、优化和维护  
> 创建日期：2026-07-20  
> 基准仓库：`socProject/main`，提交 `ef6a0c1`  
> 重要约束：本包不属于当前课程交付，不得直接复制到 `socProject/`，不得提交或 push

## 这个包是什么

本包保存了一次完整的 ML 预测规范审查和修复实验。当前课程项目继续使用 Git 仓库中的原始模型、接口和结果；这里的代码与产物只作为项目结束后的维护方向。

本包解决的主要问题包括：

- LSTM 输入窗口是否包含决策观测。
- 统一预测同一物品后续第 7 个有效日频观测。
- 使用真正的未来价格计算回归指标。
- val 用于选择，test 用于最终评价。
- 六个主模型使用完全相同的 test 样本。
- 删除回测中事后修改历史收益曲线的 cap。
- 保存树模型所需的类别编码器和特征顺序。
- 增加预测格式、冷启动、比较和回测测试。

## 维护包预测规范

```text
决策观测：t
输入窗口：t-59 ... t，共 60 个观测
预测目标：同一物品后续第 7 个有效日频观测
```

规范 CSV：

```text
split,date,target_date,market_hash_name,current_price,
actual_future_price,predicted_price,horizon_steps
```

## 最终实验结果

六模型使用相同的 35,229 条、154 件 test 样本：

| 模型 | RMSE | MAE | MAPE | R² |
|---|---:|---:|---:|---:|
| LSTM-C | 44.3745 | 6.2668 | 10.95% | 0.9374 |
| LSTM-D | 52.0901 | 7.0234 | 7.72% | 0.9137 |
| Hybrid | 52.0900 | 7.0204 | 10.69% | 0.9137 |
| RF | 52.8212 | 7.2600 | 6.26% | 0.9113 |
| LightGBM | 58.3566 | 8.4611 | 6.34% | 0.8917 |
| XGBoost | 61.7568 | 9.1675 | 7.93% | 0.8787 |

结论：LSTM-C 的 RMSE、MAE、R²最好，RF 的 MAPE 最低。Hybrid 的验证集路由为 `low→C、mid/high→D`，但它没有在 test 上全面超过 LSTM-C。

## 验证状态

- 自动测试：`22 passed`
- Python 语法检查：23 个文件通过
- 模型加载：5 个 Keras、3 个树模型通过
- 规范预测：14 个 val/test CSV 通过
- 六模型比较：`status=complete`
- Git 仓库：未修改、未提交、未 push

## 目录

```text
source/                 修复后的 Python 源码
tests/                  自动化测试
docs/                   设计、计划和交接材料
artifacts/models/       重训模型及路由元数据
artifacts/preds/        规范 val/test 预测
artifacts/evaluation/   模型比较与回测结果
INTEGRATION_GUIDE.md    将来重新启用时的对接步骤
MANIFEST.md             文件范围与排除项
SHA256SUMS.txt          完整性校验值
```

## 使用原则

1. 当前课程交付继续使用 `socProject/` 原始版本。
2. 不要把本包直接解压到 Git 仓库。
3. 项目结束后如需采用，先新建独立分支。
4. 先修改路径和后端兼容，再复制模型和输出。
5. 完成端到端测试后才能考虑合入。

