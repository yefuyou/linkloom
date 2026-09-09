# Linkloom Master Requirements

## 定位与状态

根目录 [SPEC.md](../../../SPEC.md) 与
[docs/PRODUCT_ROADMAP.md](../../PRODUCT_ROADMAP.md) 仍是产品边界和六个用户
Milestone 的唯一权威来源。

本目录现在包含两类规划记录：

- [MASTER_SPEC_V2.md](MASTER_SPEC_V2.md) 是面向求职完成态的
  **ACCEPTED AND FROZEN — Planning Baseline V2**：独立 Reviewer 结论为
  `PASS_WITH_FINDINGS` 且无 blocker；clarification 已写入，没有改变七个
  Gate、M0-M5 顺序、产品方向或 canonical 六阶段产品路线图，也没有让任何
  Gate 新增为 `COMPLETE`；
- [SPEC.md](SPEC.md) 及其配套文件是 2026-07 relation-evaluation MVP 的
  历史实施脊柱，继续提供资产和评测证据，但不再代表当前唯一下一任务；
- M0-M5 只是 V2 的执行顺序，不替代产品路线图；
- 本目录不是业务代码授权。任何 Child SPEC 仍须遵守
  [AGENTS.md](../../../AGENTS.md) 的人工门禁和独立 Reviewer 要求。

当前可确认的基础包括 synthetic Scanner、持久化 FakeModel Loop、离线
Gemini provider boundary 和 Memory/Writeback 基础。当前 trajectory
evaluator 的 `41 / 23 executable / 18 NOT_IMPLEMENTED` 仅是未提交、实验性
worktree evidence，不是已接受 Gate F 能力。上述基础尚未形成真实 Provider
驱动、模型自主检索、Context Assembly、业务 Vertical Slice、业务 Outcome
Eval 和量化 Reliability 的完整闭环。

截至 2026-08-30，七个 V2 Gate 均未达到 `COMPLETE`。M0.1 已经由独立
Reviewer 以 `PASS_WITH_FINDINGS`（无 blocker）复核，并由人类正式接受；
这只关闭 M0.1 integration repair，不关闭 Gate A 或 Gate B。Portfolio Freeze 是
七个 Gate 全部通过后的独立最终证据步骤，不是第八个 Gate。M1-M4 渐进组装
同一条 Team Decision & Action vertical slice，并在 M5 汇总、演示和冻结。
当前 frozen 状态只冻结规划合同，不代表未来 M5 Portfolio Freeze 已完成。
真实 Vault 写入仍被禁止。

## 阅读顺序

1. [根 SPEC](../../../SPEC.md) 与
   [产品路线图](../../PRODUCT_ROADMAP.md)：确认 canonical 产品边界。
2. [MASTER_SPEC_V2.md](MASTER_SPEC_V2.md)：确认当前求职完成态定位、七个
   Gate、M0-M5 执行顺序和 Stop Rules。
3. [task.md](task.md)：查看当前 Planner/Reviewer 门禁和唯一获授权下一任务。
4. [M0.2 SPEC](../m0_2_durable_real_provider_loop/SPEC.md)、
   [implementation plan](../m0_2_durable_real_provider_loop/implementation_plan.md)
   与 [task record](../m0_2_durable_real_provider_loop/task.md)：审阅当前
   Planner 输出；这些文件尚未授权 Worker。
5. [current_state.md](current_state.md) 与 [SPEC.md](SPEC.md)：读取 2026-07
   历史基线，不把其中的旧“下一任务”当作当前授权。
6. [architecture.md](architecture.md)、[data_contracts.md](data_contracts.md)、
   [safety_and_permissions.md](safety_and_permissions.md)、
   [evaluation_strategy.md](evaluation_strategy.md)：按 Child SPEC 需要复用
   历史合同。

## 当前实施起点

Master SPEC 的独立 acceptance review 和 M0.1 的独立实现复核均已完成。
**M0.2 Durable Real-Provider Loop Completion** 的 Child SPEC、implementation
plan 和 task record 已完成 Planner draft。当前唯一下一门禁是人类审阅这三份
文档并明确批准或调整；在此之前 Worker 不能修改 production 或 tests。

## 当前明确不做

- 不继续 M0.1 代码，不在 M0.2 SPEC 获批前开始 M0.2 Worker，也不跳到
  M0.3-M5。
- 不扩展 Agent roles、multi-agent framework、MCP、SFT/DPO、复杂 Memory、
  大型 vector database、Kubernetes 或 UI。
- 不访问或修改真实 Vault。
- 不为了提高分数修改 Gold、fixture 或隐藏 `NOT_IMPLEMENTED` 能力。
- 不把未提交或未独立验收的代码写成已完成能力。

## 文档状态约定

- **事实**：可由当前文件、输出或测试证据复查。
- **计划**：尚未实现，不能在汇报中当作已完成。
- **Accepted/Frozen Planning Baseline**：独立 Reviewer 已接受规划边界；不
  等于任何 Gate、Child SPEC 或实现已经完成，也不自动授权 Worker。
- **Optional**：不属于当前 MVP 顺序，除非人类另行批准。
