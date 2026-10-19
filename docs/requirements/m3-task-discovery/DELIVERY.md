# M3-F delivery

独立 Worktree：`work/linkloom-m3-task-discovery`；基线
`31dde91f0e9b6015abc5754e995692a2f1db89ec`；分支
`codex/linkloom-m3-task-discovery`。用户的 M3-F 请求是本次授权边界，详见 SPEC.md。

## 已实现

新增独立 `semantic_ingestion/task_observation_discovery.py`：Host 授权原文与
current_source 回读 → canonical SourceSegment / 有界全文窗口 → **现有**
ProviderNeutralSemanticExtractor → SemanticExtractionResult / TaskProposal →
build_task_observation → TaskObservation.verify 与登记版本/hash复核 → 清单。
没有复制或新增 LLM Extractor，没有接入或修改 M3 Runtime、ContextAssembler、
semantic_context / semantic_analysis、P1–P4、旧 m3_task_impact 数据/Gold/评分器。

Host 必须提供 expected workspace、不可变 RawArtifact、只读 current_source loader、
SourceReferenceRegistry（canonical 段的 evidence_ref + content_hash + source_version）。
服务不自动发现权限、不自行登记来源、不持久化或修改资料。当前版本/登记信息在调用前、
调用后与批量返回前核对。旧版本不能回读、撤权、跨 Workspace 与缺登记均关闭该来源。

默认每批最多 8 份原文、每份 8,000 字符 / 16,000 UTF-8 bytes、最多 3 次调用，
每次仅一次 attempt。超过输入上限不截断，超过调用预算明确列为 UNPROCESSED。
调用预算按 discover 批次；跨批重试/总预算归 Host，独立诊断器另有全程三次硬上限。

输出分别提供 valid_observations、partial_observations、failures、unprocessed_sources、
sources、groups、extractions / receipts。缺字段和缺年份保留 unknown 与 M1 issue；
不把未抽取当作 build_task_observation(None) 的成功观察。所有观察均为
SUPPORTING_ONLY / UNCONFIRMED。同负责人、日期、项目不会造成合并。

跨消息模型提议会投影回每一条 canonical 消息，再分别执行原有 M1 验证；
模型 group 仅保留显式提议的关联，不能当成业务任务身份，不跨消息补写字段。
完整原文句子上的保守 veto 防止裁剪后的建议、假设、引用、否定、取消文本被接受。
这是一层拒绝保护，不是通用语义判定器；边缘措辞可能漏判或保守拒绝，需要后续独立评估。

覆盖策略 `bounded-document-single-pass/v1` 在调用前固定：整个有界来源一轮检查。
coverage_complete 仅指该策略执行完成；semantic_exhaustive 永远为 false。
空输出为 NO_TASKS_DETECTED，不承诺来源没有任务。解析不完整、调用/验证失败、
达到 20 claim 上限或来源未处理时，不宣称该策略完整。

## 新公开合成真实诊断

只使用 `tests/fixtures/task_discovery/sources.json` 的三份全新资料。
标准答案在 `labels.json`，执行路径不读取标准答案、不提供任务端点；预测落盘后另行评分。
使用现有 Gemini 3.8 Flash Adapter，**恰好 3 次生成，无重试**，不使用 Gemini 2.5。
原请求、完整中性响应、原始用量、模型版本与实现/输入指纹见 evidence/live/。

| 指标 | 结果 | 分母与限制 |
|---|---:|---|
| 严格任务发现 Recall | 80% | 4/5；同一任务的全部 work 短语必须出现 |
| 严格任务发现 Precision | 80% | 4/5；描述不完整的预测严格算 unmatched |
| 匹配任务字段准确率 | 100% | 24/24；owner/project/vendor/deadline/原日期/precision 各 4/4 |
| 观察证据有效率 | 100% | 5/5；版本、来源段与字段位置实际回验 |
| 调用用量 | 6,276 tokens | input 4,453，output 1,823；thoughts 未提供，保留 null |

最终详细结果见 `evidence/reviewed-metrics.json`。没有旧保留集运行，也没有对旧资料调参。
字段准确率只针对匹配任务，不能掩盖漏任务；FakeModel 不计入语义质量成绩。
未查询当前价格，不报告真实费用；收据 .05 USD 是每次准入估计，不是账单。

实测缺口：五条会议消息里模型漏掉第二条“补齐回归脚本”的延续描述。
因此虽然输出了两个合理任务头部，严格完整任务匹配只有一个成功。
服务支持跨消息提议的验证与部分输出（FakeModel 契约测试），本轮真实模型没有完整提出它。
未追加第四次请求，未改变标准答案或原抽取器来消除这个失败。

## 五条原始会议消息 → 自动结果

这是实际提供给模型的原文，不预先给任务清单或端点：

```text
[2029-06-01T09:00:00Z] 林枫: 我负责青竹项目的索引迁移，供应商：云桥；截止日期：2029-06-20。
[2029-06-01T09:01:00Z] 周岚: 索引迁移还包括补齐回归脚本。
[2029-06-01T09:02:00Z] 周岚: 项目：青竹项目；负责人：周岚；提交容量报告；截止日期：6月22日。
[2029-06-01T09:03:00Z] 林枫: 资料纠错：上次文档把缓存大小写成32GB，实际是64GB。
[2029-06-01T09:04:00Z] 陈杉: 建议以后做界面换肤；任务取消：整理旧截图，今天不再安排。
```

实际自动提取并经 M1 验证：

| description | owner | project | vendor | deadline / 原文 | 状态 |
|---|---|---|---|---|---|
| 索引迁移 | 林枫（第一人称 + 原始 speaker 证据） | 青竹项目 | 云桥 | 2029-06-20 | VALID |
| 提交容量报告 | 周岚 | 青竹项目 | unknown | unknown / 6月22日，MONTH_DAY | PARTIAL |

第二条补充描述没有出现在提议中，作为实测失败记录；第三条的缺年份不补成 2029。
第四条是资料纠错，第五条的建议/取消内容均未形成任务观察。
原始响应及每个字段的精确 char offsets 见 `evidence/live/five-messages.json`。

## 复现与验证

在该 Worktree 根目录（PowerShell）：

```powershell
$env:PYTHONPATH='src'
python -m pytest tests/unit/test_task_observation_discovery.py tests/unit/test_task_discovery_acceptance.py -q
python tests/task_discovery_acceptance.py --replay docs/requirements/m3-task-discovery/evidence/live --output work/m3f-replay-new
python tests/task_discovery_acceptance.py --score docs/requirements/m3-task-discovery/evidence/live/predictions.json --output work/m3f-metrics-new.json
```

输出路径须不存在。REPLAY 不调用 Provider；它逐项核对原请求，再将记录的 ModelResponse
送回同一现有抽取器与发现服务。本次 replay 的观察、分组、来源、失败和覆盖结果全部相同；
latency 等操作字段不要求字节相同。原 live manifest 保留执行时指纹；评分器之后增加了
按字段计数与 NOT_RUN 分母测试，没有更改原预测、标签或模型请求。

不建议为复现重新执行 live；三次授权已用完。新的网络运行需要新的用户授权。

全量 `python -m pytest tests/unit tests/integration -q` 在收集阶段出现 4 个基线错误：
三个 memoryagentbench 测试依赖缺失 `scripts/run_memoryagentbench_g3_pilot.py`，
另一个依赖缺失 `scripts/run_stage_c_context_memory_loop.py`。
两文件在指定基线的 Git tree 中均不存在，原始记录见 evidence/regression.xml。
本任务没有修复旁支，也不宣称全量通过。当前系统还存在 pytest-asyncio 默认配置警告和
pyreadline3 退出时析构 OSError，与测试断言结果分别记录。

最终相关回归 **385 passed**（task / semantic / source_identity），见
`evidence/accepted-regression.xml`；之后强化评分器的错误合并判定，评分测试 **3 passed**，
见 `evidence/scoring-final.xml`。独立 Reviewer 的九个反例先出现 9 failures，
修复后已包含在上述 385 通过项中。来源中的指代取消也增加了中英文拒绝测试。
普通讨论、未采纳、可能、提议、假如、据说、无须、终止的检查仅用于拒绝，
边界仍是保守语法而不是通用语义证明。无法定位的指代取消会保守扣留整个当前来源的提议。

扩大到旧 Provider Adapter 测试时还遇到基线模型 ID 不匹配（旧测试请求 Gemini 2.5，
Adapter 要求 3.8）；该测试与 Adapter 文件在本分支相对基线完全相同。
原始失败见 `evidence/related-first-failure.xml`。首次宽回归使用默认临时目录遇到环境问题，
已中断并改用新建的 work/ 临时目录。`discovery-final.xml` 是缺少临时目录父目录时的
中间失败记录；`discovery-verified-final.xml`、`accepted-regression.xml` 是后续通过证据。
`semantic-regression.xml` 记录了 Worker 指代取消测试在修复前的两个预期 RED，
不是最终回归结论。中间证据全部保留；原始 XML 失败栈保留 pytest 输出中的行尾空白，
不改写历史记录。代码、测试、Markdown 与 JSON 的暂存 diff 空白检查另行通过。

最终重放见 `evidence/reviewed-replay/`，原始预测、源文件与标签保持不变；
生成/重放 manifest 分别保留当时与最终实现的指纹。最终评分新增了错误合并与冲突字段的
严格处理，真实分数仍相同。独立只读审查与完整检查目录见 REVIEW.md 及 evidence/checks.json。
没有真实 Vault 操作、安装/部署、CI 或 UI 验收声明。按授权完成精确提交和正常 Push；
不合并，不创建 PR。

## 架构检查与后续边界

新模块只负责 Host admission、调度、投影与结果清单；语义理解仍在现有模型边界，
字段角色仍由现有 M1 验证器负责。没有新存储、依赖、runtime hook 或权限能力。
后续应使用独立新资料验证跨消息完整描述召回，并由另一项任务决定 Runtime 接入。
本次不扩大范围接线。
