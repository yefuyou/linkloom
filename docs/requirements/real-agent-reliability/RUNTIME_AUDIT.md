# Runtime 源码审计：Real Agent Reliability

## 审计边界

- 角色：Reviewer，只读审计；没有修改产品源码、旧工作树或历史实验。
- 基线：`c657e7e4bdace3b673292f740a57d080a7002a76`，审计开始时 `git status --short` 为空。
- 需求边界：`unified-ask-experience`、`bounded-multihop`，以及仓库中与请求所称
  “unified-source-investigation” 对应的现行目录 `docs/requirements/unified-investigation/`。
- 重点源码：`runtime/model_loop.py`、`runtime/graph.py`、`runtime/task_plan.py`、
  `runtime/team_decision_planner.py`、`agents/runtime_adapter.py`、
  `agents/model_adapter.py`、`agents/claim_grounding.py`。为了还原入口和工具契约，
  另外只读取了直接调用者 `ui/{unified_ask,investigation,backend}.py`、
  `agents/{retrieval_agent,coordinator}.py`、`tools/runtime.py` 与少量模型/投影定义。
- 没有 Provider 调用，没有真实 vault 访问或修改。本文记录的真实 Gemini 数字来自主
  Agent 在同一指定基线上的独立实验；本 Reviewer 只把它们与源码路径交叉核对。

## 结论

当前产品 source-relation 三跳不是“模型从开放问题自行规划出三跳”的证据。产品先用
host 内的正则规则编译器 `plan_relation_investigation` 生成显式 `TaskPlan/v2`，随后强制
`planning_mode="template"` 进入 Runtime。模型仍决定每轮调用哪个只读工具、读哪个检索
结果以及何时提交 Final，但路径种类、方向和依赖已经由 host 冻结。

`12` 是产品 source investigation 的模型回合上限，不是三跳必需轮数。历史 11 轮样例
是一个控制器执行出 `3 search + 7 read + 1 Final` 的结果。V2 一轮可发多个工具调用；同
基线真实 Gemini 已以两跳 5 轮、三跳 7 轮完成，因此 11 轮既不是协议下限，也不是扩大
上限的理由。现有最小预算校验仍采用保守的 `2 * subgoal_count + 1`（逐跳 search/read，
最后 Final）。

前两项可测量瓶颈候选是：

1. **verified evidence context 的 workspace identity 比较错误，使产品真实请求中的
   `evidence_context` 全部为空。** Ledger 和 `tool_turns` 仍让本次两跳/三跳成功，因此
   这是被另一条上下文通道掩盖的可靠性缺口，不是已经观测到的错误答案。
2. **`search_notes.source_context` 的模型可见 JSON Schema 没有列出或要求 Runtime source
   identity 字段。** 描述要求模型“完整复制”，执行时才由语义校验逐字段拒绝遗漏或篡改。
   这把一个确定性契约变成首轮提示词遵循问题，可用首轮参数通过率、额外纠错轮数和
   `TOOL_INVALID_ARGUMENTS` 频率量化。

Provider 延迟也是明确压力点：同基线真实两跳为 5 轮 / 40.99 秒，三跳为 7 轮 /
45.11 秒。源码中的 `deadline_ms=5000` 目前只是 `AgentTask` 数据，没有执行点；它不是
5 秒 wall-clock timeout。应先把每轮/每工具时延拆开测量，再决定是否优化检索读取或
减少 Provider 回合，不应仅凭总时长做大型 cache 或 Runtime 改写。

## 1. TaskPlan 的真实来源

TaskPlan 有三种入口，基线没有统一记录它们的 origin：

| 来源 | 触发路径 | 实际约束 | 当前 origin 证据 |
| --- | --- | --- | --- |
| host 显式传入 | `RunRequest.task_plan` | goal 必须等于 query；`TaskPlan.from_dict` 与 backend 再验证 | request 中有 plan，但没有 origin 字段 |
| host 规则编译 | `plan_relation_investigation` 或 adapter 的 `plan_team_decision_query` fallback | 正则命中后生成 answer-free plan | 与普通 host 显式 plan 无法仅从持久化 request 区分 |
| model 生成 | `planning_mode="model"` 且没有显式 plan | 无工具的 proposal；Runtime 全量验证；最多一次 repair | trace 仅此路径写 `plan_origin="model_generated"` |

产品 source-relation 的路径是：

1. Unified Ask 调用 `plan_relation_investigation`（`src/linkloom/ui/unified_ask.py:220-257`）。
2. `ProductInvestigationService.start` 重新编译并要求传入计划字节级等价，避免 host 传入
   另一个计划（`src/linkloom/ui/investigation.py:75-92`）。
3. source 运行选择 `relation_max_steps`，默认 12，并把显式 plan 交给 backend
   （`src/linkloom/ui/investigation.py:47-58,174-188`）。
4. backend 见到显式 plan 就设置 `planning_mode="template"`
   （`src/linkloom/ui/backend.py:92-97,123-143`）。
5. Graph 仅在 `planning_mode="model" and task_plan is None` 时调用
   `propose_task_plan`（`src/linkloom/runtime/graph.py:499-517,532-611`）。
6. adapter 只有在仍无 plan 时才调用旧模板 `plan_team_decision_query`
   （`src/linkloom/agents/runtime_adapter.py:786-795`）。

因此 DELIVERY 中的 product 三跳应表述为“host 规则规划 + model 执行工具循环”，不能
表述为“真实模型完成了开放式三跳规划”。

### plan_origin 建议

在不改变任何功能的前提下，给持久化 request/trace 增加一个由 host 设置、Runtime 验证
的 origin 枚举，例如：

- `host_explicit`
- `host_rule_relation`
- `host_rule_team_decision`
- `model_generated`

至少需要在 request artifact、`plan.validated` trace 和最终审计投影中保持一致，并记录
plan SHA-256。当前只有 model 路径在 `graph.py:603-611` 发出 origin 和 hash；显式/规则
路径缺少对称事件。该变化只补证据，不应让 Runtime 根据 origin 放宽 TaskPlan 验证。

## 2. 12 轮与三跳 11 轮

### 三种计数不能混用

- **模型回合 / Provider request**：`SingleAgentModelLoop.max_steps`。每次模型响应，无论
  是一个工具、一组工具还是 Final，都消耗一轮（`runtime/model_loop.py:2200-2275,
  2373-2515`）。
- **检索工具调用**：durable Tool Ledger 中的 `search_notes` / `read_verified_note` 条目；
  一轮 V2 proposal 可包含 1..16 个有序调用（`agents/model_adapter.py:707-760`）。
- **Coordinator usage**：检索模型步数之外再加 Reviewer step，并把 Reviewer 的验证工具
  计入 tool_calls（`agents/coordinator.py:157-180,274-301`）。Graph 因此给 Coordinator
  `min(12, request.max_steps) + 2` 的外层余量，但不会把它变成额外模型回合
  （`runtime/graph.py:750-772`）。

### 为什么上限是 12

- 产品 source path 的 `relation_max_steps` 默认值和硬上限都是 12，Temporal 保持 8
  （`ui/investigation.py:47-58,92`）。backend 同时把相同数值设为
  `max_provider_requests`（`ui/investigation.py:174-186`）。
- RetrievalAgent 实际取 task cap、run policy 和剩余 Provider budget 的最小值
  （`agents/retrieval_agent.py:1212-1249`）。
- `validate_model_proposed_plan` 要求 `max_steps >= 2*n + 1`
  （`runtime/task_plan.py:185-197`）。三个 subgoal 的保守最小值是 7；12 留出读多个候选、
  一次合法 alternate、参数纠错或冲突源读取的空间。
- 到达上限后产生 `MODEL_MAX_STEPS_EXCEEDED`，没有隐式扩容
  （`runtime/model_loop.py:2517-2533`）。

### 为什么历史三跳用了 11 轮

`bounded-multihop/DELIVERY.md` 记录的旧样例是 3 次 search、7 次 read、1 次 Final，共
11 次 FakeModel decision。读 7 次来自控制器选择读取检索返回的多个候选，不是 TaskPlan
要求每跳必须读固定数量。Prompt 只要求每个 dependent search 后至少读回其证据；
`read_verified_note` 只能读之前 search 返回的 evidence ID
（`agents/retrieval_agent.py:308-333`，`agents/runtime_adapter.py:521-532`）。

同基线的补充测量进一步区分了协议和控制器行为：

| 执行 | 两跳 | 三跳 | 解释 |
| --- | --- | --- | --- |
| SDK/Fake controller 预检 | 6 轮；2 search + 3 read | 10 轮；3 search + 6 read | controller 对 leader 使用 `limit=4`，会读非答案候选 |
| 真实 Gemini 3.8 | 5 轮 / 40.99s | 7 轮 / 45.11s | Provider 使用一轮多 tool calls 批量读；均成功 |
| 历史 DELIVERY | 6 轮；2 search + 3 read | 11 轮；3 search + 7 read | 固定 FakeModel 轨迹，不代表模型下限 |

真实三跳 7 轮正好是三次 search proposal、三次批量 read proposal、一次 Final。优化方向
应优先看“每次 search 返回多少需要验证的候选”和“独立 read 是否可在同轮批量发出”，
而不是增加 `max_steps`。

## 3. 工具与证据契约

source run 显式关闭 Decision Memory、project memory 和候选 capture；只给 RetrievalAgent
注册 `search_notes`、`read_verified_note`（`ui/investigation.py:161-186`，
`agents/runtime_adapter.py:864-907`，`agents/registry.py:53-78`）。

可信链条如下：

1. `search_notes` 必须携带 source context。ToolRuntime 在 executor 前逐字段对比 Runtime
   期望 identity，并拒绝额外字段（`tools/runtime.py:888-909`）。
2. 对 TaskPlan search，adapter 从 durable pending Ledger 找到当前调用，验证 subgoal、
   query variant，并只从已验证父 read 推导 `dependency_entities`
   （`agents/runtime_adapter.py:220-329`）。检索 query 由 Runtime 重建，不信任模型传入的
   query 文本。
3. `read_verified_note` 只接受本次 run 的 search 返回的 evidence ID
   （`agents/runtime_adapter.py:521-532`）；读取时重新扫描并核对 relative path、content
   SHA-256、quote 内容与 quote SHA-256（`agents/runtime_adapter.py:496-513`）。
4. TaskPlan 完成度完全从 Tool Ledger 重建：只有 search hit 被同一 verified passage 读回
   才算 observed（`runtime/task_plan.py:1-6,424-543`）。
5. Final 再由 relation profile、claim grounding 和 Reviewer 验证。声明关系只有
   `alias_of_project`、`owner_of_project`、`reports_to`
   （`agents/claim_grounding.py:54-75`）。

### 契约缺口：模型可见 Schema 没有 source identity 字段

工具说明声称“copy every Runtime source_context identity field”，但嵌套 JSON Schema 的
properties 只有 `scope_path`、`subgoal_id`、`dependency_entities`、`query_variant`，没有
`index_path`、`index_sha256`、`index_schema_version`、`vault_root_fingerprint`、
`document_count`，也没有 nested `required`
（`tools/runtime.py:740-790`；权威字段定义在 `runtime/models.py:139-180`）。

Runtime 的后置语义校验仍能安全拒绝遗漏/篡改，因此没有证据表明它绕过 source boundary；
问题在于正确首轮调用依赖模型从长 user prompt 猜出 Schema 没表达的字段。建议让注册工具
根据 `expected_source_context` 构造显式、typed、required 的嵌套 schema，并保留执行时
逐字段等值校验。验收指标应是：首个 search 的 source identity 通过率、因
`$.source_context.<field>` 产生的纠错轮数、跨 workspace/旧 index 攻击仍全部被拒绝。

## 4. 上下文重复、选择与剪裁

每个模型回合可能同时看到三份相关信息：

- `user_input`：每轮重复完整工具说明、query、source context 与 TaskPlan；retrieval
  instruction 本身上限 16 KiB（`agents/retrieval_agent.py:183-233,289-338`）。
- `tool_turns` / legacy `tool_history`：Provider 对话历史中的调用和结果；最多 16 个结果、
  128 KiB，并保留最新的完整 turn suffix（`agents/model_adapter.py:1196-1236,
  1483-1677`）。
- `evidence_context`：从 durable Ledger 另行投影的最多 8 个 verified results / 24 KiB，
  Provider adapter 又把它序列化附加到初始 user text
  （`runtime/model_loop.py:271-532`；`agents/providers/gemini_api.py:800-821`）。

因此设计上 verified evidence 可能同时存在于 `tool_turns` 和 `evidence_context`；前者保留
工具因果顺序，后者想作为旧历史被裁掉后的稳定证据视图。两者目的不同，但会增加 token。
当前 `_verified_evidence_context` 只在自身集合内按 passage identity 去重，并不对
`tool_turns` 去重（`runtime/model_loop.py:405-488`）。修复空 context 后，应直接测量每轮
`request_audit.segments`，再决定是否只保留早期、即将从 history suffix 中掉出的 read，
而不是删除任一通道。

总 request 超过 96 KiB 时，Runtime 先丢最旧 `tool_turns`，再丢最旧 legacy history；
verified context 和最新 observation 优先保留（`runtime/model_loop.py:55-95`）。这是一种
机械 suffix 剪裁，没有语义摘要。完整 Ledger 不被裁，TaskPlan/Final 安全判断仍从 Ledger
重建，所以剪裁主要影响模型能否记住早期路径，不会授权未经验证的依赖。

### 已复现的 identity 错配

产品 UI 给 RuntimeAgentAdapter 的 workspace 是产品 `ws_*` ID
（`ui/investigation.py:161-170`；`runtime/graph.py:239-260`），检索 evidence 也以该值写入
`workspace_id`（`retrieval_v2/runtime_backend.py:74-107,173-190`）。但构造模型请求时，
`_verified_evidence_context` 的 `workspace_id` 参数却是
`state.source.vault_root_fingerprint`（`runtime/model_loop.py:2308-2316`）。选择器随后把
value.workspace_id 不等于该 fingerprint 的值过滤掉
（`runtime/model_loop.py:316-369`）。

主 Agent 在同一基线真实两跳和三跳 artifacts 中观察到所有
`ModelTurnRequest.evidence_context == []`，与上述分支完全吻合。运行仍成功是因为
`tool_turns` 带回历史结果；这不能证明 evidence fallback 正常。最小修复应传入与检索
evidence 相同的 workspace identity，同时保持 vault/index fingerprint 的独立校验字段。

修复验收至少包括：

- 产品 `ws_*` 两跳/三跳的第二轮以后出现预期 verified context；
- 所选每项的 workspace、call ID、content/quote hash 与 Ledger 一致；
- 另一个 workspace 的 value 仍被过滤；
- 记录 evidence context 与 tool history 重复字节数、最后一轮 request 总字节和剪裁数；
- context 修复不改变 Final、Reviewer、Ledger 或 Provider request 数，除非模型实际利用它
  减少回合，此时单独报告行为变化。

## 5. 预算、UNCERTAIN 与 Ledger

### 预算层次

- model/provider：source 默认 `max_steps=max_provider_requests=12`；每个 durable model
  record 消耗一份 Provider budget（`agents/retrieval_agent.py:1212-1249`）。
- tools：Coordinator 上限 20；ToolPolicyEnforcer 在 pending checkpoint 成功后、executor
  前只消费一次（`agents/coordinator.py:38-45,134-155`，
  `tools/tool_policy.py:85-143`）。
- context：instruction 16 KiB，provider-visible user input 32 KiB，verified context 24 KiB，
  model request 96 KiB，tool history 16 results / 128 KiB；实际 request 的 96 KiB 先触发。
- planning：model plan proposal 自身消耗一次 Provider request；repair 最多一次，且必须为
  proposal/repair、每 hop search/read 和 Final 预留完整预算
  （`runtime/graph.py:499-517,532-602`）。显式 product plan 不消费 plan proposal request。
- time：`AgentTask.deadline_ms=5000` 只在创建和验证数据时出现
  （`agents/coordinator.py:112-130`，全仓无执行引用）。真实 40.99s/45.11s 进一步证明它
  不是运行时 timeout。

### Ledger 与不确定状态

ToolRuntime 的顺序是：schema/semantic validation → policy eligibility → `record_pending` →
pending checkpoint → consume policy budget → executor → terminal Ledger → terminal checkpoint
（`tools/runtime.py:423-709`）。pending checkpoint 失败时 executor 不运行；terminal
checkpoint 失败则返回 uncertainty，不能假装结果已可靠持久化。

Provider 调用也先持久化 request，再标记 request sent，得到 response 后持久化完整
response，之后才执行 proposal（`runtime/model_loop.py:2341-2489`）。Provider 返回
`ModelProviderError(outcome="unknown_provider_outcome")` 时 loop 终止失败，不自动重发；
UI 把它投影为 `PROVIDER_UNCERTAIN` 并隐藏 Final
（`agents/model_adapter.py:440-513`，`runtime/model_loop.py:2470-2472`，
`ui/projection.py:231-248`，`ui/unified_ask.py:339-346`）。

同基线真实 canary 的第一次 Gemini 请求发生 `ConnectError`：Ledger/Provider record 标记
UNCERTAIN，运行停止，没有重发，总请求数 1；保守预算预留 `$0.129024`。这属于 transport
outcome unknown，不能记作语义错误答案，也不能用 retryable 字段覆盖 durable uncertainty
边界。

## 6. 实际模型规划的限制

即使显式选择 `planning_mode="model"`，它也不是开放式 Agent planner：

- 只允许 `team_decision`，plan 阶段没有工具或 source 内容
  （`runtime/models.py:430-450`；`agents/providers/gemini_api.py:1437-1482`）。
- prompt 要求 2..4 个 subgoal；TaskPlan schema 本身允许 2..5，但每个 v2 subgoal 只能有
  一个已验证父依赖，relation path 最多两段，唯一允许的双段 root 是
  `alias_of_project -> owner_of_project`（`runtime/task_plan.py:54-118`）。
- 只支持 alias → project、project → owner、person → leader 三种 typed relation；没有任意
  graph edge、多父 join、跨 workspace join、历史有效区间推断或多 decision slot 规划。
- goal 必须逐字等于用户 query；root anchors 必须来自 query；dependent question 必须使用
  `{verified_entity}`；外部 URL、write/delete/move 等被拒绝；歧义 reporting direction 被
  拒绝（`runtime/task_plan.py:185-242`）。
- 无效 plan 最多 repair 一次。repair 仍从头验证，不能授予工具或答案
  （`runtime/graph.py:546-587`）。

产品当前 source route 更窄：`plan_relation_investigation` 是 exact bounded regex grammar
（`runtime/team_decision_planner.py:187-236`）。离线 planner matrix 在本基线得到：

```text
Who owns the project behind Copper Meadow and who leads that owner?
  -> recognized; alias, owner, leader
Orion 项目负责人的直属领导是谁？
  -> recognized; owner, leader
谁负责琥珀线路？其直属领导是谁？
  -> unsupported
```

第三项正是冻结 HOLD-H 的已知 grammar limit。它应继续 fail closed，直到另一个明确 SPEC
决定扩大 host grammar 或真正评估 model planning；不能把真实执行阶段成功率当作 planning
泛化率。

## 7. 性能候选（暂不定性为缺陷）

`RuntimeAgentAdapter._read_documents` 明确“每个 tool boundary 重新读”
（`agents/runtime_adapter.py:150-152`）。每次 search 的 retrieval backend 先调用 document
provider（`retrieval_v2/runtime_backend.py:116-160`），每次 verified read 又调用
`_read_documents` 后遍历定位目标（`agents/runtime_adapter.py:496-513`）。这提供 freshness，
但成本会随 source 数和 read 数放大。

它是值得做 10/100/500 sources profile 的候选，但本文不把它排进前两项，因为现有
40–45 秒真实耗时主要包含 5–7 次 Provider 往返，尚无 profile 证明文件扫描占主导。若
profile 显示 verified read 显著增长，最小方向是按 evidence 的 relative path 读取目标并
继续校验 content/quote hash；不要引入跨 run 大型 cache，也不要牺牲 stale-source 检测。

## 8. 可复现证据与命令

本 Reviewer 实际执行：

```powershell
git rev-parse HEAD
# c657e7e4bdace3b673292f740a57d080a7002a76

git status --short
# 审计开始时为空

rg -n -i "unified[- _]source|source investigation|source-investigation" docs/requirements
# 定位到 docs/requirements/unified-investigation/

$env:PYTHONPATH='src;tests/integration'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
# 运行 plan_relation_investigation 的三问 matrix；输出见第 6 节。
```

曾尝试在系统 `TemporaryDirectory` 中启动完整离线 UI fixture；Windows sandbox 将临时路径
解析到不可写的 AppContainer Temp，SQLite 在产品执行前报 `unable to open database file`。
这不是产品失败，未产生 Provider 请求，也没有据此宣称 E2E 结果。完整 Fake/HTTP 复现可
在工作树可写路径指定 pytest basetemp：

```powershell
$env:PYTHONPATH='src;tests/integration'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
python -m pytest tests/integration/test_unified_investigation_flow.py -q `
  --basetemp work/real-agent-reliability-pytest
```

检查请求 artifacts 时，逐 turn 读取：

```text
runtime/models/model/<run_id>/<run_id>_turn_N/request.json
  model_request.evidence_context
  model_request.tool_turns
  request_audit.segments
  context_audit.selected_verified_calls
  context_audit.dropped_verified_calls
  context_audit.dropped_old_tool_turns
```

## Review verdict 与下一步

Verdict：**Needs changes before claiming real-agent planning/reliability acceptance**。现有 evidence
binding、read freshness、Ledger、Reviewer 和 UNCERTAIN fail-closed 边界有明确源码支撑；
真实两跳/三跳执行成功也有同基线证据。仍不能声称：产品使用模型生成了 source TaskPlan、
verified evidence fallback 正常工作、5 秒 deadline 生效、或任意自然语言/任意关系可规划。

建议最小顺序：

1. 修正 evidence context 的 workspace identity，并为 source context 工具 schema 显式声明
   required identity；补 artifact 级验收。
2. 添加 plan origin 审计字段和对称 trace，不改变 planner 行为。
3. 保留 `12` 上限，先以真实 5/7 回合轨迹测量首轮工具参数通过率、重复上下文字节、每轮
   Provider latency 和 read scan latency。
4. 只有 profile 证明 read freshness 扫描成为主导成本时，实施目标文件读取的最小优化。

## Learning reflection

- Role：Reviewer。
- 学到的关键边界：TaskPlan 的“计划存在”不等于“模型规划”；产品 source path 的计划
  来自 host 规则。Ledger 是安全事实源，model context 只是可裁剪视图。
- 证据：上述指定文档、精确源码行、离线 planner matrix，以及主 Agent 的同基线 Fake /
  real Gemini artifacts 摘要。
- 下一角色：主 Agent/Worker 在独立实现边界内修正两项 identity 契约并执行 artifact 级
  回归；之后由独立 Reviewer 检查 origin、context、Ledger 和 UNCERTAIN 证据。
