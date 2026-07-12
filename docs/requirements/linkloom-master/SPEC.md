# Linkloom MVP SPEC

## Problem

个人 Markdown 知识库会积累成“能存、难找、难连接、难行动”的材料。自动整理工具又常缺少原文证据、稳定评测与写入保护：用户无法判断 AI 的建议是否可信，更不能安全地让它改笔记。

## User Outcome

在 MVP 结束时，用户能在 synthetic vault 上：扫描文件、加载可校验的正文、得到主题与笔记关系建议、查看每条建议的原文证据/置信度/关系方向、查看指标和 Bad Case、人工批准或拒绝建议，并查看**不会执行**的 Change Plan。整个 MVP 不修改 Markdown。

## Product Principles

- **Local-first**：默认在本地处理，远程模型只在后续显式配置和批准后使用。
- **Read-only by default**：读取与写入能力物理分离。
- **Deterministic facts before inference**：路径、hash、已有结构先由确定性代码生成，再交给模型推理。
- **Evidence before recommendation**：每个模型建议都要引用两侧必要片段。
- **Evaluation before writeback**：先在冻结数据集评测；写回不是 MVP。
- **Human confirmation before mutation**：未来也只能按已预览计划逐项确认。
- **Explicit path boundaries**：只处理批准根目录及其相对路径。
- **Reproducible runs**：数据集、prompt、config、代码版本与运行结果可追溯。
- **No hidden autonomous loops**：没有后台静默整理或自我写回。

## MVP Scope

1. 冻结并复用 Scanner v1、relation vault v1、Gold Dataset v1。
2. 从 Index 安全加载 `NoteDocument[]`，检测内容变化。
3. 建立 Provider interface、perfect/noisy/malformed Mock 和正式 schema。
4. 生成 TopicPrediction 与 RelationPrediction；关系候选在 v1 为 10 篇文档的全量无序 pair。
5. 将推理与 Gold Dataset 严格隔离。
6. 复用/迁移 relation_eval 的 scorer、证据校验和 Bad Case 报告。
7. 在 Mock 闭环之后，运行一次预算受控的真实 Provider baseline。
8. 生成 Human Review Report 和只读 Change Plan。

## Non-Goals

Vault Profile、全文搜索/RAG 对话、GraphRAG、向量/图数据库、FastAPI、MCP、Web/Desktop UI、多 Agent、长期记忆、自动 tag/WikiLink 写回、Docker、云同步、用户系统、真实 Vault 读取/写入均不属于本 MVP。

## Definition Of Done

MVP 必须同时满足：

1. Scanner Index 能被正式 Loader 复用，且 hash/路径校验可证明源内容未被悄悄替换。
2. Mock perfect/noisy/malformed 三种场景都能产出预测、指标、Bad Case 和可读报告；malformed 不导致全局崩溃。
3. 推理代码没有读取 Gold Dataset 的依赖路径或参数。
4. 45 个候选 pair 的生成、关系方向、类型与证据有可执行验收。
5. 真实 Provider baseline（若 API 凭据与预算门禁已满足）记录数据集/prompt/config/模型/Token/成本；若未满足必须明确阻断。
6. Human Review Report 可让用户对每项建议批准/拒绝；Change Plan 只生成预览，不修改源文件。
7. 所有测试仅使用 synthetic assets；无真实 Vault、无自动 Git 提交、无源 Markdown 写入。

实施顺序、合同、评测和权限分别见 [implementation_plan.md](implementation_plan.md)、[data_contracts.md](data_contracts.md)、[evaluation_strategy.md](evaluation_strategy.md)、[safety_and_permissions.md](safety_and_permissions.md)。
