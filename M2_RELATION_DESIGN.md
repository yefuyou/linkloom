# M2 任务关系与时序修订设计

状态：设计草案，等待用户确认后才可开发。日期：2026-10-18。本文描述新增契约，不代表已实现能力。本轮仅静态核对代码和 M1 交付文档，没有运行评测、Provider 或数据库操作。

## 1. 首先禁止的错误结论

| 危险设计 | 会产生的错误 | M2 约束 |
| --- | --- | --- |
| 同负责人、同项目、语义相似或时间接近即合并 | 把联调、接口改造、验收等不同交付物合成一个任务 | 这些信息只能召回候选；身份确认需要明确指代或作用域内的任务标识 |
| 后出现的日期覆盖前日期 | 把并行任务、纠错、来源更新说成延期 | 身份确认与计划修订分别举证，然后才比较截止边界 |
| 供应商变更后出现的工作均算受影响 | 把时间先后说成因果，甚至声称已经找全 | 要求变更与具体工作的直接适配、阻塞或变更原因证据；限定检索范围 |
| 将 A 的项目复制给 B | 隐藏跨文档歧义，污染原始观察 | 项目归属单独建有出处的关联；M1 字段保持不变 |
| 找不到关系即判定无关系 | 召回缺失变成业务否定 | UNKNOWN 与有反证的 REFUTED 分开 |
| 新版本、最近导入或“正式决定”自动胜出 | 旧证据复活、冲突被覆盖、未审核记录获得正式权威 | 版本、时序、关系有效性和正式审核是不同维度 |

## 2. 当前可复用的真实边界

早期盘点报告描述的是 M1 之前的状态；本次以当前代码及 M1 交付为准。

| 已有代码 | 真实能力及 M2 用法 | 不具备的能力 |
| --- | --- | --- |
| [TaskObservation](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_observation.py:79)；[ID 生成](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_observation.py:73) | 来源版本、位置绑定的不可变观察；字段证据；`verify(artifact)` 读回校验；复用 owner/deadline/project/vendor | observation ID 不是业务 task ID；`relation_status` 固定 UNCONFIRMED，不可改为 M2 关系状态 |
| [TaskObservationQuery.list](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_observation_query.py:29) | 按 workspace/source/project/version 读候选旁路；CURRENT/NOT_CURRENT 与 extraction_conflict | project 只是规范文本相等；没有项目别名、关系联查或完整历史快照 |
| [RawArtifact](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/artifacts.py:103)、[EvidenceSpan](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/artifacts.py:205) | 复用现有 Source Identity：workspace、artifact、version、hash、evidence_ref、字符范围 | 不再引入第二套来源 ID 或复制来源全文 |
| [candidate persistence](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/candidate_persistence.py:25)、[首次捕获保留](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/candidate_persistence.py:225) | v1/v2/v3 读取；任务旁路和重复摄取基础；M2 只引用这些观察 | 候选表中没有 M2 关系契约；不能把关系塞入抽取审计即算完成 |
| [capture](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/materialization.py:533)、[approve](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/materialization.py:755)、[materialize](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/materialization.py:922) | Supporting Only 与正式审核/物化边界；关系不走正式决策写入 | 抽取成功、原文“正式”及关系证实均不授予 DecisionRecord 权威 |
| [DecisionRecord](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/decision_memory/models.py:79)、[get_history/get_as_of](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/decision_memory/store.py:1935) | 读取已审核供应商选择、替代链及决策有效期；引用已有记录/回执 | 决策 supersedes 不是任务修订；ActionRecord 不承担本轮任务身份 |
| [SourceReferenceRegistry](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/decision_memory/sources.py:10)、[reconciliation](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/decision_memory/reconciliation.py:51) | 复用来源版本/hash 核验及 stale/revalidation 的语义 | 现有 reconciler 会写决策状态，不能充当只读任务关系验证器直接调用 |
| [FrozenRelationResolver](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/relations.py:60) | 有限关系词表及显式别名的设计经验 | 不是任务实体解析器；供应商 explicit_replacement 规则不能直接证明 SAME_TASK |

复用调用链：`M1 摄取 → Supporting Only candidate/v3 → TaskObservationQuery → M2 候选对 → 证据读回与关系判定 → 独立关系存储/只读投影`。正式供应商决策由 Decision Memory 只读接入。M2 不改摄取、RAG、Runtime、正式审核或 M1 历史 payload。

## 3. 最小关系契约

### 3.1 端点与身份

- `ObservationRef`：workspace_id、observation_id、既有 source/version 引用、观察语义 payload 的 canonical hash。hash 忽略 recorded_at；同一 ID 有不同抽取内容时必须指定版本化 payload，不能静默选择其中一个。
- `ProjectRef`：作用域内的项目规范名称/已有显式标识及其证据。不建设通用项目实体库。未有别名证据的不同名称保持分开。
- `ChangeRef`：二选一：已有 `decision_id + receipt/source refs`，或未经审核的 `source claim + EvidenceSpan`。后者明确标为 SOURCE_CLAIM；不生成假 DecisionRecord。任务可以与“来源记载的变更”直接关联，但不能称其为已批准决策。
- 来源中的任务编号作为**有证据的外部标识**引用，限定 workspace、项目/系统命名空间。不同项目复用 `API-7` 不证明同任务；同项目内标识冲突也不自动合并。不新增业务 Task 主表。

### 3.2 有限关系类型

所有记录均有明确的 `subject → object`。表中方向是唯一存储方向，不用时间排序猜方向。

| predicate | 方向 | 确认要求 |
| --- | --- | --- |
| SAME_TASK | 声称对应的观察 → 被指向的锚观察 | 明确“与某记录/编号为同一任务”，或作用域明确、无冲突的同一任务编号；保留两条观察，不物理合并 |
| RELATED_TO | 描述关联的观察 → 被关联观察 | 明确依赖、承接、共同变更等工作联系，附 relation_detail；不等于身份相同，也不自动倒置依赖方向 |
| AFFECTED_BY | 任务观察 → 供应商变更 ChangeRef | 明确该项工作因该变更而调整/适配/受阻；任务和具体变更均能绑定。只提同一供应商或发生在之后不足 |
| REVISES_PLAN | 新计划观察 → 被修订的旧计划观察 | 有效 SAME_TASK 判定 + 明确修改/替代原计划的证据；列出修订字段，不能靠两个日期倒推 |
| IN_PROJECT | 任务观察 → ProjectRef | 自身显式项目，或有界且唯一的上下文/跨文档指代证据 |
| PROJECT_ALIAS | 别名 ProjectRef → 规范 ProjectRef | 明确“别名/内部代号/又称”及作用域证据；字符串相似不算 |

SAME_TASK 的业务命题具有对称性，但上述方向表示**哪个观察指向哪个锚点**，不表示因果或先后；查询相反方向时仅返回同一证明的反向视图，不新增一条事实。无明确锚点而使用同编号时，以 observation ID 字典序较大者指向较小者，保证幂等。评测按该规范方向比较。其他关系遵守各自方向。M2 不执行 SAME_TASK 的传递闭包、union-find 聚类或影响关系传递；A=B、B=C 不能绕过 A/C 的证据/冲突检查。

### 3.3 RelationAssessment（新增，提议）

| 字段 | 含义 |
| --- | --- |
| schema_version、workspace_id、assessment_id | 版本化不可变记录；ID 对规范化端点、predicate、证据集合、判定及规则版本计算 hash，排除记录时间，重复运行不重复写入 |
| subject、predicate、object、relation_detail | 有类型的端点和关系；detail 只用有限枚举，不开放任意图谱谓词 |
| phase、verdict、reason_codes | phase=CANDIDATE/ASSESSED；候选 verdict=null；ASSESSED 的 verdict=SUPPORTED/REFUTED/UNKNOWN/CONFLICT/NOT_ASSESSABLE |
| proof | 每个必要前提的 EvidenceSpan + 子范围/原文；支持证据、反证分开；可引用其他 assessment_id，但必须展开检查依赖 |
| depends_on | 观察 payload hash、来源版本/hash、项目绑定、别名、身份判定及必要决策回执；形成有界可核验依赖 |
| assessed_at、rule_version、producer_ref | 系统判定记录时间、规则版本、离线模型/规则运行标识；均不是业务生效时间 |
| asserted_effective_at | 仅原文明示的关系/计划生效时间，可空，带精度及时间证据 |
| replaces_assessment_id、change_reason | 判定纠错/重验链；与 REVISES_PLAN 分开 |

`lifecycle` 从追加事件与指定 source snapshot 投影：ACTIVE / STALE / RETRACTED / REPLACED / INVALIDATED。事件至少有 event_id、assessment_id、event_type、recorded_at、操作者/规则、原因、必要撤销证据。撤销可以是明确的人工关系纠错，不伪造业务原文。

对外“已证实”严格等于 **ASSESSED + SUPPORTED + ACTIVE + 全部前提有效**，表示来源支持该关系命题，不表示获得业务批准。“无法判定”包括 UNKNOWN（缺桥接证据）、CONFLICT（有效证据相互冲突）和 NOT_ASSESSABLE（坏引用/抽取冲突等），分别解释。REFUTED 必须有明确反证，例如“与旧任务是不同任务”；没有修订措辞应为 UNKNOWN，不能 REFUTED。置信分值不能把候选升级。

建议 reason_codes 至少包括 EXPLICIT_TASK_REFERENCE、SCOPED_TASK_KEY、DIRECT_CHANGE_DEPENDENCY、EXPLICIT_PLAN_REPLACEMENT、PROJECT_CONTEXT_BOUND、INSUFFICIENT_IDENTITY、NO_REVISION_EVIDENCE、AMBIGUOUS_PROJECT、CONFLICTING_SOURCE、STALE_EVIDENCE、EXTRACTION_CONFLICT、DATE_NOT_COMPARABLE。

## 4. 项目、影响与证据判定规则

1. **先校验，再判定。** 所有来源读回 workspace/version/hash/span 和精确字符片段；M1 字段继续用 `TaskObservation.verify`。证据片段命中只证明“确有这句话”，还要检查谓词角色、否定、建议、条件及所指端点。自由模型解释不能代替 proof。
2. **明确指代不等于字符串命中。** 身份证据必须唯一指向观察/其外部任务标识。泛称“继续负责接口工作”不能证明交付物相同。出现同名任务、编号复用、不同子任务时返回 UNKNOWN/CONFLICT。
3. **项目归属单列。** M1.project 非空可产生显式 IN_PROJECT；缺失时只能通过唯一的项目标题作用域、明确“上述项目”指代、任务编号跨文档引用或明示项目链接建立派生 IN_PROJECT。必须保存标题/链接/指代桥及任务原文两端证据。标题作用域截至下一个同级或更高级标题；多项目混杂、裸文档同现不够。PROJECT_ALIAS 要再附别名证据和作用域，不静默全局替换。
4. **查询防漏且不冒领。** 项目查询返回 `explicit_project`、`derived_project + assessment_id`，并可单列 `possible_project`；原观察 project=null 仍为 null。不能仅先调用 M1 `project=星河项目` 过滤，否则丢掉需要绑定的 B；先按来源/显式引用读取有界观察集合，再联查项目关系。
5. **影响只认直接证据。** “因星河项目从云桥改用星帆，任务 X 必须调整签名参数”可支持 AFFECTED_BY；“换供应商，稍后 X 截止 18 日”不支持。任务依赖链可作为调查线索，不自动传递因果。潜在影响与已证实影响分别展示，未完成搜索不宣称完整清单。
6. **权威和事实支持分开。** 已审核供应商替代链复用 Decision Memory；未审核变更只能作为来源记载。若查询要求“已批准变更”，缺有效回执必须返回 AUTHORITY_NOT_ESTABLISHED，不能通过 AFFECTED_BY 提升权威。任务负责人沿用各观察的字段证据；身份确认也不允许把旧负责人填入新观察的空值。

## 5. 时间与计划修订

| 时间/边界 | 来源与用途 | 禁止混用 |
| --- | --- | --- |
| decision effective time | 已审核 DecisionRecord.valid_from/valid_to，按已有半开区间语义查询 | 不能作为任务开始/截止时间 |
| task deadline | M1 deadline、deadline_precision、deadline_role 及字段证据 | 不用文档、发言、导入日期补全年月日 |
| event_time | M1 来源事件时间；可能未知 | 晚发言不等于新计划生效、晚发生不等于因果 |
| recorded_at / assessed_at | 观察导入、关系判定的系统时间 | 不用于选择“最新业务计划” |
| source update/version | 资料版本及目录当前性 | 编辑资料不等于业务改计划 |
| asserted_effective_at | 修订声明明确给出的生效时点/精度 | 未明示则 null；不借用 decision/event 时间 |

`RevisionComparison` 是只读派生结果，引用旧/新观察、SAME_TASK 与 REVISES_PLAN 的 assessment_id、修订字段证据。步骤固定：

1. 两条观察及必要桥接证据在所选 snapshot 中可核验，且无未解决冲突；历史模式明确列出所用旧版本。
2. SAME_TASK 已证实；REVISES_PLAN 已证实且修订方向唯一、不是抽取纠错或来源编辑。修订可只改范围而不改日期。
3. 比较日期必须均为有证据的 TASK_DEADLINE、完整 DAY；从原 deadline_text 验证边界词，增加派生 `deadline_bound=BEFORE/ON_OR_BEFORE/ON/UNKNOWN`，不改 M1 schema。边界语义不清/不一致、无年、仅月、时区/粒度不兼容时返回 NOT_COMPARABLE。
4. 可比时 `delta_calendar_days = new_date - old_date`；保留旧新值、边界与证据，0 表示明确未变，负数表示截止边界提前，正数仅称“计划截止边界后移 N 个日历日”。不推导实际延误、工期或完成状态。

关系未成立返回 BLOCKED（具体门槛）；明确无该修订返回 NOT_APPLICABLE；已修订但日期不足返回 NOT_COMPARABLE。这些结果的 delta 均为 null，不得用 0。原文明确“旧截止改为新截止”足以给出修订方向，即使生效时间未知也可计算边界差；但不能回答“某一天已生效的计划是什么”。该 as-of 问题必须有足够业务时序证据；不靠 assessed_at 排序。

计划投影只选择证据充分、无分叉冲突的明确替代链终点。生效时间未知时仅称“最新声明的计划”，不能称“当前已生效计划”；已知未来生效的修订，在生效前不能替换当前计划。多个无明确替代关系的候选并列为 CONFLICT/UNKNOWN，不能 last-write-wins。无需通用双时态引擎。

## 6. 来源变化、失效与撤销

| 事件 | 当前视图 | 历史与重新确认 |
| --- | --- | --- |
| 同版本重复导入/序列化 | 相同 observation/assessment 不重复；捕获时间不改变身份 | 重放仍幂等 |
| 同 artifact 新版本，即使只改标点 | 新 observation ID；旧端点/证据不再作为 CURRENT 支持 | 保留旧观察与关系；重验新版本需新 assessment，不能自动搬证据 |
| 桥接证据/别名/项目上下文消失 | 依赖它的关系及日期比较一并 STALE | 可历史读回，但默认查询不发布有效关系；不等于业务关系已被证伪 |
| 多份仍有效资料冲突 | CONFLICT；保留两方，不按更新时间胜出 | 明确纠错/撤销/替代依据后产生新判定 |
| 人工撤销/发现判定错误 | 追加 RETRACTED/INVALIDATED 事件，立即阻断依赖结论 | 保留原判定、撤销原因及时间；恢复须新判定，不能自动复活 |
| 新资料明确修改旧计划 | 单独评估 SAME_TASK 与 REVISES_PLAN | 这才可能是业务修订；与 source v1→v2 无必然关系 |

历史比较不应因“旧计划被替代”就丢弃旧计划证据：不同 artifact 的旧计划文档仍可为 CURRENT 来源；同 artifact 被新版本替代时，当前结论须由新版本对旧计划的明确重述/引用及可读旧出处支撑重新判定，或只标为历史比较。M2 不重造旧观察来伪装当前观察。

SourceReferenceRegistry 是当前目录，不提供完整旧快照。历史评测由独立 fixture 提供固定 source snapshot；生产历史接口先返回留存证据和 NOT_CURRENT 标签，不承诺“任意过去时刻数据库状态”。只读查询动态核验全部依赖，防止目录已变而关系投影尚未更新时泄漏旧结论。

## 7. 最小架构与执行责任

必要新增只有：有限关系 dataclass/状态、证据与前提验证器、项目绑定投影、修订比较器、追加式关系持久化及只读查询。建议独立 SQLite 关系侧库（复用标准 sqlite3，无新服务），只保存引用、判定与生命周期事件；不改现有 candidate payload/DecisionRecord 表。不默认创建、迁移或写原数据库。观察和决策仍由原 store 持有，侧库不是第二份任务库。

建议接口形状（均未实现）：`assess_relation(proposal, observation_reader, artifact_reader, source_snapshot, policy)`；`compare_revision(new_ref, old_ref, assessments, snapshot)`；`RelationQuery.list(workspace_id, source_id/project/observation_id, include_history=False)`。查询返回证据依赖、判定和当前性，不能只返回图边字符串。

- 确定性执行：工作区/引用/版本与字段读回，有限 schema，唯一指代与作用域检查，状态转换，审核权威检查，依赖失效，幂等，日期比较与结果分组。
- 可选 Agent：提出候选对、提出带原文的关系解释、给缺口生成补查方向。任意自然语言语义仍需有限证据类型及校验门槛；证据无法唯一支撑时留 UNKNOWN，不能由高置信度放行。
- 首轮使用固定候选对和离线 scripted responses 隔离验证关系契约；随后才接有界候选发现。无新 Planner、图数据库、自动实体聚类或关系可视化。检索召回与关系判定的 Recall 分开评估。

## 8. 原五行案例的安全结果

沿用 M1 两条观察：A=陈晨，短信接口接入/联调，2026-10-14 前，显式星河项目；B=陈晨，按星帆通信接口调整，2026-10-18 前，项目未知。M1 原始结构和历史证据不修改。

- A→星河项目可支持 IN_PROJECT；B 的“继续负责”只能提供调查线索，不能从 A 静默复制项目。
- B 明确提到星帆通信的接口适配，可报告这一原文事实；没有唯一的项目/变更桥时，B→10-15 具体变更的 AFFECTED_BY 仍是候选/UNKNOWN。
- B→A 的 SAME_TASK、REVISES_PLAN 均 UNKNOWN；两条观察不等于两个已确认独立业务任务，也不等于同一个任务。
- RevisionComparison=BLOCKED，delta=null，不能报告“延期四天”。10-15 的供应商变更只有存在有效审核回执才标为正式决策。
- 输出“已证实项/候选项/缺失证据及查询范围”，不承诺所有受影响任务已经找全。

本设计的取舍是先保留可验证的局部关系，不维护一个会把不确定身份扩散为全局事实的任务图。具体样本与评分见 M2_EVAL_SPEC.md；开发顺序见 M2_IMPLEMENTATION_PLAN.md，均待确认。
