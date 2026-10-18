# M2-P3 计划修订与截止边界比较

日期：2026-10-18。基线：`d085240f0f5ee1aa804824c61cc4f95f4734cf42`，分支 `codex/linkloom-m2-progress`。本轮实现固定观察对上的 **SAME_TASK 重验 → REVISES_PLAN → RevisionComparison**。本地离线验收通过；无真实 Provider、保留集、CI 或部署结论。停在 P3。

## 修改文件与调用链

| 文件 | 职责 |
| --- | --- |
| `src/linkloom/semantic_ingestion/task_relation_models.py` | 在原 RelationAssessment 上增加 REVISES_PLAN、changed_fields、asserted_effective_at 及有限证明角色。只有新谓词输出 v3 / plan-revision-rules/v1；P1 v1、P2 v2 payload 和 ID 不变。 |
| `src/linkloom/semantic_ingestion/task_plan_revision.py` | 只读身份前提重放、完整提案/观察范围、有限修订/反证/资料纠错、方向和竞争计划分支校验。 |
| `src/linkloom/semantic_ingestion/task_revision_comparison.py` | DeadlineProjection、RevisionComparison；DAY/角色/边界门槛、null delta、安全计算和明示生效日期元数据。 |
| `tests/task_revision_support.py` | 只加载公开来源，调用真实 M1 builder；五项非法前提独立契约试验，不读 Gold。 |
| `tests/unit/test_task_plan_revision.py`、`test_task_revision_comparison.py`、`test_task_revision_compatibility.py` | 主语/范围/否定/冲突、伪造/过期前提、日期边界、重放/序列化和旧输出兼容。 |
| `tests/integration/test_task_revisions_dev.py`、`tests/fixtures/task_revisions/` | 11 个 Dev 来源家族、12 个原子修订题；独立输入与 Gold，不改旧标注。 |
| `scripts/evaluate_task_revisions.py` | 先持久导出预测，再读取 evaluator-only Gold，输出逐题完整匹配与分子/分母。 |
| `scripts/run_relation_checks.py` | 增加 `--stage p3`，所有新日志/临时库进入新 P3 目录；默认 P2 命令保持兼容。 |
| 本文、`REVIEW.md`、`evidence/`、根 `DELIVERY.md` | 交付、初审/复审、合成结构与失败记录。没有提交数据库或大型临时日志。 |

真实调用链：`inputs/dev.json → RawArtifact / parse_source_text → TaskProposal / build_task_observation / verify → assess_same_task → assess_plan_revision → compare_revision → to_dict → 独立 Gold evaluator`。生产调用方可直接传入现有 TaskObservationQuery 返回的观察；本轮没有接入 Runtime、摄取自动关系发现或 UI。

身份前提不能靠 verdict 或 ID 字符串放行：先 canonical 反序列化检查，再在同一固定端点、artifact/registry/竞争观察清单上调用现有 P1 resolver，核对完整稳定 assessment ID。任一证据、来源版本、hash、工作区、payload、判定内容或当前性不同都阻断。比较入口还重新执行修订验证，拒绝伪造或过期的 REVISES_PLAN。调用方必须提供与前提一致的有界 context；增加上下文后应重算前提，不复用旧快照结果。

修订方向来自明确回指/替代语句，保持 new→old，不按 event_time 排序。证明同时须在提案字符范围和观察 raw_text 字符范围内；观察之外的尾随业务语句不能被整文提案借入。任务编号、项目/负责人、日期差或 source v2 都不能单独生成修订。支持 deadline 和 scope；冲突或未知时 changed_fields 不冒充已确认变化。

## 实际结构化结果

完整来源、观察、两个关系、正反证、版本/hash/范围、依赖与日期证据：

- [D04 两次修订](evidence/D04.json)
- [D05 无修订声明](evidence/D05.json)
- [D07 原五行阻断](evidence/D07.json)
- [精简实际 JSON](evidence/key-results.json)

以下是实际结果的精简投影（完整 JSON 中均保留 SAME_TASK / REVISES_PLAN IDs 与字段证据）：

```json
[
  {"question_id":"D04-b-revision","same_task":"SUPPORTED","revision":"SUPPORTED","changed_fields":["deadline"],"comparison":"COMPUTABLE","old":"2026-10-14","new":"2026-10-18","old_bound":"BEFORE","new_bound":"BEFORE","delta_calendar_days":4},
  {"question_id":"D04-c-revision","same_task":"SUPPORTED","revision":"SUPPORTED","changed_fields":["scope"],"comparison":"COMPUTABLE","old":"2026-10-18","new":"2026-10-18","old_bound":"BEFORE","new_bound":"BEFORE","delta_calendar_days":0},
  {"question_id":"D05-revision","same_task":"SUPPORTED","revision":"UNKNOWN","comparison":"BLOCKED","delta_calendar_days":null,"reason_codes":["NO_REVISION_EVIDENCE"]},
  {"question_id":"D07-revision","same_task":"UNKNOWN","revision":"UNKNOWN","comparison":"BLOCKED","delta_calendar_days":null,"reason_codes":["SAME_TASK_NOT_SUPPORTED"]}
]
```

D04 B 原文明确“本条就是A.md的同一任务，现修订其计划，将原定2026年10月14日前完成改为2026年10月18日前完成，本计划替代原计划”。证明身份、旧/新日期及替代方向后计算 +4，含义仅为**计划截止边界后移四个日历日**。生效时间未写，asserted_effective_at=null，不声称 10 月 15 日已生效，更不声称实际工作延期。

D04 C 原文“本条替代B.md计划，仅在原任务范围内增加回执校验，截止边界不变，仍在2026年10月18日前完成”。因此 changed_fields=[scope]；两个 DAY / TASK_DEADLINE / BEFORE 均可回读且明确未变，delta=0。没有把 scope 关联升级成任务合并或自动传递 C→A。

D05 只有相同作用域任务编号和不同日期，缺替代/修订声明；D07 连 SAME_TASK 都未证实，也没有明确计划桥接。二者保留观察中的 14/18 日期，delta 都是 null，不是 0，不输出“延期四天”。

## Dev 逐题与指标

独立 Gold/完整逐题评分见 [metrics.json](evidence/metrics.json)。Revision Accuracy 按原子题匹配 verdict、changed_fields、comparison status、delta，并核对 Gold 明示要求的日期/边界/生效元数据。阻断题 Gold 的 null 日期槽表示不要求比较值；输出仍可保留原观察已知日期，不能据此计算。未标注关系不计负例。

| 原子题 | REVISES_PLAN | 比较 | 字段 / delta |
| --- | --- | --- | --- |
| D01 | REFUTED | NOT_APPLICABLE | 仅改称呼、不修订；null |
| D04 B→A | SUPPORTED | COMPUTABLE | deadline；+4 |
| D04 C→B | SUPPORTED | COMPUTABLE | scope；0 |
| D05 | UNKNOWN | BLOCKED | 无修订证据；null |
| D07 | UNKNOWN | BLOCKED | 身份未证实；null |
| V01 | SUPPORTED | NOT_COMPARABLE | 旧日期缺年份；null |
| V02 | SUPPORTED | NOT_COMPARABLE | BEFORE / ON_OR_BEFORE 不兼容；null |
| V03 | REFUTED | NOT_APPLICABLE | 明确资料录入纠错、不是业务修订；null |
| V04 | CONFLICT | BLOCKED | 两个有效竞争计划，没有唯一替代链终点；null |
| V05 | SUPPORTED | COMPUTABLE | deadline；+5，明示未来生效 |
| V06 | SUPPORTED | COMPUTABLE | deadline；-6 |
| V07 | SUPPORTED | COMPUTABLE | 原文完整重述同一截止边界；0 |

| 指标 | 分子 / 分母 |
| --- | --- |
| REVISES_PLAN Precision | 7 / 7（TP=7，FP=0） |
| REVISES_PLAN Recall | 7 / 7（FN=0） |
| Revision Accuracy | 12 / 12 |
| COMPUTABLE 日期、边界与 delta 正确 | 5 / 5 |
| UNKNOWN / REFUTED / CONFLICT 正确 | 2/2、2/2、1/1 |
| 无证据修订确认 / 虚假日期变化 | 0/12、0/12 |
| 非法/过期身份前提放行 | 0 / 5 独立契约试验 |
| 关系证据原文读回 | 65 / 65 |
| 日期字段/边界原文读回 | 46 / 46 |
| 修订依赖来源读回 | 52 / 52 |

[非法前提真实结构](evidence/invalid-premise-trials.json) 分别为 canonical 但内容伪造的身份、目录中已过期身份、篡改来源版本、跨 workspace、合法身份引用了错误观察对。全部 NOT_ASSESSABLE，比较 BLOCKED / null；另有单元测试使用真实但不支持身份命题的 quote 构造 canonical SUPPORTED，重放同样拒绝。NOT_ASSESSABLE 与 UNKNOWN 分开，没有用全 UNKNOWN 获取安全分。

输入 SHA256 `0261dadbeb956ca12a805fe10da56bf1b5687a4a387026a63838556a83fcbfb4`；独立 Gold SHA256 `0ee024dcbf691c53734b0e334bc0ec628524cd0c7b1295efa78ad09c1d894e57`。旧 D01/D04/D05/D07 语义和标注不变，新增 D04 C 及独立青岚/澄明/赤松等项目变体。Gold 不进入被测 loader/函数。H01–H04 未运行、未用来调规则，不声称严格盲测或真实模型泛化。

## 日期、生效时间和来源纠错边界

DeadlineProjection 仅派生 M1 日期字段已有的角色、精度、原文范围；不改 M1 日期契约。BEFORE、ON_OR_BEFORE、ON、UNKNOWN 由有限原文角色词验证。完整 DAY、相同且明确的边界、TASK_DEADLINE、有效身份和修订缺一不可。含“最晚在…前”“前完成，含当日”等冲突提示保留 UNKNOWN。没有用 event_time、recorded_at、document_date 或审核时间补年/补截止日。

0 需要支持的修订和明示未变化边界，或完整的相同旧/新边界重述。日期相同但缺修订证据仍 BLOCKED / null；scope 修订却写了未说明改变的不同日期，则 NOT_COMPARABLE。无年份、角色或边界不足也为 NOT_COMPARABLE；明确不修订为 NOT_APPLICABLE；前提、冲突或未知为 BLOCKED。

[V05](evidence/V05.json) 只从“本计划自2026年12月20日起生效”提取 DAY 精度的 asserted_effective_at。在明确传入 as_of=2026-12-15 时，plan_currentness=FUTURE_EFFECTIVE；可以比较所声明的计划边界，但不会替换当前计划。没有 as_of 或明示生效时间时是 NOT_ESTABLISHED。EFFECTIVE_AT_REFERENCE 仅表示本次声明的日期门槛已到达，不是全库唯一当前计划查询；本轮未实现计划投影选择器。

[V03](evidence/V03.json) 明说“修正录入错误，不是业务计划修订”；两份独立来源仍 CURRENT，返回 REFUTED / NOT_APPLICABLE，不把数值差当真实业务调整，也不擅自将另一来源作废。[V04](evidence/V04.json) 保留仍有效的竞争替代声明，不按来源更新时间胜出。P4 才负责追加式失效、撤销与历史查询。

## 检查、失败保留与复现

- P3 聚焦 **44 passed**，既有 M1/P1/P2/Decision Memory/正式审核相关回归 **241 passed**。P1 v1 / P2 v2 共 12 份已发布判定完整 JSON 与 ID 往返不变。正式审核回归使用测试自身全新临时库；未操作原库或进程。
- 发布范围复现：从已提交 P2 基线归档，再仅覆盖本轮 30 项交付文件，在新的隔离副本执行同一组聚焦与回归检查，**285 passed in 66.83s，exit=0**。离线 evaluator 再现 12/12、Precision 7/7、Recall 7/7 和五项非法前提全部阻断。没有借用未提交的无关改动。首次在归档覆盖尚未完成时提前启动检查，旧 runner 将 `p3` 当作测试路径并报错，未执行测试；等待归档完成后使用新 label 正常复现，没有覆盖原失败产物。
- [integrity.json](evidence/integrity.json)：38 项受保护源码/fixture/历史证据，只有获准扩展的模型文件变化；M1 源码、P1 resolver、P2 校验和历史输出不变。既有无关 tracked diff 完整保留。
- 开发 RED 和失败日志保留在新的 `work/m2-p3-task-revisions/acceptance-local/`，脱敏摘要见 [failure-records.json](evidence/failure-records.json)。含初始缺模块、有限语法缺口、正反证被吞、别的任务语句误挂，以及独立审阅 R1–R3。两项测试设置错误也保留：伪造日期角色先被 M1 构造器拒绝；一个范围样本的身份句与“仅在”在同一语句，被 P1 保守拒绝，调整独立测试的明确语句分隔后验证 P3，不改 P1 或 Gold。
- 独立只读审阅 [REVIEW.md](REVIEW.md) 保留初审失败和复审：完整观察范围、模态否定、混合边界均先复现失败再修复，最终 PASS，无剩余 Required。没有通过删除反证、放宽来源校验或写死业务名称提高分数。

复现（项目 dev 依赖已安装；不用 Provider 配置）：

```text
python scripts/run_relation_checks.py --stage p3 replay-p3 tests/unit/test_task_plan_revision.py tests/unit/test_task_revision_comparison.py tests/unit/test_task_revision_compatibility.py tests/integration/test_task_revisions_dev.py
python scripts/evaluate_task_revisions.py --output work/p3-replay-unique
```

测试 label/输出目录必须用新名称，脚本拒绝覆盖旧证据。Windows 部分运行在成功 pytest 退出后打印已有 pyreadline3 console 析构警告，最终测试退出码为 0；没有 CI/部署推断。

## 局限及 P4 的确切依赖

这是有限中文显式证明校验器，未覆盖任意自然语言、所有修订说法、跨段含糊代词、多任务文件中的无编号计划指代、任意有效时间精度或全项目计划穷尽性。只支持明确的 DAY 生效声明和日历日比较；未知表达保守阻断。当前无关系 SQLite、自动候选发现、Agent Planner、GraphRAG、UI、通用双时态或真实 Provider 能力。

P4 需保存版本化 ObservationRef、v1/v2/v3 assessment 和 comparison 引用，追加生命周期/撤销/替代事件；展开 SAME_TASK→REVISES_PLAN→comparison 的依赖闭包，查询时按当前 source registry 重验。只读查询不得建库、自动重算写库或复活旧关系；来源改变或前提撤销应阻断当前视图并保留历史。计划生效日未知时不能靠 recorded_at 选当前计划，未来计划在生效前不能覆盖当前计划。应先建立独立侧库的读写与事件契约，再接项目/来源/观察查询和计划投影；不更改现有 Decision Memory 审批边界。本轮不进入 P4。
