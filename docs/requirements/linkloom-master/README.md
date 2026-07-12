# Linkloom Master Requirements

## 定位与状态

**Linkloom 是一个本地优先、默认只读、证据可追溯且必须经人工确认才可修改 Markdown / Obsidian 知识库的个人知识助手。**

本目录是 MVP 的长期施工蓝图，不替代根目录的产品合同或用户路线图：

- 根目录 [SPEC.md](../../../SPEC.md) 与 [docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md) 仍是产品边界的权威来源；
- 本目录把已存在的 Scanner 与 relation_eval 实验资产，收束为可连续实施的工程合同；
- 本目录不是业务代码授权。每个 Milestone 仍须遵守 [AGENTS.md](../../../AGENTS.md) 的人工里程碑门禁。

当前已完成：Read-Only Vault Scanner v1（仅 synthetic sample vault，已具备 CLI 和 Scanner 单测）。

当前未完成主链路：Index 到正文的安全加载、可替换 Provider、主题/关系推理产品化、与 Gold 隔离的评测、人工审核报告、Change Plan。当前 MVP **不写回笔记**。

## 阅读顺序

1. [current_state.md](current_state.md)：先确认仓库事实、旧实验与风险。
2. [SPEC.md](SPEC.md)：再确认 MVP 目标、范围和非目标。
3. [architecture.md](architecture.md)：理解模块和依赖方向。
4. [data_contracts.md](data_contracts.md)：实现前锁定输入、输出与关系类型。
5. [safety_and_permissions.md](safety_and_permissions.md)：确认哪些读取/写入允许。
6. [evaluation_strategy.md](evaluation_strategy.md)：确认“模型变好”的证据如何产生。
7. [implementation_plan.md](implementation_plan.md)：按 Milestone 实施。
8. [milestone_acceptance.md](milestone_acceptance.md)：按矩阵验收。
9. [antigravity_handoff.md](antigravity_handoff.md)：交给实施 Worker 的直接说明。
10. [task.md](task.md)：唯一项目级进度账本。

## 当前实施起点

从 **WP-0：现有资产冻结与合同对齐** 开始。工作包（WP）是技术执行单元，不会重命名产品六阶段；WP-0 不实现新能力，只冻结 `Scanner v1`、`relation_vault_v1`、Gold Dataset 和当前测试基线。完成并获人类批准后，进入 **WP-1：Read-Only Note Loader**。

## 当前明确不做

- Vault Profile：仅 optional backlog；不阻塞主链路。
- 全文搜索/RAG 聊天、图数据库、GraphRAG、FastAPI、MCP、Web/Desktop UI、多 Agent、数据库、Docker、用户系统。
- 真实 Vault 访问或任何 Markdown 写回。
- 为了提高分数而修改 Gold 标签、关系库文本或另建第二套评测集。

## 文档状态约定

- **事实**：可由当前文件、输出或测试证据复查。
- **计划**：尚未实现，不能在汇报中当作已完成。
- **Optional**：不属于当前 MVP 顺序，除非人类另行批准。
