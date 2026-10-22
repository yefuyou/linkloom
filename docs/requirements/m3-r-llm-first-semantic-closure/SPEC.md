# M3-R：LLM-first 语义证明架构收口与真实产品闭环

实施边界由用户本轮完整 M3-R 指令授权；验收与未完成项见 DELIVERY.md。

## Problem

M3-Q 的真实 Product LIVE 已能运行 F/G/Runtime，但 r8 的关系计划带有精确来源句柄时，`SemanticAnalysis` 被筛选条件阻断，正常产品没有真实语义提案。旧 P1/P2/P3 的有限业务句式仍控制可信关系路径，导致模型已经理解的自然语言修订无法形成可审查产品结论。

## Goals

- 让正常 Product 关系调查在有效端点、授权来源和预算存在时实际调用已有 `SemanticAnalysis`/`SemanticProposer`。
- 允许带精确来源绑定的关系步骤进入非权威语义候选层。
- 保留 Workspace、来源版本、EvidenceSpan、方向、冲突、撤权、预算和 Runtime 工具授权校验。
- 在新语义路径中移除旧有限业务语法作为必要门槛；不删除历史/P4读取兼容能力。
- UI 明确区分模型提案、证据已验证、语义复核、正式业务关系。
- 不自动写入 P4，不自动计算未经确认的正式延期，不修改历史 M2 0/4。

## Non-Goals

- 不新增 Planner、Runtime、关系存储或第二套语义服务。
- 不创建大型人工审批系统；若现有授权合同不足，停在可复核候选层。
- 不用 H01–H04 关键词、中文正则或固定样本补丁。
- 不强制 Product 使用 medium；保留已验证 low 路径。
- 不修改 master、PR #6、历史 Gold/Seal/Frozen 或真实 vault。

## User Workflow

Workspace → Sources → Ask/Tasks → 单次外部授权 → F → G → Runtime → `analyze_task_semantics` → Evidence Validation → Reviewer → Answer-first Product 结果 → 支持/反对证据 → 原文 → 刷新/冷读。

## Contracts

- `InvestigationStep.to_semantic_query()` 继续保护 P4/时间/来源范围的 host-bound 执行合同。
- 新增或复用一个仅供 SemanticAnalysis 的 proposal query 投影，允许来源句柄作为授权上下文，不改变 P4 查询限制。
- SemanticProposer 输出保持 untrusted；Evidence Validation 只验证引用和绑定；Semantic Review 只形成可审查候选；P4 仍需要正式授权/证明。
- UI 以现有 TaskResult/Reviewer 结构展示候选、证据、冲突、缺口和下一步。

## Acceptance Criteria

1. r8 类计划的带来源 relation steps 能注入 SemanticAnalysis，并注册 `analyze_task_semantics`。
2. 正常 Product LIVE 至少一次真实调用该工具，并保存 Provider、Tool Ledger、提案、引用、Reviewer 和 UI 证据。
3. SUPPORTED/REFUTED/CONFLICT/UNKNOWN 仍保持区分；无 P4 权威时不写关系、不发布正式延期。
4. 新自然语言案例不依赖固定中文语义正则；同负责人不同任务、否定、冲突、普通日期和撤权均有可解释结果。
5. 来源/Workspace/版本变化、Reviewer 拒绝、UNCERTAIN 和冷读安全合同保持通过。
6. M2 H01–H04 原始 0/4、Gold、Seal、Frozen 不变。
7. 受影响测试、真实 Chromium HTTP E2E、必要独立复审通过，P1/P2=0。

## Evidence

- r8 raw preparation/runtime/provider receipts are the baseline audit.
- New LIVE receipts must include semantic tool call and provider request metadata.
- New browser/HTTP evidence must demonstrate refresh and cold read without extra model calls.

## Implementation Plan

See `implementation_plan.md`; executable work items are in `task.md`.

## Learning Reflection

The baseline audit showed the missing connection is a source-scoped semantic projection: G already emits relation endpoints and exact source spans, but the old host-bound query projection rejects source-scoped relations before SemanticAnalysis can be created. The next slice must preserve that rejection for P4 while adding a separate proposal-only projection.
