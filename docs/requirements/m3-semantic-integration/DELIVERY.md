# M3-D 交付：语义提案接入现有 Runtime

本轮完成授权合成快照上的离线集成，未执行新的真实 Provider 请求。
外层工具选择使用 FakeModel，内层分别使用历史输出 REPLAY 和明确标注的 FAKE 对照。
这证明实际 Runtime 接线、证据检查与计数恢复，不能宣称真实模型自主规划或生产业务成绩。
所有模型提案始终 `SEMANTIC_NOT_EVALUATED`、`publishable=false`；没有 P4 Promotion。

## 集成历史与范围

- 公共祖先：`26392432dcef70692461e6f2f50bc1bd5528f69a`，已用 merge-base 核对。
- A：`bd5de920e4bcf1a76c12253c7001910552baf287`。
- B：`8453126d6c4eef386727e193f39df461e0816b2a`。
- 新 Worktree：`work/linkloom-m3-semantic-integration`，分支 `codex/linkloom-m3-semantic-integration`。
- 在固定起点正常快进 B，再正常合并 A，集成提交 `d1679ed4a8b229801fd666aeb112a23d9349e09e`。
  A/B 修改文件不重叠，没有冲突；没有重置、覆盖、改写它们的分支或工作目录。

调用接口差异先记录于 [SPEC.md](SPEC.md)：B 的 inspect 是截断显示投影，A 需要原始完整授权快照；
A 的 `propose_task_relations` 是 Provider 输出函数，不能注册为 Runtime 工具。
兼容修改只扩展 B 的 opt-in 工具、Final 回执与 Reviewer，以及 task_relations 的配置步数。
没有改动 A 的语义模块、并行 C 的 ContextAssembler/Context Engineering、旧 Gold、评分器或冻结指标。

## 新增能力、参数和权限

`analyze_task_semantics` 是现有 ToolRuntime 的可选工具。仅 Host 配置 `SemanticAnalysis` 时注册，
且仅 task_relations 工作流可见；旧 ask/connect/team_decision 不获得该能力。

| 参数 | 约束 |
| --- | --- |
| workspace_id | 必填，只能是 Host 当前授权 Workspace 的 enum 值 |
| investigation_id | 必填，只能是 Host 预先配置的调查 ID |
| 其他字段 | additionalProperties=false，拒绝路径、API Key、模型或任意来源输入 |

继续沿用 Schema 校验、工具白名单、调用预算、Pending/Terminal Tool Ledger、Checkpoint、Trace、
ModelArtifactStore、外层 Provider 错误与步数/Token 预算。新增 CallBoundExecutor 只在这些检查之后
取得真实 ToolCall 身份，模型不能伪造 run_id 或绕开工具读取 SQLite/文件。
网络、Vault 写入、Gold 权限仍为 deny；本轮内层策略只接受 FAKE/REPLAY，拒绝 LIVE。

Host 注入 `SemanticSourceSnapshot` 和独立 `SemanticCallPolicy`，不从 inspect 显示结果反向组装证据。
快照包括原文 RawArtifact、版本授权/预期清单、全部竞争 M1 观察及 payload、ObservationRef、
Typed ProjectRef/ChangeRef、待判断问题、来源 handle、撤销集合、完整性/截断状态及预算。
构建器重新解析原文生成精确 EvidenceSpan，复用 A 的 context_errors 校验来源当前性、端点和竞争观察。
缺来源、缺竞争观察、授权不匹配、原文解析 PARTIAL/FAILED、超预算或明确截断都 BLOCKED；不会把截断清单标为完整。
失败 manifest 也保留原始数量、预算与清单指纹，显示列表超限明确标注截断。
完整性仅指 Host 声明的有界授权调查范围，不能证明外部来源或所有业务任务均已发现。
M2 候选只能辅助调查，没有召回某候选不产生负面关系结论。

## 实际调用链

```text
RunRequest(workflow=task_relations, question)
  -> RuntimeEngine.start_multi_agent / resume_multi_agent
  -> RuntimeAgentAdapter：绑定当前 run 的任务工具与语义调查
  -> Coordinator -> RetrievalAgent
  -> SingleAgentModelLoop（外层 FAKE 模型，独立模型执行记录）
     -> ToolRuntime.inspect_task_context -> M1 + M2 discover_candidates
     -> 模型读取真实回执
     -> ToolRuntime.query_task_relations -> P4 RelationQuery，只读
     -> 模型读取真实回执
     -> ToolRuntime.analyze_task_semantics
        -> Host 原文快照构建器 -> A context_errors
        -> 独立请求/Token/成本准入 -> 耐久精确请求预留
        -> A SemanticProposer -> propose_task_relations 输出函数
        -> REPLAY/FAKE adapter -> 原始响应耐久化
        -> A validate_proposals + 声明来源覆盖检查
        -> 条件性 compare_proposed_revision -> ToolResult
     -> 模型读取 ToolResult -> Final 选择实际三项回执 ID
  -> Runtime 投影实际可见回执
  -> Reviewer 重读原文、P4 生命周期、调用身份、请求/响应工件与 EvidenceSpan
  -> 结构化 task-relations-result/v1
```

Final 不接受模型自报关系。inspect/query 回执必须确实在选择语义工具时可见，语义回执必须在 Final 时可见；
这兼容既有模型历史字节预算裁剪，同时保留持久化请求的 hash 校验。执行顺序必须 inspect < query < semantic。
Reviewer 将所有回执 ID、参数、顺序、状态和值与当前 run/task 的耐久 Ledger 对账，并核对完整固定结果投影。
无工件的阻断诊断也须保持完整固定投影，不允许改成已接受/可发布。
Reviewer 不再次调用模型；来源、撤销状态或工件变化即拒绝旧结果。
Reviewer 的 evidence_sufficient 表示回执投影/来源复查通过，不表示业务语义已接受。

## 证据与发布边界

输出分别保留 MODEL_PROPOSED、A 原文验证报告、EVIDENCE_VALID/INVALID、
SEMANTIC_NOT_EVALUATED、来源当前性/撤销状态、coverage 和 publishable。
coverage 按请求前 Host 声明的来源版本集合检查，不读取 Gold；来源内引用仍须通过 A 精确原文验证。
覆盖完整也不等于独立语义验收。引用有效且覆盖完整的提案可作为待核验模型分析展示；
缺引用的提案保留缺失清单并阻断。P4 当前已证实结果保留在独立 relation_query 字段。

| 实际执行案例 | 结果 |
| --- | --- |
| 历史 revision REPLAY | 两项提案原文有效/覆盖完整，条件性日期 +4，仍未接受/不可发布 |
| 历史 causality REPLAY | SUPPORTED / REFUTED / UNKNOWN；覆盖依次完整/不足/不足，调查 BLOCKED |
| 历史 conflict REPLAY | 正反证保留，修订日期比较 BLOCKED，delta=null |
| 新 Dev 变体 FAKE 完整对照 | 三种影响状态均引用独立变更、工作记录与调查备忘，仍 NOT_EVALUATED |
| 新 Dev 变体 FAKE 漏反证来源 | REFUTED 缺独立变更来源，EVIDENCE_INVALID / BLOCKED |
| 新 Dev 变体 FAKE 漏调查来源 | UNKNOWN 缺调查备忘，EVIDENCE_INVALID / BLOCKED |

A 的原始三次输出与指标均未改动。REPLAY 校验已提交文件的原始 SHA（仅兼容 Git LF/CRLF 转换）、
冻结清单、精确请求、Schema 与来源快照；原 response SHA 仅作为导入元数据。
A 没有保存完整原 SDK wire，因此不声称独立恢复/验证原始 SDK 响应。本轮重建的 REPLAY 响应有自己的 SHA。
source_commit 为冻结 provenance/core_commit `04a446d10494a2f6d61ea1f0a745a3ce9f1e8d0c`，
与 freeze.git_base 对账；导入 A 分支提交另列 imported_from_commit。
新变体仅添加公开原文输入；FAKE 引用是接线对照，不能说新真实模型已经修复了引用能力。

## 调用计数、预算与恢复

每个执行案例外层 4 次 FAKE、内层 1 次请求，预算分别记录。
6 个案例合计 24 次外层 FAKE、3 次内层 REPLAY、3 次内层 FAKE；Dev D01 另有 4 次外层 FAKE、0 次内层请求。
REPLAY 的旧 Token 为 imported_usage，FAKE 为 simulated_usage，按调用记一次，不按 proposal 累加。
三项历史回执报告 input=12358、output=1193、total=13551；thinking/cache 细节未知，不按零处理。
未知成本保持 null；本轮新增真实计费请求/费用为 0。没有把历史 Token 或费用算作新调用。

每个 run 只有一个语义批次槽，策略独立约束请求数、输入/输出 Token 上限、预估成本上限。
调用前 exclusive-create + fsync 精确请求预留，绑定外层 run/tool call、调查 ID、模型、策略、请求与来源 hash。
request/response/result 使用原 ModelArtifactStore，结果绑定请求和响应工件 hash；预留保存 REPLAY 来源身份。
这些是 Runtime 审计工件，不是 P4 Receipt/Promotion。

- 已完成结果：重启后读耐久回执，不再请求 Provider。
- 响应已落盘、结果未落盘：只重放本地响应，不再请求 Provider。
- 预留已落盘、响应结果未知：SEMANTIC_OUTCOME_UNKNOWN，禁止自动重试。
- Provider 失败：保留一次预留/失败结果，不自动重试；未配置、预算不足、输入不完整在请求前阻断。
- 外层观察已耐久化的 Checkpoint：3 次原外层 + 1 次剩余 Final，内层仍为 1 次。
- 外层 Tool Ledger 已提交但 observation 尚未提交的崩溃窗口：现有循环恢复安全失败
  MODEL_LOOP_RUNTIME_FAILED，外层/内层新增请求均为 0。没有宣称所有窗口均可成功恢复；
  本轮保留这个旧 Runtime 限制，未扩大为通用恢复层重构。

## 测试、只读与 Dev 状态

[evidence/checks.json](evidence/checks.json) 给出结构化测试计数、失败集合与检查命令；
JUnit、实际回执、结果、Checkpoint 导出和 Reviewer 保存在 evidence 下。
[failure-attribution.json](evidence/failure-attribution.json) 将故障注入断言映射到准入、工具、Provider、
证据、Reviewer 和恢复阶段，不推测模型内部原因。

| 检查 | 本地结果 |
| --- | --- |
| 独立审阅修复后 A/B/D/P4 聚合 | 126 passed |
| 其中本轮 D 专项 | 56 passed，包含 17 项独立审阅回归 |
| 旧流程/注册/权限/模型循环/恢复/Dev 契约 | 124 passed / 14 retained failed |
| compileall / diff --check | 通过 |

14 项旧失败与 B 已在固定基线复现的集合相同，新增失败集合为空：5 项旧 M1.1 脚本的 source_context
已不合现有 Schema，9 项依赖未入库的 work/phase14_gemini 录制文件。完整失败 ID 及 B 的基线证据引用
见 checks.json。没有改旧测试来变绿，也不宣称全仓库/CI 全绿。
系统 Python 退出时可能产生 pyreadline3 析构警告；测试退出码和 JUnit 独立记录。

专项覆盖错 Workspace/调查 ID/非法路径与密钥参数、来源失效/撤销、撤销端点、缺来源/竞争观察、
截断/字节与 Token 预算、Provider 未配置/失败、请求与成本预算、外层步数预算、引用不足、
Reviewer 期间来源变化、响应工件变化、内外层恢复计数。B/P4 回归覆盖撤销关系、UNKNOWN/CONFLICT、
历史和日期比较、只读不建库及已有 sidecar 字节不变；旧 ask/connect 与自足 team_decision 用例通过。
Runtime 执行只写自己的审计/Checkpoint/Trace；合成原文、Vault 锚和 P4 sidecar 不被写入。

`evals/m3_task_impact/semantic_runtime_adapter.py` 实际通过既有 execute 与 PublicTools 读取 D01 原文，
进入同一 Runtime 并产生三项工具回执。它没有自行实现 M1 抽取或关系问题识别，故 EMPTY_QUERY_SET/BLOCKED，
归一化回答 ABSTAIN，业务状态 NOT_EVALUATED；其余 11 Dev NOT_RUN。
新增 adapter/证据 runner 不读取 Gold 或保留集，不调用 scorer，不改 Gold 或覆盖冻结指标。
原 evaluator 单测仅回归原契约与静态标注检查，不作为新业务成绩；没有保留集的 Agent 执行或评分。
没有以脚本的完整答案冒充业务得分。

## 复现与证据

在本分支 Worktree 根目录运行，输出目录须使用新名称：

```powershell
$env:PYTHONPATH='src;.'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
python scripts/evaluate_m3_semantic_integration.py --output work/m3d-reproduce-unique
python -m pytest tests/unit/test_semantic_context_builder.py tests/unit/test_semantic_invocation.py tests/unit/test_call_bound_executor.py tests/integration/test_semantic_runtime_integration.py tests/integration/test_semantic_recorded_runtime.py tests/integration/test_semantic_runtime_boundaries.py tests/integration/test_semantic_dev_contract.py tests/integration/test_semantic_review_guards.py -q --basetemp work/m3d-tests-unique --junitxml=work/m3d-tests.xml
```

聚合与旧回归完整命令见 checks.json；旧回归命令预期保留上述 14 项失败。
实际完整审计目录 `work/m3d-evidence-reviewed-04/` 被保留。evidence 包含脱敏的公开合成数据，
Checkpoint 导出省略 Host 绝对路径，内层 request/reservation/response/result 保留原始字节供 ArtifactRef hash 核对。
evidence 的局部 .gitattributes 固定 JSON/XML 为 LF，避免 Windows checkout 改变审计工件 SHA。
没有 API Key 或真实用户资料。原始 SQLite Checkpoint、外层完整模型工件、Trace 均可由上述命令重新生成。

## 独立审阅与后续阻塞

独立只读复审结论 PASS，无 P1/P2 阻塞；首轮五项问题和修复证据记录于 [REVIEW.md](REVIEW.md)。
验收范围为离线 Runtime 集成与安全结果边界，
不是独立语义策略或生产自主能力验收。后续仍需显式定义语义验收策略、授权生产 M1/问题发现装配、
以及另行设计 P4 Receipt/Promotion；本轮没有实现它们。
真实 Provider 新请求默认关闭；若后续需要，须先明确模型、次数、Token 上限与费用并取得授权。

按用户授权，只提交本轮文件并正常推送独立集成分支、回读远端 SHA；不创建 PR、不合并目标分支、
不更新 m2-progress、不修改其他 Worktree。完成后停止。
