# 停止与恢复审计

历史证据：上一轮 `LIVE-MISSING` 实际 6 个模型回合，非法 alternate 被拒绝后模型仍收到 observation，并在第 6 回合提交 null Final；但 rationale 为空，且结果层保留拒绝错误而不运行 Reviewer。不是 ToolRuntime 失守，也不是从未向模型返回错误。

| 问题 | 当前源码契约与本轮处理 |
| --- | --- |
| 何时允许 Final | 模型在剩余回合内主动提交；ModelLoop 持久化终态后仍由 RetrievalAgent 校验 Typed Final、计划、claim 和 Reviewer。循环终结本身不等于产品成功。 |
| Partial 表达 | `team-decision-result/v1`：decision.value=null、status=insufficient_evidence；每个已核验事实独立 rationale+evidence_refs；uncertainty.statement=null、unknown_fields 为准确子目标 ID。 |
| Validator 接受条件 | 所有声明有真实 read 支持、匹配请求的有向关系；可达缺失子目标已搜索，没有剩余计划 alternate；不支持主答案，不能偷换负责人为领导。 |
| Reviewer 拒绝条件 | 引用未观察/未核验、版本或 quote 不符、超出关系计划、未尝试可达子目标、unsupported claim 或未报告真实冲突。没有从 Ledger 字符串拼 Final 的旁路。 |
| 工具拒绝如何返回 | 原 durable observation / ModelToolResult；保留错误类别与路径，现有 recovery_guidance 约束下一回合，无新增通道。 |
| 哪些可修正 | 一次计划查询错误，可改合法输入，或在充分搜索后主动提交仍须严格验证的 null Final。第二次连续无效输入停止。未知 Evidence ID 仍遵循旧“一次拒绝后必须成功 read”的独立规则。 |
| 哪些必须停止 | 身份字段缺失/不匹配、权限、来源版本变更、未确认 checkpoint、Provider unknown；不得据此恢复成可信 Partial。 |
| Budget vs missing | 前者是执行资源耗尽，没有可核验 Final；后者是已尝试合法调查后业务事实不足，模型可在预算内主动提交 Partial。修复残留工具错误掩盖 max_steps 终态，以及 UI 缺失 budget 分类。 |
| TaskPlan 能否判断下一跳 | 能从 verified parent、searched slots、missing relation、pending alternate 判断当前合法搜索是否用尽。没有唯一可信父实体的子目标保持 pending，不能臆造 entity。 |
| 冲突如何停止 | 使用现有 verified conflict 判定与双方来源，禁止选任一争议实体继续下游。Partial 的独立事实与争议事实分开。 |

Progress 现在使用受信任 Product Workspace（与 source root fingerprint 分开），只有可达缺失子目标已搜索、所有候选已有 Verified Read、无计划 alternate/未处理冲突时给出 FINAL_PARTIAL 指引并收起工具。没有增加默认 12 回合。

P0 的恢复只允许单次 `task_plan_semantic_mismatch` 的 query_variant/subgoal_id 错误被后续 null Final 放弃；不能放弃 source identity 错误，也不能借此交付完整主答案。后续完整验证链保持。

## 网络观测口径

原 Ledger 维持谨慎的 `UNCERTAIN` 预留与停止行为。本轮同时保留原 SDK 类型：确定的连接建立/DNS/proxy-connect 失败记 known transport failure；ReadTimeout、RemoteProtocol、无法确认结果的异常记严格 provider_outcome_uncertain。缺 Usage 只影响费用预留，不能单独定义严格网络未知。任何 Ledger UNCERTAIN 的案例均不再执行；缺少 outcome 的未闭合/未诊断记录 fail closed。两次严格 unknown 停整个批次，无 SDK 自动重试。

成本 admission 使用已确认 Usage 的估算费用 + 所有未知请求的完整最坏预留 + 下一请求最坏预留，必须不超过 $8；同时满足最多 120 次、首请求起 3 小时、OS lease 并发 1。不是要求消耗所有额度。历史 live CLI 在本专项分支禁用，避免另开预算。
