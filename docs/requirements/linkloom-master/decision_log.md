# 决策日志

| Date | Decision | Context / Alternatives | Consequences | Status |
|---|---|---|---|---|
| 2026-07-12 | local-first | 私人笔记隐私高；云优先会扩大泄露面 | 远程 Provider 只能后置、受控 | Accepted |
| 2026-07-12 | 默认只读 | 整理建议不等于用户意图 | 读取与写入模块分离 | Accepted |
| 2026-07-12 | Scanner 与 Agent 分离 | 文件事实需要稳定、可测试 | Scanner 无模型依赖，冻结 v1 | Accepted |
| 2026-07-12 | inference 与 evaluator 分离 | Gold 泄露会制造虚假高分 | 生产 inference 禁止读 Gold | Accepted |
| 2026-07-12 | 不先做 Vault Profile | 它只解释统计，不能打通关系/评测主链 | Optional backlog，不阻塞 Loader | Accepted for MVP |
| 2026-07-12 | 不先做 RAG 搜索 | 当前 MVP 要先证明关系推理可信 | 检索后置，不引入 vector DB | Accepted for MVP |
| 2026-07-12 | v1 全量无序 pair | 10 docs 仅 45 pairs，最简单可审计 | 大 vault 前再测 candidate pruning | Accepted for MVP |
| 2026-07-12 | 先 Mock 再真实模型 | 先验证合同和 scorer，避免把模型失败混入工程 bug | perfect 仅验证管线 | Accepted |
| 2026-07-12 | MVP 不写回 | 写回需要 diff/hash/backup/rollback | ChangePlan 只生成不执行 | Accepted |
| 2026-07-12 | 数据集冻结版本化 | 为模型改标签会破坏比较 | 改动只能新版本并记录原因 | Accepted |
