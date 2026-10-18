# M2-P4 关系侧库、生命周期与可验证查询

日期：2026-10-18。基线 `af001a4d968676c30979254ac70dfb750e8f9ad6`；固定分支 `codex/linkloom-m2-progress`。实施边界来自用户确认的 P4 任务及根目录三份 M2 设计。仅使用合成资料、显式临时 SQLite 路径和离线规则。本地离线验收、独立复核与隔离发布复现通过；不代表 CI、部署或真实 Provider 能力。停在 P4。

## 文件与调用链

| 文件 | 职责 |
| --- | --- |
| `src/linkloom/semantic_ingestion/task_relation_store.py` | 独立 SQLite writer；不可变 assessment/comparison、首捕获保留、校验和、幂等和追加事件事务。拒绝初始化其他业务数据库。 |
| `task_relation_lifecycle.py` | 五类生命周期、显式前驱与记录时间、稳定事件 ID；精确保留原提案范围的 replay recipe；不另存 RawArtifact 文本。 |
| `task_relation_query.py` | mode=ro/query_only、每次读的依赖闭包验证、P1/P2/P3 重放、项目派生查询、比较和有限修订链投影。没有跨调用有效性缓存。 |
| `tests/unit/test_task_relation_store.py`、`test_task_relation_query.py` | 重开/重复、非法事件、不可变、来源/观察/ID 篡改、撤销传递、边界状态、计划分叉和未来生效。 |
| `tests/integration/test_task_relation_authority_readback.py` | 真实临时正式审核流程；回读回执；SOURCE_CLAIM 权限隔离；关系撤销不写正式决策。 |
| `tests/task_relation_lifecycle_support.py`、`tests/integration/test_task_relation_lifecycle_dev.py`、`tests/fixtures/task_relation_lifecycle/` | 独立 Dev 标签、D04/D07/D08 和新的 Redwood 版本/撤销序列；真实 SQLite 写入和读回。 |
| `scripts/evaluate_task_relation_lifecycle.py`、`scripts/run_relation_checks.py` | 先生成实际预测再加载独立 expected；新 P4 输出目录，不覆盖旧日志。 |

调用链：`现有 M1 观察 + P1/P2/P3 验证结果 → 显式 TaskRelationStore.put_assessment/put_comparison → 不可变 payload + replay locators + 初始 ACTIVE 事件 → RelationQuery 的 SQLite 只读快照 → 外部 M1/Source Registry/原文/审核回执重验 → 递归前提核验 → 当前已证实、有效非肯定、历史/不可核验三类输出`。

P1 v1、P2 v2、P3 v3 的原始 JSON 和 ID 不重写。嵌入的 TaskObservation 和 EvidenceSpan 是原判定的不可变审计快照；侧库没有可修改的 Task 主表，不能用内嵌快照替代当前 M1。查询必须再提供外部观察或 TaskObservationQuery，按版本化 ObservationRef 和语义 payload hash 对照。没有外部来源目录或观察时，记录可诊断、可查看历史，但不能发布当前确认关系。

## SQLite 与事件边界

四张表：`task_relation_metadata`、`task_relation_assessment`、`task_relation_event`、`task_revision_comparison`。记录包含工作区、端点、规则版本、完整原 payload、依赖和完整性 hash。原 payload 中既有的证据原文完整保留；不另建来源正文表。额外的提案仅保存原依赖 span 的 hash、精确字符起止、角色与片段 hash，重建时不扩大范围。

写入必须显式传入文件路径，不默认打开 LinkLoom 原库；查询使用 URI `mode=ro`，或调用方传入的 SQLite 连接并设置连接级 `query_only=ON`。查询不初始化 schema、不追加动态 STALE 事件、不触发 Provider、不物化 DecisionRecord。跨 Decision Memory 与侧库不假设分布式原子事务：调用方提供明确的来源/观察快照，侧库读在一个读事务中捕获，审核接口仅重读。

| 状态转换 | 规则 |
| --- | --- |
| 首捕获 → ACTIVE | writer 自动追加；只说明记录存在，不表示 verdict 为肯定。 |
| ACTIVE → STALE / RETRACTED / REPLACED / INVALIDATED | 明确事件，含操作者、原因、前驱和有时区记录时间。 |
| STALE → RETRACTED / REPLACED / INVALIDATED | 允许终止或新判定纠错；不能重新 ACTIVE。 |
| RETRACTED / REPLACED / INVALIDATED → 任意状态 | 拒绝；重复提交同一原事件是幂等 no-op。 |

事件 ID 对完整事件内容确定性计算；SQLite 写事务检查当前前驱，拒绝旧前驱、记录时间倒退和非法转换。REPLACED 必须指向已存在、同工作区、同谓词且 ACTIVE 的新判定，并有相同的端点来源锚点；观察允许同 artifact/ordinal 的新版本，SAME_TASK 允许对称方向，不能将另一任务借同谓词伪装成纠错。完全换来源或段落的情况需显式新评估与撤销，本轮不自动解析跨来源替代身份。业务计划的 REVISES_PLAN 与判定纠错的 REPLACED 分开。更新/删除触发器禁止 API 和普通 SQL 原地覆盖；重复写旧判定不会再追加 ACTIVE 或恢复撤销关系。完整 hash 用于损坏检测，不是数字签名；持有任意文件替换、删触发器权限的恶意管理员不属于本轮认证边界。

## 当前验证与查询语义

只有 `verdict=SUPPORTED + persistent_lifecycle=ACTIVE + evidence_state=VERIFIED + 全部前提有效` 才有 `current_supported=true`。原 JSON 中 lifecycle/evidence_validity 保持历史内容，不作为当前发布依据。UNKNOWN、REFUTED、CONFLICT 的有效记录为 CURRENT_ASSESSMENT；不能由 ACTIVE 变成肯定。

每次查询重验来源 workspace/version/hash/精确 EvidenceSpan、外部观察 payload、原提案范围，并重放现有评估函数。SAME_TASK 先验证；REVISES_PLAN 再用真实 SAME_TASK 与原提案重放；比较再调用 P3。项目归属递归验证 PROJECT_ALIAS。AFFECTED_BY 为 REVIEWED_DECISION 时重新调用 P2 的真实回执/候选/替代链只读核验，SOURCE_CLAIM 继续仅有来源声明权威。依赖缺失、撤销、跨作用域、循环或超出有界深度均阻断；当前目录将同一 artifact 的多个版本都声称 CURRENT 时拒绝选择。

旧 replay recipe 固定的 context 不遮蔽调用方新增的当前反证：先精确重放旧 ID，再加入真实、当前的外部 M1 观察做语义防护重验。若发现编号复用等改变结论，返回 CURRENT_CONTEXT_CHANGED_REASSESSMENT_REQUIRED 和 DIAGNOSTIC_ONLY 的新上下文核验结构，不发布或持久化新关系。无关当前观察只增加依赖而未改变语义时，不会令所有旧关系失效。TaskObservationQuery 返回的 NOT_CURRENT / extraction_conflict 也独立保留，不能从原观察快照中抹掉。

接口：

```python
with TaskRelationStore(explicit_sidecar_path) as writer:
    writer.put_assessment(identity)
    writer.put_assessment(revision, evidence=original_proposals)
    writer.put_comparison(comparison)

with RelationQuery(explicit_sidecar_path, artifacts=source_snapshot,
                   source_registry=registry, observations=m1_observations) as query:
    query.confirmed(workspace_id, observation_id=observation_id)
    query.list(workspace_id, project=project, include_history=True)
    query.comparison(workspace_id, comparison.comparison_id)
    query.plan(workspace_id, observation_id, as_of=explicit_calendar_date)
```

`list` 默认返回当前有效判定（含非肯定）；`confirmed` 只返回已证实。`get` 按 ID 始终提供留存诊断；`include_history=True` 的列表包含失效、撤销及替代原因。项目过滤联查有效 IN_PROJECT / PROJECT_ALIAS，不依赖 M1.project 字面字段，不回写原观察。缺少关系不证明任务不存在。

历史结果标明 CAPTURED_RECORD_WITH_CURRENT_VALIDATION，不声称重建任意过去时点数据库。过去来源快照也不能撤销当前人工事件；没有另一个独立固定全量状态快照，就不称其为“当时有效数据库视图”。历史 comparison 原 +4 保留，当前比较必须重新验证，失效则 BLOCKED/null。

## 最小计划投影

仅沿已证实有向 REVISES_PLAN 检查有限链，无任务身份聚类或任意多跳。分叉、环、同端点相互矛盾的生效日期声明返回 CONFLICT；不按 assessed_at、导入顺序或 assessment ID 选赢家。明确未来计划不会覆盖已明示生效的前序计划；没有业务生效日的链终点仅称 declared_terminal / EFFECTIVE_TIME_UNKNOWN，effective_plan=null。日期计算仍调用 P3。

合法的显式判定替代能消除已纠正旧判定对新链的阻断；旧判定和旧比较继续以历史状态保留。未知、明确无修订、不可比与失效分别保持 P3 的 BLOCKED / NOT_APPLICABLE / NOT_COMPARABLE 语义，不能统一替换成 0 或肯定结果。

当前链仅由 current_supported 边扩展；历史失效边保留诊断，不污染另行重验通过的新版本链。请求只落在失效链上且没有有效修订时为 EVIDENCE_INVALID。两种导入顺序的独立白榆项目变体均证明：3 月 3 日保留已于 3 月 1 日明示生效的计划 B，3 月 5 日才生效的计划 C 只列 future_plans；不能按导入顺序提前选择 C。

## 实际验收与失败记录

独立 Dev 语义验收为 **53/53 原子检查、4/4 案例家族**，不同于 pytest 数量，也不是原关系 Precision/Recall 的新成绩。完整逐字段判定及分母见 [metrics.json](evidence/metrics.json)。最终 P4 聚焦 **29 passed，exit=0**，已有相关 M1/P1/P2/P3/Decision Memory / 正式审核回归 **285 passed，exit=0**，共 314 个不同测试；没有修改这些既有生产模块或旧 Gold。独立复核 **PASS**，保留两个 Required 的初审失败和复核；实际脱敏探针见 [review-probes.json](evidence/review-probes.json)。从 P3 已提交基线归档加 P4 源码/测试清单重建隔离副本，P4 **29 passed in 109.91s，exit=0**，Dev 再现 **53/53**，metrics 完整相同，未借用原工作区未提交的无关文件。

| SQLite 实际读回家族 | 检查分子/分母 | 关键结果 |
| --- | --- | --- |
| [D04](evidence/D04-plus-four.json) | 11/11 | +4 可计算；身份撤销后当前 BLOCKED/null，历史 +4 保留。 |
| [D07](evidence/D07-unknown.json) | 6/6 | SAME_TASK/REVISES_PLAN 仍 UNKNOWN，比较 BLOCKED/null。 |
| [D08](evidence/D08-derived-project.json) | 9/9 | M1.project=null 的观察通过有效别名被项目查询找到；撤销后不再发布，历史原因可读。 |
| [新 Redwood 序列](evidence/REDWOOD-version-retraction.json) | 27/27 | s0 真正当前有效的 +2；s1 旧关系 ACTIVE/STALE，新纠错为 REFUTED/NOT_APPLICABLE；s2 旧身份 RETRACTED，不复活，历史 +2 保留。 |

[integrity.json](evidence/integrity.json) 记录 72 项受保护源码/fixture/旧证据字节未变，以及无关 tracked diff 完整保留。独立 input SHA256 为 `11c0c2ec6a2f3c5e2e5bb3a2b583d7257879fffdde2d3eb1e89e9f6ac4cc66f5`，expected 为 `cc0bd36f118895db39a333f7a37da974a5a0cdea0381cd32950868630b8235ae`。被测 loader 不读 expected，独立 evaluator 在实际预测写出后才读标注。H01–H04 未执行，不宣称盲测。

所有 RED 及失败日志保存在新 `work/m2-p4-task-relations/acceptance-local/`，脱敏归因见 [failure-records.json](evidence/failure-records.json)。已复现并修复：REPLACED 历史误阻断有效新计划、显式 as_of 漏验原比较、合法 REFUTED 比较被误改 BLOCKED、同端点冲突生效日期按 ID 选取。另保留一个测试设置错误：RawArtifact 的版本由内容生成，没有 source_version 构造参数。Dev 初版的目录相位与仅检查历史 payload 的覆盖缺口单独记录，不通过改标签规避。独立审阅问题和复核见 [REVIEW.md](REVIEW.md)。Windows 的已有 pyreadline3 在 pytest 成功后输出 console 析构警告；所有上述最终测试进程退出码为 0，没有掩盖测试失败。

复现使用新 label 和新输出目录：

```text
python scripts/run_relation_checks.py --stage p4 replay-p4 tests/unit/test_task_relation_store.py tests/unit/test_task_relation_query.py tests/integration/test_task_relation_authority_readback.py tests/integration/test_task_relation_lifecycle_dev.py
python scripts/evaluate_task_relation_lifecycle.py --output work/p4-replay-unique
```

## 仍未实现的边界

没有自动候选发现、Agent/Planner/Runtime 接线、GraphRAG、图数据库、UI、真实 Provider 或通用双时态引擎。不声称找全项目任务。当前性基于调用方显式提供的有界来源/观察目录及此侧库事件；不保证其他进程的跨库同时刻状态。规则仍是 P1/P2/P3 已声明的有限证据表达。

M2 最终整体验收仍需先固定候选/证明输入和实现版本，再由独立验收运行保留集（本轮 H01–H04 未执行）；区分固定候选效果和端到端候选召回，串联身份、项目、影响、修订、生命周期与最终答案。进入 M3 前还需明确只读工具适配、工作区授权与来源快照装配，以及有界调查/失败输出的验收；P4 不自动接入 Agent 或进入 M3。
