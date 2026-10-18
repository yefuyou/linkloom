# M3-B：Existing Agent Runtime × Task Relation Tools

## 交付范围

起点 `26392432dcef70692461e6f2f50bc1bd5528f69a`；独立分支
`codex/linkloom-m3-runtime-tools`，独立 Worktree
`D:/webproject/Linkloom/work/linkloom-m3-runtime-tools`。
本次用户任务即批准边界，落盘于 [SPEC.md](SPEC.md)。未读取或修改 A 的工作目录，
未修改 M2/P1–P4 语义模块、M3 Gold 或评分器，未新建 Agent、Router、Coordinator 或 Runtime。

## 工具、参数与授权

| 工具 | 参数 | 输出及限制 |
| --- | --- | --- |
| `inspect_task_context` v1 | 必填 `workspace_id`；可选 `anchor_ids`（最多 16 个 ID）、`max_candidates`（1–100，默认 20） | 经当前来源核验的 ObservationRef、M1 原始抽取事实与字段证据、候选原因/不确定性、预算、截断和缺失上下文。候选恒为 UNCONFIRMED，不能证明关系 |
| `query_task_relations` v1 | 必填 `workspace_id`；可选 `observation_id`、`include_history`（默认 false）、`as_of`（真实日历日期 YYYY-MM-DD） | 复用 P4 RelationQuery 的关系、UNKNOWN/CONFLICT/阻断、持久生命周期、来源检查、计划修订、历史与当前日期比较 |

Workspace 在注册 Schema 中绑定到宿主授权值，额外参数拒绝；ID 仅允许业务标识。
模型不能提供 SQLite 路径、快照读取器或任意文件路径。新工具仅对显式
`task_relations` 工作流授予；原 ask/connect/team_decision 能力列表不变。
现有 ToolPolicy 保持 network/vault_write/gold_access=deny。

宿主注入 `TaskRelationTools(workspace_id, snapshot_provider, relation_path=...)`。
快照可包含 M1 TaskObservation 或 TaskObservationRead；M1 的非当前/冲突状态会传给 P4，
不能只信任嵌入的旧观察。每次调用及 Reviewer 都重新读取宿主快照。
默认输入预算为 128 观察、64 来源、500,000 字符；超限显式失败。
inspect 输出上限 32 KiB，query 输出上限 48 KiB；超限返回明确截断标记。

P4 查询采用已有只读连接，不构造 TaskRelationStore 或 TemporalDecisionStore。
不存在 sidecar 时返回 `NO_VERIFIED_RELATIONS`，不建库、不写判断、不恢复撤销关系。
已有但均无法当前发布时为 `NO_CURRENT_SUPPORTED_RELATIONS`；支持关系存在但未全部返回时，
仍保留 `VERIFIED_RELATIONS_AVAILABLE` 和完整快照支持计数，不能把截断当作否定。

query 的重复证据采用引用表：`span_ref → evidence_spans`、
`observation_ref → observation_refs`、`observation_snapshot_ref → observation_snapshots`。
表保留精确来源版本、quote、hash、偏移和原始抽取字段；有无损展开测试。
计划中的 revisions 引用顶层关系，链上额外关系会一并呈现。
这是工具视图，展开后才能交给 P4 的 canonical from_dict；不是新存储格式。
历史记录与历史 comparison 保留 P4 标记，旧 SUPPORTED 不等于 current_supported。

## 实际调用链

```text
RuntimeEngine.start_multi_agent(RunRequest(workflow="task_relations"))
  -> RuntimeAgentAdapter（注入授权 TaskRelationTools，禁用 Decision Memory）
  -> 现有 Coordinator
  -> 现有 RetrievalAgent
  -> 现有 SingleAgentModelLoop（无修改）
  -> 现有 ToolRuntime：Schema -> Policy -> pending Ledger/Checkpoint -> executor
      -> inspect_task_context -> M1 来源复核 -> M2 discover_candidates
      -> query_task_relations -> P4 RelationQuery.list / plan / comparison
  -> 下一轮模型读取真实 ToolResult
  -> Final：context_call_id + relations_call_id
  -> Runtime 验证 Final request 中确实可见对应成功回执，投影 task-relations-result/v1
  -> 现有 ReviewerAgent -> validate_schema 回调重读来源和 P4 生命周期
  -> 现有 Coordinator 发布结构化结果 -> Checkpoint / Trace / result.json
```

Final 不能附加模型自行断言的关系或自由文本；伪造、失败或模型未看到的回执会拒绝。
输出 `answer_status=PARTIAL`，明确此为有界工具回执投影、LLM 语义能力尚未接入。
步数、Provider 请求预算、Token/上下文预算、Provider 错误分类、Ledger 与恢复机制
沿用原 Runtime。本工作流通过 Python RunRequest 显式选择，现有意图路由及 UI 不变。

## 已执行证据

[evidence/summary.json](evidence/summary.json) 保存 5 条实际执行轨迹索引；每条均为
3 轮 FakeModel、2 次真实 ToolRuntime 执行、持久 Checkpoint 和 Reviewer 结果：

| 场景 | 查询结果 | 外层运行 / Reviewer |
| --- | --- | --- |
| verified_rules | VERIFIED_RELATIONS_AVAILABLE，P1/P3 已有规则证据及日期比较 | completed / evidence_sufficient |
| no_records | NO_VERIFIED_RELATIONS，缺失库没有被创建 | completed / evidence_sufficient |
| stale | 来源版本失效；无 current_supported，保留阻断原因 | completed / evidence_sufficient |
| revoked | 已撤销关系及依赖修订不可发布；历史仍带标记 | completed / evidence_sufficient |
| insufficient | UNKNOWN、缺证据及不可计算比较，未包装成业务否定 | completed / evidence_sufficient |

Reviewer 通过表示结构化结果准确反映当前证据及限制，不表示 UNKNOWN 被证明为正关系。
另外测试来源在模型读后失效：Reviewer 拒绝，外层失败为
`TASK_RELATIONS_EVIDENCE_REVIEW_FAILED`。

每条轨迹旁有 `checkpoint.json`（从实际 Checkpointer 回读）、
`model-observations.json`（实际模型请求）、`checkpoints/results/.../result.json` 和
`traces/.../events.jsonl`。原 SQLite Checkpoint 与全部模型耐久工件保留于
`work/m3-runtime-evidence-reviewed-04/`，可通过下列命令重新生成。
来源内容、合成 Vault 锚文件和 P4 sidecar 的前后 hash/内容一致；Runtime 仅在自身审计目录写
Checkpoint、Trace、模型工件和结果。fixture 准备阶段通过原 P4 Store 写入测试判断，
该准备动作在只读 Runtime 执行之前。

**能力声明**：这证明 FakeModel 工具接线和已有规则读取成功。工具顺序来自脚本，
M1/P4 输入来自公开合成 Dev fixture，不能称为真实模型自主规划、M3 自主抽取/发现成绩
或新的 LLM 语义判断成功。未调用真实 Provider，未运行保留集，未产生 M3 业务评分；
真实 Provider token、成本均未测量。

## 测试与旧回归

[evidence/checks.json](evidence/checks.json) 给出结构化结果及失败集合对照。

| 检查 | 结果 |
| --- | --- |
| 新工具及 Runtime（含独立审阅修复） | 21 passed：9 单测 + 12 集成 |
| 原 P4 Query / Store | 27 passed |
| 原流程/模型循环/权限/恢复/M3 evaluator 回归 | 118 passed，14 failed |
| 固定起点对当前 14 失败的定向回放 | 同样 14 failed；新增回归失败集合为空 |
| compileall / git diff --check | 通过 |

新增覆盖错 Workspace、失效来源、已撤销关系、证据不足、工具权限/预算耗尽、
模型步数耗尽、Provider 未配置、非法参数/日期/路径字段、未观察或伪造 Final、
Reviewer 二次核验、截断状态、历史过滤与计划链共用数量预算、只读不建库与原工具能力不变。
原回归中的 ask/connect，以及自足的 Team Decision Final-contract 正向/反向用例通过。

保留的 14 项失败：5 项旧 M1.1 FakeModel 脚本提供过时的 source_context，当前基线
即拒绝为 TOOL_INVALID_ARGUMENTS；9 项依赖未入库的 `work/phase14_gemini` 录制文件。
固定起点使用 `git archive` 在 B 的 work/ 内取出源码、测试、evals 及所需 seed 文档，
逐项复现相同失败；没有修改旧测试来变绿。未宣称整个仓库测试全部通过或 CI 已通过。
最初不完整归档/较长路径导致的额外基线失败不作为等价对照，正式对照为
`evidence/baseline-retained.xml`。
系统 Python 退出时可能打印 pyreadline3 析构警告；JUnit 与退出码均已独立保存。
入库 XML 仅规范化了行尾空白，原始 JUnit 完整保留在 work/ 对应路径。

## 与 M3-A 的集成接口与剩余项

`inspect_task_context.semantic_proposals.status=NOT_CONNECTED` 明确消费边界：
当前版本的 ObservationRef + 精确来源证据 + M2 candidate endpoints/reasons。
A、B 合并后，由 A 的已校验 SemanticProposal 消费此边界，并定义 P4 验证/重放接入；
B 没有复制 Prompt、语义分类规则或 SemanticProposal 数据模型。
旧规则 UNKNOWN 只表示现有规则/证据不足；不能据此作最终业务否定。
正式 Decision Memory 权威依赖若没有授权读回上下文，P4 继续阻断，不自动建库补齐。

后续集成需要显式提供真实 Workspace 的有界只读快照提供器；本轮仅验证隔离合成输入。
大 sidecar 内部读取仍沿用 P4 的查询行为；此工具限制输入及输出，未重写 P4 数据访问层。

## 复现命令

在本分支 Worktree 根目录，使用现有 Python 开发依赖：

```powershell
$env:PYTHONPATH = 'src;.'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'

python -m pytest tests/unit/test_task_relation_tools.py tests/integration/test_task_relation_runtime.py -q --basetemp work/reproduce-new-unique --junitxml=work/reproduce-new.xml
python -m pytest tests/unit/test_task_relation_query.py tests/unit/test_task_relation_store.py -q --basetemp work/reproduce-p4-unique
python -m pytest tests/unit/test_agent_registry.py tests/unit/test_tool_policy.py tests/unit/test_tool_runtime.py tests/unit/test_runtime_models.py tests/unit/test_p83_fake_model_loop.py tests/unit/test_p84_durable_model_loop.py tests/integration/test_retrieval_tool_runtime.py tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_team_decision_final_contract_communication.py tests/integration/test_runtime_v2_multi_action_resume.py evals/m3_task_impact/tests -q --basetemp work/reproduce-regression-unique --junitxml=work/reproduce-regression.xml
python scripts/evaluate_m3_runtime_tools.py --output work/reproduce-evidence-unique
python -m compileall -q src/linkloom/agents src/linkloom/tools src/linkloom/runtime scripts/evaluate_m3_runtime_tools.py
git diff --check
```

证据 runner 使用 exclusive create；重跑请使用新的目录名。
第三条为完整的本轮 132 项旧回归选择，预期保留上述 14 失败；单项结果见 XML。
固定起点定向回放使用相同环境，在固定 SHA 归档根目录运行：

```powershell
python -m pytest tests/integration/test_m11_team_decision_vertical_slice.py tests/integration/test_team_decision_final_contract_communication.py -k 'phase14 or frozen_mps or fabricated_team or team_decision_evidence_review_failure or team_decision_cold_resume' -q --basetemp work/reproduce-baseline-unique --junitxml=work/reproduce-baseline.xml
```

归档须包含 `src/`、`tests/`、`evals/`、`pyproject.toml` 及
`docs/requirements/m1_team_decision_eval_seed/`，不从 A 取文件。
运行目录和归档均保留，没有清理用户文件。

## 独立审阅与架构检查

独立只读 Reviewer 检查正确性、权限、只读状态、证据可见性、生命周期及旧流程边界。
发现“截断后错误报告无支持关系”、错误文案、计划链历史过滤及缺库计数字段问题，
均已修复并补回归。
最终复审结果记录于 [REVIEW.md](REVIEW.md)。
架构保持任务工具负责 M1/M2/P4 适配，RetrievalAgent 负责模型循环，
Runtime 负责权限与耐久，Reviewer 负责发布前核验；无新依赖、无第二套语义执行器。

验收以本 SPEC 的工具接线范围为准；A 的模型语义接入及上述基线问题明确保留。
按用户授权，独立审阅通过后仅提交并推送目标分支；无合并、无 PR、无强推。
