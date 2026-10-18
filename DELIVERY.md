# LinkLoom M2-P2 交付

本轮实现 IN_PROJECT、PROJECT_ALIAS、RELATED_TO、AFFECTED_BY 的固定候选、证据约束判定。完整实现说明、文件职责、D03/D06/D07/D08 真实 JSON、审批隔离证据、失败记录与复现命令见 [M2-P2 DELIVERY](docs/requirements/task-relations-p2/DELIVERY.md)。

本地 Dev 20/20 个原子问题符合独立 Gold；四类关系正例全部通过，无错误项目绑定、无据影响确认或 SOURCE_CLAIM 越权。P2＋P1 聚焦检查 108 passed；相关 M1/P1/Decision Memory 回归 188 passed（有交集，不相加）。逐题指标见 [metrics.json](docs/requirements/task-relations-p2/evidence/metrics.json)。

从干净 HEAD 加明确交付文件构建的隔离副本，合并去重检查 **241 passed**，Dev 导出再次 20/20。独立审阅发现的三项问题已修复并复核，无剩余 Required。

尚未实现计划修订、截止日期比较、自动发现、关系持久化或 Agent/UI 接线；原五行案例 B 的项目、身份和具体变更影响仍为 UNKNOWN。真实 Provider 调用与保留集执行均为 0。下一阶段需先实现 SAME_TASK 前提之上的明确计划修订证明及日期可比性校验。

固定交付分支：`codex/linkloom-m2-progress`。本轮包含可复现所需的本地 M1/P1 前置源码、测试和交付文档，其他既有工作区修改未纳入提交。
