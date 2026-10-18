# M2 候选关系自动发现 — 任务 B

基线：`af001a4d968676c30979254ac70dfb750e8f9ad6`。分支：
`codex/linkloom-m2-candidate-discovery`。开发、测试、数据库临时目录与 P4
隔离。只新增任务专属文件；没有修改根 DELIVERY、P1/P2/P3 或 P4 文件。
验收边界见 [SPEC](SPEC.md)，独立审阅见 [REVIEW](REVIEW.md)。

## 实际能力

- 从有界 Workspace 快照或 `TaskObservationQuery.list()` 查询行发现候选；
  重新核验 M1 观察、来源版本/哈希、当前 registry、摘录与结构化字段。
  排除旧查询行、冲突提取、篡改观察、未登记来源及其他 Workspace。
- 支持同项目/命名空间明确任务编号、文件/任务回指、直接项目别名、
  项目范围弱候选及范围内词汇重叠、项目/别名与供应商变更线索。
  修订提示要求回指/编号与修订措辞，日期不同不产生修订提示。
- 候选包含端点 `ObservationRef` 或 `ChangeRef`、`predicate_hint`、排序优先级、
  原文位置及来源引用、发现理由和不确定性。只有
  `DISCOVERED / UNCONFIRMED / SUPPORTING_ONLY`，没有 verdict。
  不创建 `RelationAssessment`，不写关系数据库，不修改观察或业务任务。
- 对称关系按端点/谓词去重；修订保留方向。同一对不同提示可以各占一槽。
  输出稳定，不受输入顺序、重复观察或重新导入时间影响；支持严格序列化回读。
- `revalidate_candidate()` 用新快照重放发现规则，检查端点和全部发现依赖。
  这是使用旧候选的必要步骤，候选 ID 或旧 JSON 不代表当前有效。

默认上限：128 观察、64 来源、500,000 来源字符、128 上下文信号、100 候选、
每观察 10 候选；生成尝试默认最多 1,000 次、绝对上限 5,000 次，在候选构造、
证据哈希和合并前检查。输入/上下文/生成预算超额明确报错，不返回部分结果；
可缩小快照或选择 anchor。输出总量/单端点截断原因、每端点实际扇出、输入数、
字符数及上下文信号数均可见。generated 为生成尝试（含合并），considered 为去重池，
returned 为最终输出。观察的编号/范围/词法等信号只解析一次并复用。
有限快照内采用两两比较，复杂度约为 O(n² + n×变更数)，没有全库搜索/分页。
优先级只是排序规则，不是关系置信度。

## 离线结果

12 个新合成 Workspace 查询，9 个相关端点；相同输入及 K 预算。
Gold 只在两种方法的预测落盘后由评分器读取，并记录输入/预测/Gold 哈希。
Recall 指“值得验证的端点召回”，不是关系真值、Provider 表现或端到端 Agent 成功率。

| 方法 | Recall@1 | Recall@3 | Recall@5 | @5 候选/噪声 | 明确回指漏召回 |
| --- | --- | --- | --- | --- | --- |
| Workspace 内描述归一化精确匹配 | 1/9 (11.1%) | 1/9 | 1/9 | 2 / 1 (50%) | 4/4 |
| 新发现方法 | 8/9 (88.9%) | 8/9 | 8/9 | 16 / 4 (25%) | 1/4 |

新方法 @1 返回 9 个候选、1 个噪声；@5 为 12 个唯一目标、16 个提示候选，
平均每查询 1.33 候选。没有相关目标的 3 个查询中，1 个被弱项目线索污染。
跨项目同名/同编号负例与无项目无关任务负例均未污染；同项目无关任务仍有噪声。

具体 Bad Cases（完整清单见 [bad-cases.json](evidence/bad-cases.json)）：

- **C10 漏召回**：`narrow.md` 的明确回指在观察 raw_text 之外，发现只扫描任务
  摘录中的回指，不把外围的“本任务”默认绑定给某个任务。@1/3/5 均漏掉 `O:old`。
- **C01/C04/C08 噪声**：会议室、办公桌等无关工作与查询处于同一项目，作为弱
  候选进入较大 K；均带 `PROJECT_COOCCURRENCE_ONLY`，不能确认关系。
- **C12 噪声**：同项目办公桌与咖啡机任务仅因项目共现入选，@1 也有噪声。
- **C05 基线误召回**：不同项目的同名/同编号任务描述完全相同；基线召回，
  新方法不召回。

这组样本很小且属于公开开发集，没有独立盲测、代表性估计、延迟基准或
与优化 BM25/embedding 的比较。中英文混合、无 project 后缀的项目变更、
别名链、自由措辞、任务摘录外的回指、未知项目归属仍是限制。
供应商候选必须已有 source-grounded vendor/project 或直接别名，不能补填缺失字段。
原五行例子中第二条任务缺少项目；本模块保守返回零候选。这不是端到端完成证明。

## 验证与复现

- 专属候选/评分边界测试：30 passed。
- M1/P1/P2/P3、来源身份相关单元回归：185 passed。
- 三个来源更新探针（任务/别名/变更）：更新前有效，更新后旧候选无效，3/3。
- 原五行：2 个观察保持未确认；SAME_TASK UNKNOWN，修订 UNKNOWN，
  日期比较 BLOCKED，delta 为 null；没有确认延期。
- 只执行相关离线测试；无真实 Provider、旧保留集、全库回归、CI 或部署声明。
  本机 Python 退出时有环境级 pyreadline 析构提示，相关测试进程退出码为 0。

复现命令见 [fixture README](../../../tests/fixtures/task_candidate_discovery/README.md)。
详细命令和测试计数见 [verification.json](evidence/verification.json)，指标、预测、
更新探针和五行结果均在 [evidence](evidence/) 中。输出为纯合成/公开样例，无密钥、
数据库、私人数据或本机绝对路径。已有临时输出保留在本 Worktree 的 `work/`。
哈希按 UTF-8/LF 文本计算，避免 Git 在 Windows 检出时的换行转换影响复现。

## 文件与 P4 集成

新增生产模块：`src/linkloom/semantic_ingestion/task_relation_candidates.py`。
新增专属测试：`tests/unit/test_task_relation_candidates.py`、
`tests/unit/test_task_candidate_evaluation.py`、`tests/task_candidate_support.py`、
`tests/fixtures/task_candidate_discovery/`。
新增离线脚本：`scripts/evaluate_task_candidates.py`；文档/证据均在本专属目录。

P4 可按下列方式接入，接口不需要本轮修改公共模型：

```python
rows = TaskObservationQuery(store).list(workspace_id)
result = discover_candidates(
    workspace_id, observations=rows, artifacts=current_authorized_artifacts,
    source_registry=store.source_registry,
)
# 使用候选前重新取得当前快照，调用 revalidate_candidate。
# 再将候选端点、完整竞争观察及证据交给现有 P1/P2/P3 判定器。
```

候选不是 RelationAssessment，不能直接按 SUPPORTED 持久化。
`AFFECTED_BY` 保持 observation → ChangeRef、SOURCE_CLAIM；正式决定的
审批权限仍由现有判定器核验。SAME_TASK/RELATED_TO 为无向候选，REVISES_PLAN
有方向。P4 应继续拥有关系评估、写入、生命周期与 schema。
现有 P2 的 RELATED_TO 证明检查带 subject→object 方向；对称去重的候选应把
两个方向分别送入固定端点判定，再由 P4 汇集证据与冲突，不能只检查排序后的一个方向。

需要集成协调：当前来源/registry 的完整快照由调用方提供；带别名提示也仍要
重新建立 P2 前提；发现直接依赖 P1 私有 `_keys/_namespaces/_NAMESPACE/_aliases/
_quote/_sentences` 语法函数。今后可协调提供公共信号读取接口，本轮没有改它们。
候选预算可能遗漏端点，不能把发现结果视为穷尽关系；跨请求没有缓存或持久化。

具体集成探针：将 C02 的候选、完整观察及证据送入现有 P2 两个方向，均为 UNKNOWN。
现有 `task_work_proofs._target` 对引用 `任务 BILL-57 ` 的尾空格没有归一化；
目标是 `任务 BILL-57：`，不满足其精确片段回读。已有 P2 单测中目标为
`任务 GATE-42 的…`，有相同空格，能够通过。本轮不改 P2；若集成需要消除此
格式限制，应协调独立修改并验证公共接口。这个结果没有计成判定成功。

验收范围为独立模块与离线证据；明确保留上述召回/噪声限制。未与 P4 联调，
未合并到固定开发分支，不启动 M3。
独立复核已通过：两项 Required 已修复，当前无 Critical/Required 阻塞；
本轮有界离线模块的验收条件已满足。
