# M3 跨文档任务影响评测准备

M3-D 的新增离线适配入口为 `semantic_runtime_adapter.OfflineSemanticRuntimeAdapter`，
通过本目录既有 `execute(request, tools)` 契约读取公开 Dev 来源并运行现有 Agent Runtime。
`python scripts/evaluate_m3_semantic_integration.py --output work/m3d-reproduce-unique`
生成实际调用、Checkpoint、Reviewer 与来源回执。D01 仅执行契约验证，因 M1 抽取和关系问题识别
尚未接入而阻断，业务状态为 NOT_EVALUATED；其他 11 Dev 为 NOT_RUN。它没有调用 scorer，
没有替换下述 SCRIPTED 演示、旧 Gold 或冻结指标，也没有新的真实 Provider 请求。

16 个新的合成业务问题，**12 Dev + 4 保留**。全部 Gold 为作者可见的人工标注，
不构成严格外部盲测，也不代表 M3 Agent 已实现。本目录只准备数据、适配契约和离线评分。
基线：`af001a4d968676c30979254ac70dfb750e8f9ad6`。

## 当前可运行范围

从本 Worktree 根目录运行，使用 Python 3.11+ 和已有项目依赖：

```powershell
$env:PYTHONPATH = 'src;.'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
python -m pytest evals/m3_task_impact/tests -q
python -m evals.m3_task_impact validate

# 默认路径：没有生产 M3 adapter，12 Dev 保持 NOT_RUN。
python -m evals.m3_task_impact run --output work/m3-not-run.json
python -m evals.m3_task_impact score --predictions work/m3-not-run.json --output work/m3-not-evaluated.json

# 唯一固定 Scripted 演示，仅证明目标 adapter、资料读取、独立评分能串起来。
python -m evals.m3_task_impact run --mode SCRIPTED --case D01 --output work/m3-scripted.json
python -m evals.m3_task_impact score --predictions work/m3-scripted.json --output work/m3-scripted-score.json
```

输出使用 exclusive create；重跑请换文件名。`validate` 只做两份数据的静态标注完整性核验，
不会运行或评分保留集。`run` 和 `score` 本轮只接受 Dev，拒绝保留 ID。
命令退出 0 表示文件生成/评分程序正常结束，不表示业务验收通过。

`SCRIPTED` 只实现 D01 的固定答案，实际调用 `list_sources` 和 `read_source`，记录回执；
它没有任务发现、M1 抽取、M2 关系推理或模型能力。单测中的 Gold-shaped answer 明确是评分器
正确性输入，未作为运行 Agent 的输入或系统结果。未运行的其他 11 Dev 不进入评分分母。

## 数据与隔离

| 位置 | 内容 | 消费者 |
| --- | --- | --- |
| `data/dev/inputs.json` | 12 个问题、来源原文、工作区、版本当前性快照 | Dev runner |
| `data/holdout/inputs.json` | 4 个不同项目的问题和原文 | 后续获授权的保留运行 |
| `gold/dev.json` / `gold/holdout.json` | 事实、原子关系、修订、拒答、理由、必要证据目录 | 离线 evaluator / 作者 / Reviewer |
| `fixtures.py` / `runner.py` | public 数据加载、只读工具、实际执行回执 | 运行进程 |
| `scorer.py` | 只读已落盘预测后评分 | 独立评分进程 |

Gold 的每项 `evidence` 为必要消息引用的集合；`evidence_catalog` 保存对应精确
`EvidenceSpan`（Unicode 范围、quote/hash、workspace、版本、来源）。缺少集合中的任一桥接/反证
均不给完整证据分。来源 catalog 和 Gold 均有指纹，预测绑定输入快照。

运行进程不导入 scorer、不加载 Gold，不向 adapter 提供任务列表、候选对、标签、理由或预期工具动作。
工具只按 public `doc_id` 读取固定资料，无路径访问接口。运行器不提供持久化、写入或网络工具。
这是受控离线适配接口，不是隔离任意 Python 代码的安全沙箱；文件回执用于本地审计，不是抗恶意篡改的签名证明。
Git checkout 对作者可见，所以此保留集应称作者可见保留集。保留集仅静态审阅，本轮不执行、不评分。

## 覆盖清单

| ID | 业务边界 |
| --- | --- |
| D01 | 直接任务 / 负责人 / 截止恢复 |
| D02 | 多文档发现、两个直接影响、一个未知影响 |
| D03 | 明确同任务，复述不等于修订 |
| D04 | 明确不同任务，同负责人不可合并 |
| D05 | 供应商变更直接影响 + 明确截止修订 |
| D06 | 同编号而日期不同，缺修订依据 |
| D07 | 项目归属与任务身份桥接缺失 |
| D08 | 同等来源的修订正反证冲突 |
| D09 | 可读旧版本失效、资料纠错不是业务修订 |
| D10 | 未审核来源“正式决定”的权威拒答 |
| D11 | 直接影响 / 明确无影响 / 未知的区分 |
| D12 | 明确范围修订，截止有据保持不变 |
| H01–H04 | 缺失字段、项目桥接、编号作用域、依赖与直接影响边界的保留组合 |

每个案例给定一套闭合合成资料。这里的发现 Recall 仅指给定检索范围内的 Gold 观察，
不能外推为真实项目完整影响清单。一个跨文件同一业务任务可有多个来源观察；发现分母按观察计算，
SAME_TASK 另外评分，不物理合并观察。
所有问题的 snapshot=current，tasks 分母仅含当前来源版本；D09 的可读历史 b1:1 在
`discovery_exclusions` 明示，不进入当前任务发现分母，但历史关系和失效修订必须单独识别并举证。

## 归一化输出契约

未来 adapter 接口：`adapter(request, tools) -> answer`，由 `execute` 调用。
`request` 只有 case_id、workspace_id、snapshot、question；tools 暴露资料目录和原文读取。
adapter 自行进行发现、抽取与关系分析，再把生产输出转换成以下评测专属结构：

```text
answer:
  tasks[]: ref, task, owner, project, deadline, evidence[]
  relations[]: predicate, subject, object, verdict, reason, evidence[]
  revisions[]: subject, object, status, old, new, delta, changed_fields, reason, evidence[]
  answer_status: COMPLETE | PARTIAL | ABSTAIN
  abstentions[]: 明确缺口 reason code
  answer_evidence[]: 支撑回答状态与拒答的必要来源
```

- `ref=doc_id:1-based消息序号` 只是适配层观察 locator，不是 M1 observation ID 或业务 task ID。
- `task` 是原文任务名称的精确摘录。`owner` / `project` 没依据填 null；派生项目关系不回写原观察字段。
- `deadline={value,precision,role,bound}`；精度沿用 M1，边界沿用 M2。没有完整年时 value=null。
- SAME_TASK / REVISES_PLAN 的方向是新/指向观察 → 旧/锚观察。AFFECTED_BY 的 object 是带类型的
  来源变更声明 `change:doc_id:消息序号`，与同消息任务 locator 明确区分；变更原文必须在必要证据中。
  IN_PROJECT / PROJECT_ALIAS 的项目名称来自原文。它们不是正式 DecisionRecord 引用。
- `verdict=SUPPORTED|REFUTED|UNKNOWN|CONFLICT|NOT_ASSESSABLE`。负面结论必须有反证；未知必须指出缺口。
- `revision.status=COMPUTABLE|BLOCKED|NOT_COMPARABLE|NOT_APPLICABLE`；old/new 为 `{date,bound}` 或 null。
  只有 COMPUTABLE 能有整数 delta；`0` 表示有据未变。不可比 / 阻断 / 不适用的 delta=null。
  changed_fields 为实际修订字段（本集 deadline 或 scope），不是根据日期差推测。
  所有状态都要求输出对应 SAME_TASK 与 REVISES_PLAN 判定并核验状态/原因/证据，包括阻断和不适用。
- `evidence[]={ref,span}`，span 使用工具返回的完整 EvidenceSpan，必须精确绑定原始消息。
- 不接受重复任务、关系或修订 key；额外发布的关系也评分，不仅遍历 Gold。

关系输出是本问题的回答投影，不是给所有来源构建完整图谱：显式项目填在 task.project；
IN_PROJECT 用于题目要求调查的缺失归属，PROJECT_ALIAS 用于明示别名桥接。
身份/修订只输出题目要求比较的观察，影响问题按发现的任务列 AFFECTED_BY，明确依赖列 RELATED_TO。
adapter 应省略与问题无关的额外图谱边。离线 Gold 是这一回答投影的封闭标注宇宙，
当前评分器不对未标注的自由扩展图谱边做开放世界语义裁判。

## 评分口径

所有比例带 numerator / denominator / value；零分母 value=null。只为实际 eligible 的预测计算业务指标。
数据集汇总按分子/分母加总，不平均案例百分比；`summary.by_scope` 将 SCRIPTED_WIRING_ONLY
与其他 adapter 输出分开，NOT_EVALUATED 不进入业务指标分母。评分器不调用模型；外来 adapter 记录的
模型执行只能标为 IMPORTED_UNVERIFIED，不将其自报内容当成已独立核验的模型调用。

| 维度 | 口径 |
| --- | --- |
| Task Discovery Recall | 有必要原文证据的 Gold 任务观察被恢复数 / 全部 Gold 任务观察；未召回仍在分母 |
| Task Discovery Precision | 有据恢复的 Gold 观察 / 输出观察；补充约束胡乱罗列任务 |
| Relation Precision / Recall | 正例要求 predicate、方向、端点、当前证据和必要 proof 完整；缺 proof 的正断言同时 FP+FN；micro 与各 predicate 分别报 |
| Task / Owner / Deadline / Project Accuracy | 字段精确匹配且必要证据有效 / Gold 观察数；缺失字段和缺任务不给分 |
| Evidence Groundedness | 结构化主张匹配标注且必要证据完整 / 所有输出结构化主张；另报原文读回有效率 |
| False Merge | SAME_TASK 发布错误：明确反证为 hard，未知/冲突/缺 proof 为 unsupported；计数及发布对比例 |
| False Revision | 无据发布修订或错误计算日期；按观察对去重，另报关系/比较子计数及发布对比例 |
| Revision Accuracy | 状态、旧新日期边界、delta、修订字段、原因和必要前提完整匹配 / 预声明修订问题数 |
| Relation State Accuracy | 非正关系 verdict + reason + 正反证完整匹配；UNKNOWN 不能替代 CONFLICT/REFUTED |
| Abstention Accuracy | 每题 answer_status、缺口集合及回答证据完整匹配；答得出的题乱拒答也扣分 |

复用 `linkloom.evaluation.metrics.binary_metrics`、`bad_cases.classify_link_case`、
`RawArtifact` / `EvidenceSpan.verify` / 现有原文 parser。现有 trajectory grader 的固定断言与 P2
固定候选评分不覆盖 M3 任务发现，本目录只新增必要的业务答案比较，不另建模型调度、LLM Judge、数据库或完整 Harness。
False Merge 按无序观察对去重；SAME_TASK 的错误方向仍在关系 Precision/Recall 扣分。
静态隔离核验同时覆盖项目名称和 workspace+artifact identity；局部 doc_id 如 a/b/c 可以在不同工作区复用。

失败阶段顺序：extraction → discovery → evidence → relation → revision → answer；同时保留全部命中阶段，
这只是确定性诊断标签，不声称已经证明模型内部根因。运行层另列 capability_gap / execution_contract /
agent_execution / output_contract。错误分类沿用 false_positive / false_negative 等既有 taxonomy。

## 执行状态与可观测性

预测包含 adapter_kind、输入指纹、target_action、target_invocations、工具 seq / arguments / result hash / status、
model_calls、input_tokens、output_tokens、cost_usd、wall_ms、failure_stage。未知成本保持 null，不能当零成本。
固定脚本 model_calls=0；未接入生产 adapter 时 model_calls=null。模型 token/计费来自未来 adapter 的真实遥测，
本轮不填估算值，不硬编码 Gemini 价格。

`score_run` 回读来源重放工具回执并检查引用确实通过 read_source 可见；只有目标调用及成功资料读取均有记录
才进入评分。无动作、回执/快照不匹配为 NOT_EVALUATED；adapter 抛错为 RUN_ERROR；输出不合契约为 CONTRACT_ERROR。
这三种状态单独报告，不包装成任务通过。缺接口的 NOT_RUN 不计任务失败。

本轮无真实模型调用、无保留集成绩、无生产 M3 端到端成绩。未来集成依赖：M2 自主候选发现接口、
M1 观察与生产 ID 的 locator 适配、P4 持久化快照/版本读取，以及实际执行轨迹和模型用量遥测。
本目录不实现或更改这些生产模块。
