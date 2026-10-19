# 接线与 P4 后续契约建议（本轮未实现）

## 当前真实调用链

调用方提供授权的当前来源、完整有界 M1 inventory、typed endpoints、问题及预算
→ `SemanticProposer.propose(context)`
→ 来源/权限/预算 admission
→ `ProviderRequestAuthorization` 对精确请求授权
→ `ModelProviderAdapter.complete(ModelTurnRequest)`
→ `GeminiProviderAdapter` → 现有 `build_gemini_sdk_client` / SDK `models.generate_content`
→ 唯一强制 `propose_task_relations` 函数输出（不执行 ToolRuntime）
→ host 将 handles 绑定为 ObservationRef / ProjectRef / ChangeRef
→ MODEL_PROPOSED
→ `validate_proposals(result, current_context)` → EVIDENCE_VALID 或 EVIDENCE_INVALID。

证据验证后 semantic_status 仍为 NOT_EVALUATED，publishable 始终为 false。
独立语义评分/人工复核可以对指定样本作 SEMANTIC_ACCEPTED 判断；不赋予写入或发布权限。
源内容中即使出现指令，也只是待解释资料。函数未注册到任何产品工具目录。

`compare_proposed_revision` 仅在支持身份的模型提案与修订提案引用有效时进行条件性日期计算。
计算采用 M1 的日期精度/角色和可定位的日期边界，不使用旧关系判定或修订 replay。
结果不是实际延期，也不是当前业务计划选择器；缺年、冲突、未知或不兼容边界保持 null。

## 最小 Agent 接口

未来由一个显式调用方建立 `SemanticContext`，通过注入的 adapter 与 exact-request guard
调用 proposer。调用方负责确认 inventory_complete、truncated、授权版本集合和撤销集合；
这只是该次有界快照完整，绝不代表 B 发现了全部业务任务。B 候选可安排验证优先级，不能
裁掉同源竞争任务或将未召回任务默认为无关系。超预算应返回 BLOCKED 并由上层明确分批，
不得悄悄截断。分批后跨批证据/竞争端点的完备性仍须由调用方维护。

当前模块不负责权限发现，不读取凭据，不建数据库，也不负责自动 source refresh。
同一次外部请求期间会复查来源，但调用方在展示或后续接受前仍需提供最新快照再验证。
模型实际返回版本、请求/提示词/schema/响应 hash 和模型使用量随结果提供。
通用 ModelUsage 不单列 thinking tokens；Smoke 的 SDK 计量回执另外保留这些值。
usage 属于一次请求；同批多个 proposal 携带同一使用量，统计应按请求/回执去重，不逐提案相加。

生产接线至少应接入：有界授权快照构建器、既有预算 guard、语义提案调用、当前引用复查、
独立语义验收/人工审批策略、产品答案渲染。证据有效的提案可以展示为待核验解释，不能
伪装成当前已证实关系。Coordinator、ToolRuntime 和 UnifiedAsk 本轮均未改动。

## P4 为什么不能直接接受

现有 `TaskRelationStore.put_assessment` 仅接受 RelationAssessment，并保存旧证明 replay
recipe；`task_relation_query` 的每次查询重新执行旧 P1/P2/P3 判定。把模型提案强行包装成
旧记录会在查询时回到旧句式规则，或破坏 P4 对 ACTIVE 与当前证明的区分。

建议将来新增独立的 `semantic-relation-receipt/v2` 类型及查询分派，保留旧记录规则。
最小字段为：

- 原始 proposal ID / payload hash、typed endpoints、workspace 与有界上下文 hash。
- 原始引用及 Unicode 范围、来源版本/hash、M1 payload hash、授权和撤销依赖。
- 模型/实际版本、prompt/schema/validator 版本、执行回执及使用量。
- 独立语义验收回执：验收者/策略版本、提案 hash、结论、反证与缺证、验收时间及适用范围。
- 显式 promotion 授权事件：谁按何策略允许发布；不是 evidence-valid 或 model confidence。
- SAME_TASK、alias、正式审批等依赖回执 ID；任一依赖变化时重新验证或失效。

查询流程应先检查 append-only 事件链和 promotion/revoke，再复查当前授权来源、端点及依赖。
语义结论只针对其已验收的精确上下文；新来源/冲突、撤销、版本或策略不兼容即停止发布，
等待重新验收。不要在读路径隐式调用 LLM，也不要用旧 regex replay 验收新模型语义。
旧 receipt 路径继续原有 replay；新版本必须显式分派，未知版本拒绝。

SOURCE_CLAIM 的直接影响即使被语义验收，也仍无正式决策权威。需要正式审批的产品行为
另查既有 durable receipt 与替代链，不能从叙述文本或模型字段推导批准。
本轮只提供此设计建议，没有实现 schema 迁移、promotion、P4 写入或正式数据库访问。
