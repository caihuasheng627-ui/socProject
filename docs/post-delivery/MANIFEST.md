# 维护包文件说明

## 包含内容

### 源码

- 统一预测规范和特征工程
- LSTM-C、LSTM-D、GRU 训练脚本
- 深度模型和树模型规范预测导出
- Hybrid 路由选择
- 六模型比较
- 公平回测
- held-out SHAP 防护
- 两个特征工程兼容入口

### 测试

- 预测目标与目标日期
- 60 步窗口边界
- split 防泄漏
- 未知物品处理
- 预测 CSV 规范
- 模型比较公平样本
- 回测 cap 与手续费
- 特征入口一致性
- SHAP held-out 防护
- 树模型拟合范围、输出和模型包

### 模型和结果

- LSTM-C、LSTM-D×3、GRU
- RF、LightGBM、XGBoost
- Hybrid 路由及 C/D 分组结果
- 14 个规范 val/test CSV
- val/test 模型比较
- 零手续费和 2.5% 手续费回测

## 未包含内容

- `socProject/` 中的任何文件
- 原始 train/val/test 数据集
- 后端、前端和数据库文件
- Git 历史或 `.git/`
- API Key、`.env` 或其他密钥
- Python 缓存、pytest 缓存
- 训练日志
- 临时文件
- 旧格式预测 CSV
- 尚未按新模型重新生成的 SHAP 图片

## 数据依赖

将来运行时需要从合法的最终项目备份中提供：

```text
train.csv
val.csv
test.csv
buff_val.csv（仅在需要 BUFF 验证时）
```

数据必须保持原始列结构。不要把数据集直接放入本维护包的 Git 仓库。
