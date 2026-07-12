# 评测策略

## 数据集与隔离

`tests/fixtures/relation_vault/` 与 `tests/eval/relation_gold.yaml` 组成冻结的 `relation_vault_v1`：10 篇文档、45 个无序候选 pair、3 个正向关系、3 个困难负样本。它覆盖显式正样本、隐式主题正样本、相近邻域、不同领域但词面接近的困难负样本。

推理读取 `NoteDocument[]` 与受控 prompt；Evaluator 读取 Gold。任何生产 inference 模块不得 import Gold loader、接受 Gold 参数或根据 Gold 选择输出。Perfect Mock 可读取 Gold，但 report 必须标为 oracle plumbing test。

## 指标

- Topic：TP/TN/FP/FN、accuracy、precision、recall、F1、false-positive/negative rate。
- Relation：pair precision、pair recall、pair F1；只在 linkage TP 上计算 direction accuracy 与 type accuracy。
- Evidence：source/target exact substring、归属正确、非空、未过期 hash。v1 不使用复杂语义证据评分。

## Mock 场景

| 场景 | 必须证明 | 不证明 |
|---|---|---|
| perfect | 端到端正常路径、预期满分、无 Bad Case | 真实模型智能 |
| noisy | FP、FN、方向错、类型错、证据错能被报告 | 真实模型稳定性 |
| malformed | schema 错误被局部记录，其他项继续 | 质量指标可与正常 run 比较 |

## 真实 baseline（WP-7）

前提：凭据只在环境变量；无明文配置；synthetic-only；请求/成本上限实际执行；缓存和重试语义已测试。固定 dataset version、prompt version、model、temperature、max output、concurrency、retry policy 和 config hash。至少重复运行 3 次，分别保存 manifest、prediction、metrics、bad cases、成本；不得把失败/0 prediction run 当 baseline。

## Bad Case 与数据治理

错误分类：topic FP/FN、relation FP/FN、direction error、type error、evidence error、schema error、provider error、Gold data error。修 Gold 只能产生新 dataset version、记录具体原因和审阅者；严禁为了提高模型得分改 v1 标注。每个 baseline 都绑定 dataset、prompt 和模型版本。

## relation_eval 复用策略

先复用其 scorer、validator、bad-case/report 的行为和现有测试意图；迁移到 `src/linkloom/evaluation` 时，保持 v1 结果可对比。修复硬编码路径、预算/cache 未执行与 malformed 计分语义后，才能把它标为正式 Evaluator。
