# M2-P2 跨文档项目绑定与直接影响关系

2026-10-18。本轮范围为用户确认的 P2：固定候选端点、有限原文证据提案、离线确定性校验。没有自动候选发现、计划修订、日期比较、关系侧库、UI 或 Agent 接线。真实 Provider 调用为 0，H01–H04 未执行。

## 文件与真实调用链

| 本轮文件 | 职责 |
| --- | --- |
| `src/linkloom/semantic_ingestion/task_relation_models.py` | 扩展现有模型：ProjectRef、区分 SOURCE_CLAIM / REVIEWED_DECISION 的 ChangeRef、可无观察的来源依赖、P2 predicate/detail/premise/authority 元数据。P1 v1 JSON/ID 保持兼容；P2 使用 v2 序列化。 |
| `task_relation_validation.py`（同目录） | 从 P1 提取共享观察/来源校验；新增精确提案、完整语句上下文、合法 EvidenceSpan 检查。P1 判定规则未扩展。 |
| `task_relation_context.py` | 四类 P2 判定入口；项目原文、标题范围、显式别名、跨文档项目登记桥；重验别名前提和当前性。 |
| `task_work_proofs.py` | 唯一文件/任务锚点、有限工作联系及具体供应商变更的直接因果证明。 |
| `task_change_authority.py` | 只读核验 DecisionRecord、替代链、immutable candidate、真实 HUMAN_APPROVAL 回执/事件及来源依赖。 |
| `tests/unit/test_task_relations_p2.py`、`test_task_change_authority.py` | 五类 verdict、独立项目/人物变体、条件/引用/冲突、版本与权限边界、幂等及序列化。 |
| `tests/task_relations_support.py`、`task_change_support.py`、`tests/integration/test_task_relations_p2_dev.py` | 公开输入构建、全新临时库审核、先预测后读取独立 Gold。 |
| `tests/fixtures/task_relations_p2/` | 独立物化的 Dev 来源与 Gold；不覆盖 P1 或旧冻结集。 |
| `scripts/evaluate_task_relations_p2.py`、`run_relation_checks.py` | 可复现导出/评分及隔离测试入口。 |

首次发布另外携带本地已有 M1/P1 的必要源码、测试与交付文档，及它们依赖的普通文档适配、中文供应商替代识别。具体见本提交文件清单；这些前置能力不计作本轮新开发。未包含既有 UI、Runtime、Provider、RAG、产品路线图等其他未提交修改。

调用链：公开 `inputs/dev.json` → `RawArtifact / parse_source_text` → 既有 `TaskProposal / build_task_observation / verify` → registry 当前快照 → `assess_relation(predicate, subject, object, artifacts, source_registry, evidence, ...)` → 共享 M1/P1 来源核验 → 关系专用证明 → `RelationAssessment.to_dict()`。SAME_TASK 仍调用 `assess_same_task`。生产调用方也可从现有 `TaskObservationQuery.list(...).observation` 传入观察；本轮没有自动接入查询或摄取流程。

`source_context_quote` 仅构造额外的整文 EvidenceSpan，调用方须显式登记和验证，用于 M1 发言 segment 外的标题。D08 混排文本的既有 parser 状态是 PARTIAL；未修改 parser、观察或历史 segment。正式权限核验复用 materializer 的只读 canonical adapter `_load_candidate` / `_receipt_from_row`，没有调用其写入方法；后续内部接口变更须跑权限契约回归。

## Dev 实际结果

逐题结果、原始观察、原文、版本/hash/字符范围、正反证及依赖见 [evidence/metrics.json](evidence/metrics.json) 与 [D03](evidence/D03.json)、[D06](evidence/D06.json)、[D07](evidence/D07.json)、[D08](evidence/D08.json)。以下分母都是固定候选原子问题，不能视为全项目任务召回率。

| 关系 | TP / FP / FN | Precision | Recall |
| --- | --- | --- | --- |
| IN_PROJECT | 4 / 0 / 0 | 4/4 | 4/4 |
| PROJECT_ALIAS | 2 / 0 / 0 | 2/2 | 2/2 |
| RELATED_TO | 1 / 0 / 0 | 1/1 | 1/1 |
| AFFECTED_BY | 2 / 0 / 0 | 2/2 | 2/2 |

20/20 题符合 Gold（18 个 P2 题、2 个 P1 身份边界题）；UNKNOWN 6/6、REFUTED 3/3、NOT_ASSESSABLE 2/2。CONFLICT 在聚焦单元测试覆盖，预声明 Dev 没有该类题。两条 SAME_TASK 均为 UNKNOWN，本子集没有身份正例，Precision/Recall 分母为 0，不标成 100%；P1 六题和 55 项原测试另行回归。

实际输出的证据 quote 24/24、依赖 span 62/62 成功读回。两条非法题被拒绝，其中 V04 的旧句在当前来源中不存在，输入提案读回失败单独保留，未混入成功分母。无据项目绑定 0、无据直接影响 0、SOURCE_CLAIM 冒充正式批准 0。所有已确认正例均覆盖独立 Gold 指定的来源文本；证据存在仍须经过关系语法和唯一绑定门槛。

| 题 | 实际判定 / 依据 |
| --- | --- |
| D03 | RELATED_TO SUPPORTED / WORK_LINK；“B1的接口调整与A.md的联调工作相关”。SAME_TASK UNKNOWN；没有修订推导。 |
| D06 | AFFECTED_BY SUPPORTED；B 明说“因为A.md记载的从云桥科技改用星帆通信，此任务必须调整签名参数”，另回读真实审核、旧/新供应商及替代链。 |
| D07 | A2 IN_PROJECT SUPPORTED；B2 IN_PROJECT、SAME_TASK、RELATED_TO、AFFECTED_BY 均 UNKNOWN。REVISES_PLAN 保留旧 Gold，明确排除为未实现，未计算日期差。 |
| D08 | PROJECT_ALIAS SUPPORTED；B3→星河项目 SUPPORTED，保留别名前提 ID；B6→星河项目 REFUTED，依据“天河项目”的独占标题上下文。两条 M1.project 仍为 null。 |
| V01 | 同文多项目：正确绑定 / 明确排他反证。 |
| V02 | 本工作区别名 SUPPORTED，跨 workspace NOT_ASSESSABLE。 |
| V03 | 任务明确无需因本次变更调整：REFUTED。 |
| V04 | 当前来源不存在旧变更证明：NOT_ASSESSABLE。 |
| V05 | 同供应商但没有因果：UNKNOWN。 |
| V06 | 直接因果 SUPPORTED，kind=SOURCE_CLAIM，正式批准权威未建立。 |
| V07 | 本任务明确回指项目登记文档，双来源桥接 SUPPORTED。 |

## 四项结构化示例与 D07 缺口

[key-results.json](evidence/key-results.json) 是实际输出的精简投影；四个完整 JSON 保留所有端点、来源和读取状态。

```json
[
  {"case_id":"D03","predicate":"RELATED_TO","verdict":"SUPPORTED","relation_detail":"WORK_LINK"},
  {"case_id":"D06","predicate":"AFFECTED_BY","verdict":"SUPPORTED","change_kind":"REVIEWED_DECISION"},
  {"case_id":"D07","predicate":"IN_PROJECT","subject_label":"B2","verdict":"UNKNOWN"},
  {"case_id":"D08","predicate":"PROJECT_ALIAS","verdict":"SUPPORTED","relation_detail":"ALIAS_SCOPE:短信接入"}
]
```

D07 的 A2 原文“我负责星河项目的短信接口接入，计划在2026年10月14日前完成联调”；B2 原文“我继续负责短信接口接入，将按新供应商星帆通信的接口调整，计划在2026年10月18日前完成”。B2 缺项目标题/明确跨文档项目指代；缺到 A2 的唯一任务编号或身份回指；缺明确的工作联系声明；也缺指向 B1 那一次供应商替换的具体因果/变更桥。“继续负责”、星帆通信、相邻发言和两个日期不能补齐这些证据。两条观察保持原值，并不等于确认了两个不同业务任务。

## 正式审核与来源声明的权限隔离

D06 的 [authority_audit](evidence/D06.json) 记录：capture 后正式记录数 0；审核前两次物化均被 APPROVAL_MISSING 阻止；随后调用既有 `approve(reviewer_id, expected_candidate_fingerprint, reason)` → `materialize`，生成两份 HUMAN_APPROVAL 回执；新记录 supersedes_id 指向旧记录。判断结束正式记录仍是 2，`database_writes_during_assessment=0`。

ChangeRef 的 IDs 只是待核验引用。权限 helper 从真实 store 重新读取 candidate/receipt/event，比较 fingerprint、来源、工作区、项目、uses vendor、供应商值、有效时间边界及 supersession。伪造 receipt ID、来源失效、撤销 candidate 均不能通过。合成库测试还证明已有 immutable receipt 触发器拒绝篡改，以及禁止 SQL 写操作时权限核验仍可完成。

V06 的 [SOURCE_CLAIM 实际输出](evidence/V06.json) 可确认原文所说的直接影响，但 decision_id/candidate_id/receipt_id 均为空，`authority_checks` 明示 SOURCE_CLAIM_ONLY / FORMAL_AUTHORITY_NOT_ESTABLISHED。未调用批准路径，也不把原文“正式”当成审核回执。关系本身始终为 SUPPORTING_ONLY。

## 验证、失败记录与复现

- P2 聚焦 53 项；与 P1 55 项合跑 **108 passed**。M1、P1、文档/来源、candidate/物化、Decision Memory 写策略/有效时间/撤销相关检查 **188 passed**（其中包含 P1，两个批次不能直接相加宣称去重总数）。本轮未重跑旧 HTTP/付费评测。
- 31 个基线文件核对：只有允许扩展的 P1 模型/校验文件变化，M1 源码、P1 原 fixture/Gold/历史 JSON 均未变；六份历史 P1 v1 判定完整往返，包括原 assessment ID。
- 重复执行、序列化与 recorded_at 变化不改变判定 ID；来源版本变化会阻止旧观察/关系继续成为当前事实，旧记录不被覆盖。本轮没有关系生命周期事件持久化。
- 开发中的失败日志保留在本地新建 P2 目录：缺少类型的 RED、有限语法未覆盖、分号漏读正证、标题与排他归属冲突。均通过新增检查后修复。一次测试试图修改临时回执以验证防护，先被已有 immutable trigger 拒绝；改为直接断言该现有防护，没有绕过触发器。旧成绩及历史日志未改。
- 独立审阅另发现窄提案借用未提交标题、嵌套标题截断父作用域、单句重复变更绕过唯一性三个问题；先以 `test_review_*` 复现三项失败，再修复并纳入 108 项最终检查。标题/声明须分别由已提交范围覆盖，父标题仅由同级或更高标题关闭，变更按出现次数而非语句数量计数。原审阅与复核结论见 [REVIEW.md](REVIEW.md)。
- 干净发布副本以既有 HEAD 的 `git archive` 加 53 项明确交付文件构建，未复制其他工作区修改：P2、P1、M1 及上述相关回归合并去重后 **241 passed**，Dev 导出再次 20/20。证明本次提交范围可独立复现。该副本使用新的临时数据库，没有打开原库。隔离副本首次解压因 Windows 长路径失败，随后改用项目 `work/` 下更短目录；两个副本均保留，不删除已有内容。
- Dev 输入 SHA256 `2cdf6a0fe51b85d2b26d438438a1555a1d86c42a350ed942021f5f3d8af74a3d`；独立 Gold SHA256 `ef594322ff2034ef927eff94dcfe985891769ed5f714ee40b10dd2e4b55d97fc`。独立作者先固定语义/Gold，生产入口不读 Gold；公开设计曾含保留题语义，未来不能声称严格盲测。H01–H04 本轮未物化/执行。

复现（新 checkout，安装项目的 dev 依赖，不配置 Provider）：

```text
python -m pip install -e ".[dev]"
python scripts/run_relation_checks.py replay-p2 tests/unit/test_task_relations_p2.py tests/unit/test_task_change_authority.py tests/integration/test_task_relations_p2_dev.py tests/unit/test_task_identity_resolution.py tests/integration/test_task_identity_dev.py
python scripts/evaluate_task_relations_p2.py --output work/p2-replay-unique
```

合并回归可在第一条测试命令后追加：`tests/unit/test_task_observation.py tests/integration/test_task_observation_flow.py tests/unit/test_document_text.py tests/unit/test_semantic_ingestion_candidates.py tests/unit/test_semantic_ingestion_materialization.py tests/unit/test_decision_memory_write_policy.py tests/unit/test_decision_memory_reconciliation.py tests/unit/test_temporal_decision_memory.py`，使用新的 label；对应本轮干净副本 241 项检查。

每次使用新的 label/输出目录；入口拒绝覆盖旧证据。SQLite 只在指定的新临时目录创建，用于现有审批流程，未建关系数据库。Windows 环境部分 pytest 在成功退出后打印既有 pyreadline3 console 析构警告，最终测试退出码是 0。

## 已知覆盖边界与 P3 依赖

当前只覆盖有限中文显式表达：原文责任句、可识别项目标题/排他声明、本工作区内部代号、唯一任务/文件工作回指、明确供应商对及本任务因果/无影响、单个本地变更的明确指示代词。复杂代词、跨段隐式承接、自由叙事因果、任意语言别名、同名项目实体消歧、任意任务发现均未完成。超出语法或多个可能锚点保留 UNKNOWN；非法或旧来源返回 NOT_ASSESSABLE。正式变更目前保守要求可回读 HUMAN_APPROVAL 和未使用字段覆盖的来源候选，未声称覆盖所有 policy authorization / 人工字段重解算形式。

没有调用真实 Provider；测试通过率不等于真实模型抽取或自动发现能力。没有基于相似度补规则或硬编码人物/项目/任务编号。独立单元样本使用青岚、海棠、羽舟、杉林数据、远川服务等不同名称。

P3 还需要：有效 SAME_TASK 判定作为前提；明确“修订/替代/纠错”的计划证据及新旧有向端点；字段变更与证据依赖；冲突/撤销/来源失效阻断；日期精度、任务截止角色和 BEFORE 等边界的可比性检查。只有身份和修订均被支持时才能产生 RevisionComparison；缺年份、不同角色、只有日期不同均不算延期。P2 的 RELATED_TO/AFFECTED_BY 不替代这些前提。本轮停在 P2，不自动进入 P3。
