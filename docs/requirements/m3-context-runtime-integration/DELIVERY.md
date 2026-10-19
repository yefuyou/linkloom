# M3-E 交付：Context Engineering 接入现有 Runtime

完成授权合成来源上的离线集成，新增真实 Provider 请求为 **0**。
外层使用 FakeModel 的显式工具动作；内层使用 D 的 FAKE / 历史提案 REPLAY。
这证明现有 Runtime 中的上下文选择、自动压缩、精确回补和冷恢复接线，
不代表真实模型自主规划成功、生产业务成功率或独立语义验收。
模型提案继续保持 `SEMANTIC_NOT_EVALUATED`、`publishable=false`。

## 集成来源和接口

- D 起点：`31dde91f0e9b6015abc5754e995692a2f1db89ec`。
- C 来源：`9520362261f8f850191c0fa82f04bdc6452ddc05`。
- 共同祖先：`26392432dcef70692461e6f2f50bc1bd5528f69a`。
- 新独立 Worktree：`work/m3e-context-runtime`。
- 分支：`codex/linkloom-m3-context-runtime-integration`。
- 正常合入 C 的提交：`aa78e8019415bf5a2f23294b7640f92bf53864d9`。

C/D 的改动文件不重叠，合并无冲突。没有修改 A/B/C/D 原分支或工作目录，
没有变更旧 Gold、保留集、冻结指标、M2/P1–P4 业务语义和正式 Decision Memory。
没有引入依赖、新 Agent、Coordinator、Router、模型循环或上下文数据库。

接口差异与最小适配见 [SPEC.md](SPEC.md)：C 接受 `RetrievedEvidence`、整数版本的
`ContextResource` 和 C `EvidenceRef`；D 持有原始 `RawArtifact`、M1 ObservationRef、
字符串 artifact version、SemanticSourceSnapshot 和实际 Tool Ledger。
适配层显式保存三种证据身份，复用已有 `ModelArtifactStore` 保存 JSON 工件。

Host 使用 `RuntimeEngine(..., context_runtime_config=ContextRuntimeConfig(...))` 启用。
仅 task_relations 取得该配置；未配置时及 ask/connect/team_decision 保留原路径。
`TaskRelationTools.context_sources()` 是 Host 读取接口，没有加入模型工具清单，
模型仍只能通过已有白名单工具取得有界回执，不能传入路径、数据库或 API Key。

## 真实调用链

```text
RunRequest(task_relations)
  -> RuntimeEngine -> RuntimeAgentAdapter -> Coordinator -> RetrievalAgent
  -> 原 SingleAgentModelLoop
     -> 每个新请求耐久化之前：RuntimeWorkingContext.prepare
        -> TaskEvidenceBridge.session：冻结一个已验证授权原文快照
        -> D build_semantic_context：完整清单/竞争观察/端点/权限/版本校验
        -> 原文段 -> canonical Runtime passage ID + C EvidenceRef -> 显式映射
        -> 既有 TaskPlan：Host typed queries 提供未解决子目标
        -> C EvidenceRecovery：重读精确原文并校验引用
        -> RuntimeAgentAdapter._assemble_context -> C ContextAssembler/select_evidence
        -> 实际请求压力：C compact_working_context + 确定性 Fake Compactor
        -> 原始证据再次恢复 -> 选择 -> 完整 ModelTurnRequest 预检
        -> 授权快照边界重读，若变化则 BLOCKED
     -> 原 request/response 工件、Checkpoint、预算与 Provider 错误分类
     -> 外层 FakeModel 选择已有工具 -> 原 ToolRuntime / ToolPolicy / Ledger
        inspect_task_context -> M1 / M2 候选发现
        query_task_relations -> P4 RelationQuery（只读）
        analyze_task_semantics -> D 内层独立 FAKE/REPLAY 请求 -> A 验证
     -> 下一轮读取实际工具回执，重复上述上下文准备
     -> Final 选择本 run 真实回执 ID
  -> Reviewer：WorkingContext 工件/快照/原文恢复校验 + D 来源/生命周期复核
  -> 原 task-relations-result/v1，附 working_context_audit
```

候选未召回不会产生否定结论。子目标只来自 Host 的显式问题及可观察端点；
所有声明来源都保守保留，跨文档变更来源同样是必需原文。
正反证分组依据实际回执的 supporting_evidence / counter_evidence 和精确引用，
没有回执分类的原文进入未分类调查组；共享引用单独成组，组之间不重复。
每个子目标必须覆盖声明范围内的全部必需组，超限明确披露缺口。
这是有界授权清单覆盖，不是全局任务发现或答案正确性的证明。

## WorkingContext、回补和恢复

每轮保存目标、观察到的工具进度、未解决问题、失败状态、负面证据、冲突和全部原文引用。
`observed_*` 表示工具执行已完成；业务关系和独立语义验收仍未解决。
只允许压缩 `operations` 和操作摘要，C 的保护检查确保其他字段逐项不变。

`working/<run-hash>/<sequence>-<state-hash>/` 下保存 before/state/mapping/audit，
以及当前真实 Ledger 和 RuntimeState 的不可变副本。C RecordRef 使用原始工件的
相对 locator 和 SHA；这些是实际 Ledger/Checkpoint 内容的快照，不是重造的过程摘要。
原 Ledger、Checkpoint 历史和已存在模型工件不被覆盖或压缩。

映射记录原 `evidence:*`、原 artifact version、完整 canonical `ev_v2_*` Passage 身份、
C `context_ev_v1_*` 及授权快照 hash。每个不可变 artifact-version 对应独立资源，
C 整数版本固定为 1，原始字符串版本另外保存；没有字符串拼接转换 Evidence ID。

Recovery 通过 Host 当前来源接口检查 Workspace、授权、来源注册版本/哈希、完整解析、
Unicode 字符/行范围、quote hash、原文内容和撤销状态。一次选择/回补批次只读取一个
冻结快照，边界再取新快照比较包括 queries/endpoints/竞争观察的合同身份。
权限、来源或合同变化即拒绝；不启用历史来源作为当前证据。
摘要只标为 `UNTRUSTED_OPERATIONAL_HINT`，不能成为关系证据。

冷恢复沿实际 durable request 的 hash-bound audit 装载 WorkingContext，
核对 run/task/turn、原始指令、请求 hash、配置/Tokenizer 身份、Ledger/Checkpoint 副本、
先前模型记录和当前来源。恢复本身不调用模型或重新执行工具。

实际验证了两个恢复边界：语义工具 observation 已耐久化后仅发送剩余一次外层 Final；
Final response 已耐久化后外层/内层追加请求均为 0。已有工件逐字节不变。
保留 D 的已知限制：terminal Tool Ledger 与 observation 提交之间的歧义窗口仍安全失败，
本轮不声称任意崩溃点都能成功恢复。

## 自动压缩和请求预算证据

数据来自 [long-history.json](evidence/long-history.json)，原始记录见同名 ZIP。
9 次外层 Fake 调用、8 条真实工具 Ledger、1 次内层 FAKE；7 次自动触发压缩。
累计从各轮请求投影中省略 25 个旧交互位置，**不是删除 25 条 Ledger**；
实际 Ledger 始终保留 8 条，最新 inspect/query/semantic 必要回执保留。

| 最后一轮 | 压缩前 | 压缩后 |
| --- | ---: | ---: |
| 完整请求 + 配置 envelope 字节 | 131,573 | 43,310 |
| 输入参考 Token | 32,894 | 10,828 |
| WorkingContext 序列化字节 | 4,510 | 3,094 |
| C WorkingContext.render 参考 Token | 897 | 543 |
| 必需来源/子目标覆盖 | 无请求分配空间，明确缺失 | 4/4 完整 |

目标、未完成问题、失败、冲突、证据引用和原始记录引用在压缩前后完全一致。
这里的覆盖只衡量本次声明来源清单；没有产生新的业务成功率。
供应商变更 REPLAY 在第 4 轮同样触发自动压缩，4 段原始正反/缺证上下文全部回补。

最终预检计量实际序列化后的整个 ModelTurnRequest，并加显式配置的 envelope
（离线演示为 64 参考 Token / 256 bytes），不是把各片段估计简单相加。
报告分别列出输入、输出预留、工具声明、最近结果、原文/映射、WorkingContext、
操作摘要、历史、既有 verified context 与序列化开销。输出预留 2,048，
注入的离线窗口 65,536；这些是测试配置，不是 Provider 容量声明。

保留原 8-result / 24 KiB verified-evidence、16-result / 128 KiB 工具历史、
32 KiB user_input 和 96 KiB normalized request 上限，C 默认本地选择仍为 5 条 / 3,000 Token。
完整请求必须同时满足 C 配置和旧上限，必要来源放不下时先 BLOCKED。
注入 Tokenizer 必须声明不可变 implementation ID，配置身份及 probes 都参与冷恢复检查。

演示计数为 `INJECTED_UTF8_QUADS_REFERENCE; NOT_GEMINI_OR_BILLING`，
完整 JSON 字节/4 向上取整的确定性离线参考；C WorkingContext 的单独 Token 指标
对 render 文本使用同一个注入计数器，字节指标对完整 to_json 计量。均不是 Gemini 实际 Token 或收费量。
外层 Fake usage 未提供 Provider Token；内层 FAKE simulated_usage 和 REPLAY imported_usage
单独标记，历史 Provider 用量不重复计费，新增计费请求/成本为 0。

## 实际结果与错误归因

| 轨迹 | 外层 / 内层请求 | 结果 |
| --- | --- | --- |
| revision-replay | 4 / 1 REPLAY | 证据/声明来源覆盖完整，条件性日期 +4；语义未验收 |
| causality-replay | 4 / 1 REPLAY | 保留受影响/不受影响/未知；原 REPLAY 的后两项引用仍不足，调查 BLOCKED |
| conflict-replay | 4 / 1 REPLAY | 正反证保留，日期比较 BLOCKED |
| long-history | 9 / 1 FAKE | 自动压缩、目标/未完成工作保留、原文回补、Reviewer 通过 |
| source-stale | 2 / 0 | 第三轮前 CONTEXT_SOURCE_INCOMPLETE，外层 CONTEXT_INCOMPLETE |
| budget-blocked | 0 / 0 | CONTEXT_COVERAGE_INCOMPLETE，明确 missing IDs |
| cold-final | 原 4 / 1 FAKE，恢复追加 0 / 0 | 原工件不变、Reviewer 重读后完成 |

Runtime completed / Reviewer evidence_sufficient 只说明审计投影可核验，
可以包含诚实的业务 BLOCKED 和 NOT_EVALUATED，不能解释为业务结论已成立。
原 causality 诊断缺少独立变更来源/完整调查引用的问题未由 Host 偷补。

完整性、来源变化、压缩失败与请求放不下统一停止新外层请求，错误为 CONTEXT_INCOMPLETE；
具体原因、missing IDs、source_errors、恢复结果和工件引用保留在 working_context_audit。
Reviewer 时撤销 P4 关系会拒绝结果，不允许摘要复活旧关系。

## 验证和独立审阅

[checks.json](evidence/checks.json) 记录完整命令、结构化 JUnit、已知失败对照、工件 hash 和边界。
E 新增 39 项测试；E/C/D/P4 相关集合 **229 passed**。
旧工作流/工具权限/预算/冷恢复/Dev 契约回归 **134 passed / 16 failed**；
16 个失败 ID 精确等于 D 已记录的 14 个与 C 已记录的 2 个的并集，新增失败为 0。
没有声称整个旧测试集全绿或 CI 已通过。

E 测试覆盖长历史、原文跨轮回补、正反/桥接来源、来源更新/撤销/禁止访问/截断、
引用范围和版本、来源合同原子性、P4 撤销、三种预算不足、被篡改的 WorkingContext/
mapping/checkpoint、Tokenizer/策略变化和两类无重复恢复。
D 的 Provider 失败/内层请求预算、旧工作流与 P4 只读测试同时保留通过。
独立只读审阅见 [REVIEW.md](REVIEW.md)。

7 个 ZIP 保存实际模型目录的精确 JSON 字节、最终逻辑 Checkpoint、结果及 Trace。
可移植证据仅包含公开合成来源和本项目隔离测试路径；JUnit 主机/本地身份已脱敏。
archive manifest / immutable references / request hash / Ledger / Checkpoint 身份已独立校验。
C 原合成覆盖率及 A/C/D 冻结证据只作历史对照，文件 hash 未变，没有产生新的 M3 业务评分。
保留的 C 单元测试会在内存中运行自己的合成策略对照，不覆盖冻结指标，也不作为 Agent 业务成绩。

## 复现

在本分支仓库根目录 PowerShell 执行；需项目原有 Python 开发依赖，无 API Key。
输出目录必须取新名字，避免覆盖已有证据。

```powershell
$env:PYTHONPATH='src;.'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
$env:HF_HUB_OFFLINE='1'
$env:TRANSFORMERS_OFFLINE='1'
python -m pytest tests/unit/test_runtime_context_sources.py tests/unit/test_runtime_context_preflight.py tests/unit/test_context_runtime_review_guards.py tests/integration/test_context_runtime_integration.py tests/integration/test_context_runtime_failures_resume.py -q --basetemp work/reproduce-m3e-unique
python scripts/evaluate_m3_context_runtime.py --output work/reproduce-m3e-evidence-unique
python scripts/evaluate_m3_context_runtime.py --verify docs/requirements/m3-context-runtime-integration/evidence
```

229 项集合和 150 项旧回归的精确命令见 checks.json，可分别重放。
本机 pyreadline3 在进程结束时偶有 Console 析构警告；测试退出码/JUnit 结果正常，
未因此修改环境或扩大任务范围。

## 尚未自动完成的能力

没有新的真实模型规划/压缩调用，没有完整 Provider wire Tokenizer/成本实测。
窗口计数硬保证只针对注入计数器和规范化请求加配置 envelope，不声称等于真实 SDK wire。
授权清单、M1 观察和 typed questions 仍由 D 的 Host 接口提供，未扩展全局来源调查能力。
关系语义独立验收、P4 Receipt / Promotion、正式业务发布仍未实现。
必要组过大时当前策略明确阻断，不通过放大旧边界或丢唯一证据来继续。
