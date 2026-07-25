# 双轨收尾实施计划

1. 用测试固定库存聚合、双轨 API 和线上评估产物契约。
2. 新增库存预测聚合服务，接入 `/api/inventory/value_history`。
3. 将组合诊断迁移到权威 `predict_for_skin` 输出。
4. 扩展 Hybrid V2 评估：R2、逐决策预测 CSV、线上比较 JSON、独立回测。
5. 扩展 Model Lab comparison/backtest API，同时保留旧字段。
6. 前端增加历史/线上分段控制，库存图展示 7 天与 30 天两条预测线。
7. 更新 i18n、OpenAPI 和静态契约测试。
8. 重新生成线上产物，运行完整 pytest、JS 语法、YAML、接口烟测和 diff 检查。

