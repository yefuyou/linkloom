# SPEC 草案：跨会议“可能是同一件事”提示

状态：待用户审阅；本轮仅方案与出题材料，不含功能实现。
日期：2026-11-03。
分支：`feat/same-task-hint`；基线：`aa20f2f6d5d1f9ad4a686cb8fdc3832a66202e0e`（本轮 fetch 后的 `origin/master`）。
本文是本功能方案的唯一入口；遵循本次工作约定存放于 `work/`，不在 `docs/` 新建文件。

## 1. 目标和范围

对当前工作区每条待确认候选，给出至多 3 个已有正式任务供人判断是否合并。语义提示补充现有精确同名提示；精确同名优先。点“选它”只进入现有合并预览，提交仍需原有人工审核。

处理链：授权与当前性校验 → 正式任务检索投影 → BM25 + 本地向量 + RRF → 前 10 本地 base INT8 重排 → 可选受约束选择题 → 分模式阈值 → 最多 3 条提示 → 现有合并预览。

不改变任务抽取、正式任务字段、合并/撤销算法、人工字段归属、审核规则、关系图和笔记库内容。不删除现有代码，不扩大本次远端改动范围。

## 2. 当前代码事实和复用点

| 现状 | 方案处理 |
|---|---|
| `ui/static/tasks.js` 的 `sameTitleHtml()` 在展开的候选处理区按 `title === title` 提示；`choose-same-task` 进入预览 | 保留精确相等的字面语义，增加语义卡片；独立处理新入口，不能仅把旧事件的标题相等检查删掉 |
| `tasks/models.py` 的正式 `Task` 和中性 `TaskCandidateInput` 没有独立 description/project 字段 | 检索描述由已授权原文投影产生，标题、负责人取当前值；不为本功能新增正式任务字段/数据库迁移 |
| `tasks/query.py`、`review.py` 提供当前审核、来源和字段归属投影；合并可能依赖多场会议 | 复用审核和来源版本验证，覆盖仍被当前字段依赖的全部会议，不拿全部历史原文作背景 |
| `retrieval_v2/retrievers.py` 的 `HybridRetriever` 使用每路前 20、RRF k=60，默认 Noop | 以 Noop 取得 RRF 前 10，再单独调用重排；避免先重排 20 后再截 10，也不改通用检索默认值 |
| PR #48 已在基线中：`rerank/local.py`、`pipeline.py` 有整批降级、有限分数验证、输入限长和超时 | 本功能注入显式 CPU/ONNX 的 base INT8；不使用可能走 CUDA/torch 的默认 auto 配置，也不读取远端 Jev 模式 |
| base INT8 输出原始 logit，RRF 是另一种分数 | 分模式校准展示阈值，不能直接当概率或共用阈值 |
| 现有任务读取可能因来源变化追加 `needs_review` 审核记录 | 保留此校验行为；提示自身不追加“同一任务”、合并或字段变更记录，不把读取笼统宣称为数据库零写入 |

PR #48 的开发记录 `benchmarks/rerank/2026-11-02/results.md` 只证明既有文档数据上的表现，不能代替本任务的质量与延迟评测。L6 材料导出先例为提交 `99d5c4af8` 中的 `work/graph-eval-seed.md`。

## 3. 授权、候选与检索语料

### 3.1 严格在召回前收窄

- 由宿主确定数据库，由请求显式提供工作区；沿用现有 workspace、来源 active/current、原文证据和人工审核校验。负责人只是检索字段，不是访问权限。
- 输入候选必须是当前版本、未被处理的 pending 候选，ID 与 candidate fingerprint 均匹配；历史、已拒绝、已合并和仅待复核的已审核候选不发起新语义匹配。
- 正式任务仅允许相同工作区，状态为 `open`、`in_progress`、`blocked`，当前 binding 为 `approved` 且当前字段来源仍可验证；排除 `done`、`cancelled`、无有效审核绑定、`needs_review`、`invalidated`。
- 在建立 BM25/向量索引前完成过滤。不能先全库检索再过滤，也不能把另一工作区、未授权或失效记录送给 embedder、重排器、选择模型或浏览器。
- 每个允许任务建立一个检索文档，以 task_id 去重；合并的多会议原文并入同一任务文档，不让一个任务占多个召回位。

### 3.2 检索文本和证据保持分离

检索文本使用固定字段次序：当前标题；描述原文；当前负责人；来源会议名称与明确的会议时间。查询候选采用相同结构；不根据负责人、截止日期或项目名硬性认定同一任务，负责人/截止变化仍可能是同一事项。

“描述原文”优先采用当前、可验证的 task observation description；适配器无该字段时采用与候选绑定的 EvidenceReference.quote。所有片段都必须落在已授权任务证据内，记录使用的回退来源；不调用模型补写描述，不从整个会议自由截取相邻事项。项目只使用片段本来明确写出的内容，不靠推测补齐。

检索文档是临时投影：IndexDocument 的 resource/evidence identity 唯一对应工作区+task_id+投影指纹，logical_path 是逻辑标识而非本机路径。原始引用独立保存 source_id、source_version_id、quote、quote_sha256、source_sha256、行/字符范围与 meeting_time。重排分数不成为证据。

人工改写的当前标题/负责人可检索和展示，但对应引文明确标作上下文，不宣称会议原文支持人工改写值。

### 3.3 索引和并发

- 宿主持有可关闭、容量有界的内存索引和本地模型对象；首次按工作区构建，此后按投影指纹增量更新。缓存以工作区和授权语料指纹隔离，不建立新的持久化内容库。
- 语料指纹涵盖当前任务值/状态、review revision、有效字段来源、候选 assignment，以及来源 active、当前版本和内容哈希；不以 TTL 代替重新验权。名称等检索字段变化也使投影失效。
- 在短事务中取得已验证的不可变快照，释放事务/数据库锁后运行 embedding、重排与模型。结果返回前另开短事务检查输入候选及所用语料指纹；变化则丢弃结果，不复活旧授权。
- 模型发送前再次检查来源/权限；缓存命中也须重新验权。外发后发生的撤权不能撤回已发送内容，立即使结果失效并停止后续请求。
- 前端先显示原有候选卡片，再用有界队列逐张补充提示，展开卡片优先；本地重排并发初值为 1，避免共享 CPU worker 排队拖垮合并接口。切工作区、翻页、刷新或候选变更时取消/忽略旧响应。

## 4. 召回、重排和降级

1. 复用 BM25Index、VectorIndex、现有本地 multilingual MiniLM embedder、BM25Retriever、DenseRetriever、HybridRetriever。每路仍前 20，RRF k=60；此功能用 Noop 运行融合，最终只取 RRF 前 10 个唯一 task_id。
2. 将这同一组 10 个任务及其固定检索文本包装为 RerankCandidate，注入 `LocalCrossEncoder(model_name=BASE_MODEL, device=cpu, engine=onnx)`，经 `RerankPipeline(mode=local, local=...)` 评分排序。
3. 锁定 PR #48 的 base 模型 revision、ONNX INT8 制品校验和及现有输入预算；重排仍使用其 256 token pair 上限、截断记录和有限分数校验。每批最多 10，不修改 PR #48 的通用上限 20。
4. 缺少依赖/制品、准备中、超时、进程异常、输出数量或 ID 不符、NaN/Inf 时，整批回退到原 RRF 顺序，不混用一部分重排结果，不转远端重排。保留 safe reason 和各阶段耗时。
5. 请求处理只使用已准备的本地模型/制品，准备阶段与在线延迟分开。缺少制品时不在卡片请求中下载/量化大模型。沿用本地重排 5 秒硬超时；暂定暖机后每候选本地完整提示 p95 ≤3 秒作为待测目标，冷启动单列，不把 PR #48 的文档延迟当作本功能结论。
6. 本地向量不可用时可保持同名提示，语义部分标记 unavailable；不把 BM25-only 冒充混合 RRF，也不套用 RRF 阈值。BM25-only 产品降级不纳入此次范围。

| 模式 | 顺序 | 展示门槛 |
|---|---|---|
| 精确同名 | 严格字符串相等；先于语义结果 | 不用语义阈值，但仍须满足工作区、未完成和当前授权条件 |
| base INT8 正常 | rerank score 降序，平分按 RRF/id 稳定排序 | 独立开发集确定的 local 阈值 |
| 重排不可用 | 完整原 RRF 前 10 顺序 | 独立开发集确定的 RRF 阈值 |
| 某模式尚无冻结阈值/指纹不匹配 | 可计算排序供开发评测 | 不上线该模式的语义提示；精确同名继续可用 |

阈值针对当前模型 revision、投影版本和检索参数，不移植 PR #48 的检索分数。原始分数/阈值仅留诊断，用户界面不显示“同一任务概率”。

## 5. 可选的模型选择题

模型判定是可插拔步骤，不是召回/重排的必要条件。未配置模型、未有当前外发授权、预算不足或选择能力关闭时，直接使用通过相应阈值的本地结果，不读凭据触发默认付费调用。

### 输入

- 从重排（或 RRF 降级）前 10 中取前 min(5, n) 个作为固定选项；n 少于 3 时不调用模型，直接用本地结果。
- 候选及各选项只带已授权的当前字段、来源说明和有界原文片段；不带 gold、阈值调参结果、完整会议或别的候选。
- 选项由宿主编码 C1…C5，加 NONE（都不是）；输出枚举随本次选项固定。温度 0（若支持），最多一次请求，不做模型自我重试或工具调用。记录候选次序以复现位置偏差。
- 复用现有 ModelTurnRequest/structured tool 契约、模型适配器和 ProviderRequestAuthorization 的完整请求哈希/预算 reservation 校验；不要借用不匹配的旧抽取授权。配置模型本身不授予新来源或新目的的外发权限。

### 输出与逐字校验草图

输出字段：`choice=C1..Cn|NONE`、`compared_choice=C1..Cn`、`rationale` 一句、`candidate_quote` 和 `task_quote`。choice 非 NONE 时 compared_choice 必须相同；NONE 时 compared_choice 明确对应说明中被比较的那个任务。

每侧 quote 含 evidence_id、start、end、text。宿主逐项检查：选项在允许列表；引文归属正确的一侧与来源版本；范围合法；原文 `[start:end] == text`（逐字、区分大小写、不 trim、不 Unicode/空白归一化）；引用 hash 与快照一致；两侧均非空。rationale 限长、限制为一句且不允许另加未经校验的引文。

最终理由由宿主组合为：`候选原文「{校验通过的候选片段}」，已有任务原文「{校验通过的任务片段}」；{一句解释}`，再按纯文本转义显示。引用准确只表示理由有出处，不证明两项确为同一任务。

有效选择仅展示被选且通过对应展示阈值的 1 项；有效 NONE 不展示语义项。选中第 4/5 项也只能将该项提升到可见位置，不能把未通过阈值的项放行。精确同名列表始终独立保留最高优先级。

超时、越界选项、结构不符、缺任一侧引文、错引用、引用位置错误、授权变化：判定结果作废。权限变化时丢弃整体结果；其他可恢复故障退回本地最多 3 项，理由明确为本地排序依据，不复用失败的模型理由。

本轮不调用任何付费模型。后续若仍无付费运行授权，模型语义准确率/真实费用记为未评测；Mock 只验证协议，不冒充真实模型质量。选择请求初始超时上限建议 8 秒；每候选最多一次，整页/整集共享宿主预算，禁止每卡重新取得一份全额预算。一次运行须有请求/输入输出 token/费用和墙钟上限，价格未知或计费状态不明时禁止启动选择请求，不能将未知费用写成 0。

## 6. 页面行为

- 待确认候选卡片上渐进展示列表，最多 3 个唯一任务；详细理由和两侧原文在现有展开区可见。精确同名在前，语义项去重后补满剩余名额。
- 精确同名从完整授权任务池中取得，不受 RRF 前 10 截断；已占满 3 个名额时不再发起无展示价值的选择模型请求。
- 精确同名仍写“已有同名任务：{标题}（{负责人} · {截止}）”；语义项写“可能是同一件事：{标题}（{负责人} · {截止}）”。缺失负责人/截止沿用现有空值文案，不补日期。
- 本地理由使用事实模板：“标题与任务原文的本地检索/重排结果相近，请核对两边原文”；注明本地结果来源。只使用有映射的实际原文片段，不让重排器编造解释。
- 两边引用分别标记“本次候选原文”“已有任务原文”，带来源会议、时间与版本；多来源任务可展开查看其有效字段来源。默认摘要有界，展开保持逐字原文。
- 点击“选它”仅设置 merge target，复用当前差异字段选项、review reason 与 merge-preview。仍需原有 reviewer、fingerprint、review revision、preview token 才能提交；模型理由不自动当成人工确认理由。
- 新语义按钮校验的是当前响应中该工作区/候选指纹/语料快照下的 task_id，不能接受任意 DOM dataset 注入目标。随后始终以服务端现有合并预览重新验证。
- 已处理、历史、待复核卡片不显示可操作新语义提示；低于阈值无提示，加载/失败不阻塞“作为新任务/拒绝/手动合并”。模型结果不得改动用户已经打开的合并预览/目标；用户操作后丢弃迟到的选择响应。
- 超过 3 个精确同名时，只占前 3 位，按当前任务更新时间降序、task_id 稳定排序；保留现有手动合并完整目标入口。

跨项目同名在同一工作区仍可能触发字面同名提示，这是保留该规则的结果，不能包装成已确认同一事项；显示原文项目上下文。评测必须把这类提示的错误暴露出来，既报端到端错误提示率，也按精确同名/新增语义项分别统计，不能仅报新增部分而掩盖原规则的误提示。

## 7. 接口草图（非实现）

内部契约：

- `AuthorizedTaskHintSnapshot`：工作区、候选 ID/fingerprint、语料 fingerprint、当前可用任务投影、版本绑定引用。
- `SameTaskHintService.get_hints(workspace_id, candidate_id, candidate_fingerprint)`：只做本地检索/重排/阈值，返回排序和展示结果；不合并。
- `SameTaskChoiceJudge.choose(snapshot, options, request_authorization)`：只返回通过 schema/引文校验的选择或失败原因；不访问任务存储、不提供写工具。
- `SameTaskHintPolicy`：最多 10 召回/3 展示/5 选择选项、local 与 RRF 的各自阈值及版本、截止时间、缓存容量；未冻结阈值为 disabled。

HTTP 建议增加两条现有命名空间下的独立接口：

1. `GET /api/product/task-candidates/{id}/same-task-hints?workspace_id=…&candidate_fingerprint=…`。始终本地；不因 GET 产生付费选择调用。
2. `POST /api/product/task-candidates/{id}/same-task-choice?workspace_id=…`。body 仅接受 `expected_candidate_fingerprint`、`expected_hint_snapshot`；宿主重新获取选项并校验授权/预算，客户端不能提交自编 task 列表、原文、模型名或凭据。前端仅在该用途已配置并已授权时调用。

共同响应形状：schema_version、candidate_id、candidate_fingerprint、hint_snapshot、retrieval_mode、judge_status、hints[]、safe diagnostics。每个 hint 含 task_id、当前 title/owner/deadline/deadline_text、match_kind、rank、reason.kind/text、candidate_excerpt、task_excerpt、task_review_revision。展示对象含双侧引用；开发 trace 可另留 RRF rank/score、rerank score/耗时，但不返回未授权内容。

错误沿用 `INVALID_REQUEST`、`RESOURCE_NOT_FOUND`、`CANDIDATE_CHANGED`、`SOURCE_INVALID` 等既有 HTTP 规则；新 hint snapshot 变化用 409 `HINT_STALE`，不自动重试付费调用。无结果为 200+空 hints；排序不可用不伪造结果。来源授权变化必须返回冲突或空结果，不能带旧引文。

模型未启用时前端不发 POST，服务端也不构造远端客户端。新增接口保留 Host/Origin、JSON 大小、参数白名单、重复 query 参数、错误内容脱敏和 unsupported method 断言。现有 review/merge/undo 请求与响应不修改。

## 8. 评测材料、独立出题和阈值

### 本轮先交付

`work/same-task-seed.md` 从 4 份已跟踪、既有合成开发输入导出全部 sources（31 组、58 份来源条目，允许重复），附基线、文件 SHA-256、Git blob ID、每份原文 SHA-256。只保留来源输入，不导入旧 questions/queries/expected/关系判定，不运行抽取器或本功能模型。

沿用 L6：实现前导出并推送指定分支 → 实现会话之外的人起草 → 用户逐条确认预期 → 标记“外部起草，人工确认”并冻结。当前会话与当前会话的子 Agent 都不代写新题/新标签。已有 fixture 的模拟审核也不称为真实人工审核。

### 数据集最低要求

- 至少 30 个被确认的主判定对，建议落实为至少 30 个独立候选场景；建议起始配额：12 个同一事项不同说法（改名/简称/口语化各至少 4）、10 个相似但不同事项、8 个跨项目同名。外部出题人可扩充，不删难例凑分。
- 每场景指定候选来源和同工作区正式任务池（至少 10 个任务，除专测小池的边界用例），标明每个池任务的状态/模拟审核来源和唯一或允许的正确 task_id 集合；无匹配用 NONE。仅 30 个孤立二元对不能充分测 top-3 召回。
- 不把不同原工作区直接合并成真实同一工作区；若为检索构建新合成工作区，出题人明确编制其来源、项目和任务池，由用户确认。
- 需补齐材料较弱的自然口语/简称、跨项目同名及足够干扰项。外部作者可新增合成会议文本，并给逐字引文；新增内容连同题目一起审核冻结，不能由实现会话改写 gold。
- 正例跨会议，包含负责人/截止发生变化；负例覆盖同负责人/同日期/同对象不同交付、同名不同项目、条件/否定和信息不足。无匹配与确有多个合理目标必须有明确标注规则。
- 每题记录 author、reviewer、确认状态、两侧来源与原文定位、任务池、预期 target/NONE、不确定性说明。只接受确认过的题；没有确认不进入调参。

### 对照与指标

A=同一授权任务池上的现有精确同名；B=同一授权任务池上的混合 RRF；C=B 的固定前 10+base INT8；D=C+可选模型选择。B/C/D 复用完全相同的候选、池、文本快照和召回结果，不向模型输入标签。

- Recall@10（诊断）：正确目标进入 RRF 前 10 的正例数 / 所有有正确目标的正例数。
- Recall@3（主要）：正确目标进入前 3 的正例数 / 所有正例数；分 raw 排序 top-3 与阈值后实际展示 top-3 两项，避免阈值隐藏漏召回。
- 模型选择正确率：条件准确率（正确目标已在选项中或 gold 为 NONE）和端到端准确率都报；NONE 选对才算对，正例选 NONE 算错。协议/引用校验失败按端到端失败计，另报 fallback 与 coverage。
- 错误提示率：错误 task 提示条数 / 实际展示 task 提示总条数；另报负例场景中出现任一错误提示的比例，以及有提示的场景比例。精确同名、语义和整体分别统计，不能用“从不展示”获得虚假的合格结论。
- 延迟：p50/p95 的授权/投影、索引构建/增量、query embedding、BM25、dense、RRF、重排、选择及端到端耗时；冷启动、暖机、整页逐卡完成时间分别报。并发测试确认合并/撤销不会被推理持锁阻塞。
- 费用：各模式请求数、输入/输出 token、实际费用/可证明上界、每候选/整集费用与重试/失败费用；本地无付费调用的 API 费用为 0，仍报告 CPU 时间，不将其等同为零资源成本。

阈值仅在外部起草且用户确认的开发集确定；local/RRF 分开扫描，原始标签不变。选阈值时同时检查召回与错误率，公开所有失败例/模式/覆盖率，不只挑最佳子集。建议评审目标为展示 Recall@3 ≥90%、新增语义错误提示率 ≤5%，但这是待审目标，不是已测结果或已定展示阈值；小样本同时列分子/分母。若无法兼顾，不开启不合格模式。

冻结投影版本、模型/INT8 制品、选择 prompt/schema、阈值和评测脚本版本。开发集结果仅作开发结论，不能称独立 held-out 验收；后来另出的验收题不得用于回调阈值。真实付费判定评测需后续单独授权，本方案审阅不默认授权该费用。

## 9. 预计文件清单

本轮仅新增并提交 `work/same-task-plan.md`、`work/same-task-seed.md`；由于 `work/` 默认忽略，只精确强制添加这两项，不修改 .gitignore，也不添加其他 work 内容。

实施阶段预计范围（审阅通过后才改）：

| 文件 | 职责 |
|---|---|
| 新 `src/linkloom/tasks/same_task_hints.py` | 业务编排、授权快照、结果校验和缓存/生命周期 |
| 新 `src/linkloom/tasks/same_task_retrieval.py` | 任务原文投影、现有索引/混合检索适配、前 10 重排 |
| 新 `src/linkloom/tasks/same_task_choice.py` | 可选选择题 schema、模型适配、双侧原文逐字校验 |
| 新 `src/linkloom/tasks/same_task_policy.py` | 模式、容量/时间限制和冻结阈值的显式配置 |
| `src/linkloom/tasks/query.py` | 暴露有界授权任务/证据投影，复用既有验证；不改正式字段/审核规则 |
| `src/linkloom/ui/task_api.py` | 两个新接口、参数/错误/依赖注入；持有和关闭 hint 服务 |
| `src/linkloom/ui/server.py`、`src/linkloom/ui/__main__.py` | 从既有宿主配置组合可选 provider/请求 guard/预算，关闭资源；无模型配置则纯本地 |
| `src/linkloom/ui/static/tasks.js`、`tasks.css` | 卡片渐进加载、双侧原文、最多 3 个和预览入口 |
| 新 `tests/unit/test_same_task_hints.py`、`test_same_task_choice.py` | 授权与生命周期/阈值/降级/引用协议，使用独立合成用例 |
| 新 `tests/integration/test_same_task_hint_http.py` | HTTP 权限、参数、防泄漏、并发及合并预览衔接 |
| `tests/ui/tasks_dom_test.cjs`、`tests/ui/tasks_browser.cjs` | 保留原流程并补充真实可见卡片/预览交互 |
| 新 `scripts/eval_same_task_hints.py` | 固定池重放、模式对照、阈值扫描、完整指标与费用输出 |
| 新 `tests/unit/test_eval_same_task_hints.py` | 手算样例验证指标分母/空集/错误提示与费用状态，不测试实现细节 |
| 新 `tests/fixtures/same_task_hints/dev.json` | 仅接收外部起草、人工确认的冻结题与标签 |

复用 `retrieval_v2/`、`indexing/`、`rerank/`、现有 Model Adapter 与授权契约，预计不改它们的通用行为。`tasks/merge.py`、`merge_storage.py`、正式 task schema、现有安全断言均不调整。

## 10. 测试计划和验收点

以下均是实施后计划，不是本轮已执行的功能测试。

1. 检索与展示：四字段参与、每任务一文档、RRF 先取 10 再重排、真实 top-3、稳定平分、空池/小池、阈值上下界、3 项上限、精确同名优先和去重、项目干扰不作 identity 结论。
2. 权限：另一工作区/不活动来源/旧版本/未审核/已完成/已取消/已复核失效任务从索引输入处就被排除；spy 验证其文本从未进入 embed/rerank/provider/响应。输入候选旧 fingerprint、已处理/历史变体不能匹配。
3. 重排：成功保持全部 ID/原文；依赖缺失、制品准备中、超时、NaN/Inf、重复或陌生 ID、半批失败均整批保持 RRF；全局 Jev 环境与 key 已存在仍不能触发远端重排。
4. 选择：无配置零远端请求；配置但无授权/预算零请求；合法选第 1/4/5、NONE、缺双侧引用、引用串边、错源/错版本、空白/大小写伪匹配、越界、注入指令、超时、价格未知、预算耗尽和费用状态不明。失效选择不能产生模型理由或写动作。
5. 生命周期：候选/任务/来源在推理期间变化、source active A→B→A、merge/undo/status/manual edit、同 ID 换版本、工作区切换/翻页迟到结果、缓存命中撤权，均须重新校验；重新变相同不能恢复已失效审核。
6. HTTP：保留已有 Host/Origin、参数白名单、JSON/方法/错误保护；新增 route 同样隔离。老十条任务路由的“无模型”断言保留，新的可选选择调用只发生在新路由。
7. UI：两侧原文完整可见、XSS 转义、空值文案、≤3、低分无提示、同名/语义选择都只进入预览；字段勾选、人工理由、过期 preview 冲突、合并和逆序撤销原流程完整通过。语义加载慢/失败时旧操作可用。
8. 评测：独立出题来源与人工确认可查；至少 30 主判定对，题池和数据 hash 固定；指标分母/失败归因正确，local/RRF 独立阈值；未运行真实模型不报真实准确率。

拟运行：

- `python -m pytest -q tests/unit/test_same_task_hints.py tests/unit/test_same_task_choice.py tests/unit/test_eval_same_task_hints.py tests/integration/test_same_task_hint_http.py`
- `python -m pytest -q tests/unit/test_task_merge.py tests/unit/test_task_review.py tests/unit/test_task_rebind.py tests/unit/test_task_offline_permissions.py tests/unit/test_rerank.py tests/unit/test_rerank_local.py tests/unit/test_rerank_failover.py tests/integration/test_task_entity_http.py tests/integration/test_tasks_page_assets.py tests/integration/test_retrieval_rerank.py`
- `node tests/ui/tasks_dom_test.cjs`；`node tests/ui/tasks_browser.cjs work/same-task-browser-<唯一标识>`，只用新建合成数据库。
- 全量离线必需检查按当时主线约定执行；不默认启用 smoke/live/付费模型，不安装或下载无关依赖。
- 真实 base INT8 开发评测只在本地依赖/制品就绪、标签确认后运行，命令和输出目录由评测脚本公开；当前不运行任何开发集推理。

## 11. 工作量和顺序

| 步骤 | 预计投入 | 前置 |
|---|---:|---|
| 本轮方案、既有材料导出、精确提交推送 | 1 小时内 | 本次授权；写好即停 |
| 外部出题、补充自然表达/任务池、用户确认与冻结 | 0.5–1.5 人日，另加用户审核等待 | 材料可读；实施会话不代写 |
| 授权投影、索引复用、前 10 本地重排/降级和缓存 | 1.5–2 人日 | 用户通过方案 |
| HTTP/卡片/现有预览衔接 | 1–1.5 人日 | 本地结果契约稳定 |
| 可选选择题/授权预算/双侧引用校验 | 0.5–1 人日 | provider 组合点与配置确认 |
| 离线测试、浏览器验收、开发集评测/阈值冻结 | 1–1.5 人日 | 外部确认题集；本地制品就绪 |

实现与验证合计约 4–6 人日，外部出题单列；付费模型真实评测在获得单独授权后另计。主要不确定性是现有任务缺独立描述字段、多会议字段来源验证的成本以及 CPU 暖机延迟；先在授权投影与真实本地 10 项重排上验证，再接可选判定。

本轮只交付待审方案与原文材料并推送分支。没有选择展示阈值、没有新增题目/标签、没有实施或功能验收。下一步为用户审核本方案，并由本会话之外的人据 seed 起草题目。
