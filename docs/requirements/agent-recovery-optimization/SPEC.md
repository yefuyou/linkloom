# Agent Recovery, Evidence Coverage & Safe Performance Optimization

用户在 2026-10-15 的完整任务文本批准本专项连续研发、受预算限制的真实模型实验、独立复审、提交与推送。基线 `49736a7098b23c8b9f021f98f1fe6d3e7c70e755`；独立分支 `codex/linkloom-agent-recovery-optimization`。本 SPEC 记录该既定边界，不增加审批阶段。

## 行为与架构

1. P0：已有合法 Verified Read 但下游缺失时，在预算耗尽前由模型提交原 Typed Final 的独立有据 rationale，主答案为空、未知目标明确；Validator、关系计划和 Reviewer 继续校验。可修正输入错误最多一次；身份、权限、版本、Provider 未知仍停止。预算耗尽不转成功。
2. P1：复现历史 O 的具体遗漏层；只在既有 Retriever/Passage 链路做有界改善，候选仍经原 Search、Verified Read、Grounding，无整文件信任捷径。
3. P2：P0/P1 验证后，测量 10/100/500 来源各三次；如重复读取可在局部安全消除则实现，否则交付瓶颈与最小后续设计。不得以 mtime 缓存代替内容、版本与 Workspace 校验。

复用 ModelLoop、RecoveryPolicy、Progress、TaskPlan、ToolRuntime、Reviewer、Provider Ledger、UI Projection。状态由原 Runtime/Source Repository 持有；无新运行时、证据库、索引引擎、依赖或缓存服务。最多三个数据支持的生产优化点。优先级 P0 → P1 → P2。

## 验收

- 暴露失败可复现；新合成输入与 Gold 在修复前冻结，模型不可接触 Gold；作者见过 Gold 的限制明示。旧首次 12/16 及历史资料不修改。
- 完整/部分/冲突/无证据、非法参数、权限、版本、预算、已知 Provider 失败与 Provider 未知分别报告。
- Partial 经过现有 Typed Final、验证及 Reviewer；完整事实不被未知下游抹去；拒绝未核验事实和父关系外的实体。
- 同文件后续 passage 仍具有全套来源身份并经过 Verified Read；记录候选、引用、上下文和工具代价。
- 安全回归覆盖用户列举的 15 个边界；浏览器合成 Workspace 验证完整、部分、冲突、失败、未知、来源更新及竞态行为。
- 真实调用最多 120 次、$8、首请求后 3 小时、并发 1、SDK 自动重试 0；每次请求保守预留。canary 最多 20 次。复用旧 Ledger；UNKNOWN 停当前题且不重跑，累计两次严格 outcome uncertain 停整个批次。缺 Usage 不等于严格 UNKNOWN，但费用不能当零。
- 真实前后比较缺失、同文件、两跳、三跳、冲突，逐 trial 报告，无可比数据标 N/A；不无限重试到成功。
- 最终独立审查、修复阻断项；只提交脱敏指标/合成数据/源码。原始请求响应留 ignored work。提交、推送本分支并回读远端；不 PR、merge、Release。

## 内部执行顺序与检查

环境/历史证据 → focused 失败测试 → 冻结新集 → P0/P1 小增量 → 安全与既有回归 → offline SDK → 串行 live canary/前后对照/新集 → P2 三次规模前后与失效检查 → 浏览器 → 独立复审 → Git 交付。

测试命令显式设置 `PYTHONPATH=src;.`、`LINKLOOM_RETRIEVAL_MODE=bm25`。产物放 `work/agent-recovery/`；不访问真实 Vault，不读出凭据，不删除用户材料。公开报告按 A–I，历史首次、新集、FakeModel、真实模型分开。

## 初始判断

最简单的替代方案是保持安全失败、仅报告缺陷；本轮授权要求改善用户结果，因此优先研究已有恢复/Final 契约的最小修复。主要风险是把安全错误误当可恢复、把预算失败误当部分成功、把同文件路径误当证据身份、以及为性能跳过 Freshness。上述边界均须用行为测试验证。
