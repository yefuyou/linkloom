# M2-P1 Task Identity Resolution — 离线交付

日期：2026-10-18。执行边界：用户批准的 M2 第一个开发切口，依据工作区 M2_RELATION_DESIGN.md、M2_EVAL_SPEC.md、M2_IMPLEMENTATION_PLAN.md。**只实现 SAME_TASK 身份判定；本地验收通过，不代表整个 M2 或真实 Provider 能力已完成。**

## 1. 文件与职责

本轮全部为新增文件；没有修改已有 M1、Decision Memory、Runtime、RAG 或 UI 生产代码。

| 文件 | 职责 |
| --- | --- |
| [task_relation_models.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_relation_models.py) | ObservationRef、EvidenceQuote、SourceDependency、RelationAssessment；五类 verdict、精确证据、稳定 ID、严格序列化 |
| [task_relation_validation.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_relation_validation.py) | 纯函数 assess_same_task；M1/来源验证、有限回指与编号作用域核验、正反证与歧义判定 |
| [test_task_identity_resolution.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/unit/test_task_identity_resolution.py) | 独立名称/人物/项目、条件/引用/局部否定、冲突、作用域、非法输入、幂等及序列化检查 |
| [test_task_identity_dev.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/integration/test_task_identity_dev.py) | 六个 Dev 题；先预测再读 Gold；检查原五行、证据与 Gold 隔离 |
| [task_identity_support.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/task_identity_support.py) | 只读取公开输入，构造真实 M1 对象；不读取 Gold |
| [inputs/dev.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/fixtures/task_identity/inputs/dev.json)、[gold/dev.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/fixtures/task_identity/gold/dev.json)、[fixture README](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/tests/fixtures/task_identity/README.md) | 独立物化的六题来源/观察与 Gold；不同路径，不把答案交给被测函数 |
| [run_checks.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/run_checks.py)、[export_acceptance.py](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/export_acceptance.py) | 本轮隔离检查、日志、真实结构及指标导出；不写原数据库或旧成绩 |
| 本文及本轮证据目录 | 交付说明、来源保护 hash、测试日志/XML、结构化判定和独立审阅记录 |

实际 M1 接口兼容点：TaskProposal.deadline_text 需要原文日期 token，例如 `2026年10月14日`，不带“前”；角色仍由完整原文校验。仅在实现前修正新 Dev 输入的 proposal，原文和 Gold 均未改；没有重新实现 M1。

## 2. 实际调用链与契约

本轮测试/演示真实路径：

```text
inputs/dev.json → public_cases / build_case
→ RawArtifact → parse_source_text → TaskProposal → build_task_observation
→ TaskObservation.verify + SourceReferenceRegistry
→ assess_same_task(subject, object, artifacts=..., source_registry=...)
→ 来源/版本/hash/抽取契约检查 → 局部身份/作用域证明 → RelationAssessment.to_dict
→ 先落盘 predictions，再由 evaluator 读取独立 Gold 评分
```

[生产入口](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/src/linkloom/semantic_ingestion/task_relation_validation.py:206) 接受既有 M1 TaskObservation 和调用方提供的 artifact/registry，另可提供 context_observations 检查已知竞争观察。没有自动接入产品摄取、TaskObservationQuery、Agent 或 UI，也不扫描数据库。

- 引用包含 workspace、observation ID、artifact/version、evidence_ref、content hash 和观察语义 payload hash；捕获时间不参与语义 hash。
- 每条实际 proof 使用已有 EvidenceSpan 加 Unicode `[start,end)` 子范围，读回原文。来源当前性、观察核验结果随 dependencies 输出。
- SOURCE_CURRENT 不等于身份确认：UNKNOWN 的证据可以完全有效。坏来源、版本/hash 不匹配、空 registry、跨 workspace、抽取冲突或非法观察返回 NOT_ASSESSABLE。
- 明确正/反证并存为 CONFLICT；明确不同任务为 REFUTED；缺身份依据为 UNKNOWN。共享负责人、日期、相似描述不参与身份成立规则。
- 同编号必须有明确项目作用域，且不存在已知编号/namespace 冲突。显式回指需唯一绑定目标来源及任务；多个同名文件、错误编号或不完整的无编号文件回指不能确认。
- 有明确回指时采用声明观察→锚观察；仅同编号时按 observation ID 规范方向。没有任务合并、传递闭包或通用实体聚类。
- `assessment_id` 由端点语义、判定、证据/依赖、规则版本等规范内容计算，排除 assessed_at 和观察 recorded_at；重放、输入顺序、序列化及重导入时间变化不改变判定身份。
- 判定仍为 SUPPORTING_ONLY。M1 observation.relation_status 保持 UNCONFIRMED，不修改负责人、项目、截止日，不生成 DecisionRecord。

## 3. Dev 逐例结果与指标

| Dev 题 | 独立 Gold | 实际判定 | 依据/限制 |
| --- | --- | --- | --- |
| D01 | SUPPORTED | SUPPORTED | 明确回指 A.md 的 SMS-17，任务改称不影响此证明 |
| D02 | REFUTED | REFUTED | “这是独立任务，不是A.md的SMS-21” |
| D03 | UNKNOWN | UNKNOWN | 工作相关且身份待核对，不能升级为同任务 |
| D04 B→A | SUPPORTED | SUPPORTED | 明确“本条就是A.md的同一任务”；不评估计划修订 |
| D05 | SUPPORTED | SUPPORTED | 同编号、同项目且无已知作用域/身份冲突；日期差不产生修订结论 |
| D07 | UNKNOWN | UNKNOWN | 原五行缺唯一身份桥；B.project 仍为 null |

实际评分见 [dev-metrics.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/dev-metrics.json)。TP=3、FP=0、FN=0：

| 指标 | 分子/分母 | 值 |
| --- | --- | --- |
| SAME_TASK Precision | 3/3 | 100% |
| SAME_TASK Recall | 3/3 | 100% |
| False Merge Rate | 0/3 | 0% |
| UNKNOWN 状态正确性 | 2/2 | 100% |
| REFUTED 状态正确性 | 1/1 | 100% |
| Evidence Validity（逐题） | 6/6 | 100% |
| 实际证据子范围读回 | 21/21 | 100% |
| 观察依赖核验 | 12/12 | 100% |
| Gold 所列来源上下文读回 | 15/15 | 100% |

最后一项包括 D07 的供应商背景；它核验固定完整来源版本中的上下文，不把背景当作同任务证明。身份正/反证仍需实际 proof 的局部角色成立。没有通过全 UNKNOWN 获得零误合并。

## 4. 三个关键案例的真实 JSON 与原文

以下为实际导出的 [key-results.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/key-results.json) 字段摘要。完整判定分别见 [D01.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/D01.json)、[D02.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/D02.json)、[D07.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/D07.json)，包含完整有向端点、版本/hash、两条原观察、正反证、依赖、读回状态和缺口。

```json
[
  {
    "case_id": "D01",
    "predicate": "SAME_TASK",
    "verdict": "SUPPORTED",
    "subject_source": "B.md",
    "object_source": "A.md",
    "reason_codes": ["EXPLICIT_TASK_REFERENCE"],
    "evidence_validity": true,
    "identity_quotes": ["本条对应A.md的SMS-17，是同一个任务，仅更换称呼，不修订任务计划。"],
    "missing_evidence": []
  },
  {
    "case_id": "D02",
    "predicate": "SAME_TASK",
    "verdict": "REFUTED",
    "subject_source": "B.md",
    "object_source": "A.md",
    "reason_codes": ["EXPLICIT_DIFFERENT_TASK"],
    "evidence_validity": true,
    "identity_quotes": ["这是独立任务，不是A.md的SMS-21。"],
    "missing_evidence": []
  },
  {
    "case_id": "D07",
    "predicate": "SAME_TASK",
    "verdict": "UNKNOWN",
    "subject_source": "B.txt",
    "object_source": "A.txt",
    "reason_codes": ["INSUFFICIENT_IDENTITY", "PROJECT_SCOPE_UNKNOWN"],
    "evidence_validity": true,
    "identity_quotes": [],
    "missing_evidence": ["需要作用域明确且无冲突的任务编号，或唯一指向另一观察的明确同一任务声明；不会补写项目、负责人或日期。"]
  }
]
```

D01 的 B.md `[47,84)` 精确读回其肯定句，再核验 A 的项目/任务编号和唯一文件指向，得到 SUPPORTED。D02 的 B.md `[77,98)` 精确读回独立任务反证，绑定 A 的 SMS-21，得到 REFUTED。对应具体 source version 均在完整 JSON 内。

D07 仍是两条观察：陈晨“短信接口接入/联调，2026-10-14 前”与“按星帆接口调整，2026-10-18 前”。两端字段均能回读，但没有身份肯定/反证；项目缺口与“继续负责”不足以确认同任务，因此 UNKNOWN。未计算日期差，也未认定为两个已确认的独立业务任务。

全部合成原文及不可变来源引用见 [source-context.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/source-context.json)；逐范围读回见 [evidence-readbacks.json](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/dev-output-v2/evidence-readbacks.json)。

## 5. 测试、审阅与回归

- 新增 P1 检查 **55 passed**：[日志](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/p1-release-check.txt)、[XML](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/p1-release-check.xml)。包含 SUPPORTED、REFUTED、UNKNOWN、CONFLICT、NOT_ASSESSABLE，中文/英文系统标签、青岚/海棠/瀚海项目、陆青/方禾等独立人物、不同任务编号、窄摘录/多消息来源，以及重导入和 JSON 往返。
- 既有相关检查共 **223 个用例最终通过**：首次 217 passed，6 个 localhost HTTP 用例因沙箱回环连接被禁止而失败；这 6 项在本轮隔离目录的字节一致测试/fixture 副本上补跑 **6 passed**。不是 229 项，也不是一次全绿套件；初始失败记录保留。见 [原批次](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/m1-memory-regression.txt)、[隔离补跑](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/http-regression-isolated.txt)、[副本及旧证据 hash](D:/webproject/Linkloom/work/linkloom-evaluation-qualification/work/m2-p1-task-identity/acceptance-20261018-141303/http-replay-isolation.json)。
- 覆盖 M1、来源身份/版本、candidate persistence/migration、语义校验/物化、Decision Memory 有效期/历史、正式审核、供应商替代链和 segment recovery。HTTP 副本仅启动并关闭测试自身临时服务，使用 scripted Provider 与新临时库；原运行服务未操作。
- 独立代码审阅最终 PASS，无剩余 Required。先复现并修复条件越过分号、否定复用误判；另以失败测试修正引用谓词挂错端点、未确认身份仍走编号捷径、来源上下文 namespace 遗漏、无编号文件回指忽略未暴露任务。没有修改 Gold 迎合结果。
- M1 七个关键源文件 hash、已有 tracked diff、Git HEAD、旧 incident traces 及独立 Gold 均与基线一致。旧 HTTP 测试的证据输出通过副本位置落在新目录，未覆盖旧实验。
- 无真实 Provider、无保留集执行、无 CI/部署结论；未建关系 SQLite 库，未 commit/push/创建 PR。本轮日志保留，包括测试驱动开发的失败记录。环境 pyreadline3 在部分 pytest 退出后打印既有 console 析构警告；对应最终 pytest exit code 为 0。

## 6. 覆盖边界与失败归因

测试通过率与语义范围分开：55 项是离线工程检查；语义评分只覆盖六个预声明 Dev 固定观察对。真实 Provider 的语义抽取、候选发现质量、端到端 Recall 均未测。H01–H04 未物化/未执行，且设计 Gold 已被开发者见过，未来不能称严格盲测。

当前只识别有责任/任务角色的有限 ASCII 工单键，以及有限中文自指“同一任务/不同任务”表达。复杂双重否定、隐喻、自由第三方身份声明、无显式指代的同义描述、项目别名/同名项目实体消歧，均不属于已完成能力。未知措辞不能用相似度补齐。

无编号文件回指保守限定完整单 segment；窄摘录或多消息来源即使只提供一个候选观察，也不能假定其余任务不存在。namespace 只用于编号作用域核验；多个、未解析或 EvidenceSpan 外的标签阻止默认作用域确认，不推导 IN_PROJECT。调用方仍需提供一致的 source registry 和有界竞争观察清单；模块不证明全库任务、冲突或影响已穷尽。跨第三份资料的身份声明发现/绑定仍待后续流程补充。

语义不足归 UNKNOWN，明确反证归 REFUTED，正反证冲突归 CONFLICT；输入/来源核验失败归 NOT_ASSESSABLE。这些状态不会互相吞并。重新查询来源当前性需再次运行纯函数；本切口没有持久化生命周期事件、撤销接口或系统时序快照。

## 7. 下一切口的具体依赖

- **IN_PROJECT**：项目规范标识/显式别名、有界标题或跨文档桥接证据、独立项目绑定判定。派生归属不回写 M1.project，尤其原案例 B 不能复制 A 的项目。
- **RELATED_TO**：新增有限工作关联类型及方向、两端唯一绑定和实际关联语句验证；已相关不自动升级 SAME_TASK，不执行传递合并。
- **AFFECTED_BY**：先有可核验项目/变更桥；ChangeRef 复用已审核 DecisionRecord/回执或标明 SOURCE_CLAIM 的来源记载；必须有直接适配/阻塞/变更原因证据。未审核供应商变更不能获得正式权威。

三者都需扩展关系 schema/证明类型和各自独立 Gold，再接有界候选发现；P1 当前并未实现它们。计划修订、日期变化及关系持久化继续留在后续切口。

本轮停在 M2-P1 离线验收完成。
