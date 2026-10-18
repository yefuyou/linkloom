# M3 跨文档任务影响评测准备

本任务由用户指令授权，基线 `af001a4d968676c30979254ac70dfb750e8f9ad6`。
只新增 `evals/m3_task_impact/`，不修改生产模块、旧 Gold、冻结实验、数据库或根 DELIVERY。

## 行为和边界

准备 16 个新的合成业务问题（12 Dev、4 保留），先标注事实、原子关系、修订、拒答和必要证据，再实现离线评分入口。
问题覆盖供应商变更后的任务、负责人、明确的计划截止变化，以及身份、项目、版本和权威不确定性。
沿用 M1 观察与字段语义；沿用 M2 的 SAME_TASK / RELATED_TO / AFFECTED_BY / REVISES_PLAN / IN_PROJECT / PROJECT_ALIAS 及正反证边界。
SOURCE_CLAIM 不等于已审核决策。不根据日期先后合并任务、确认因果、选当前计划或制造修订。

## 架构和契约

`data/<split>/inputs.json` 只含原始资料、问题和来源目录快照。无候选对、任务列表、Gold、预期动作。
`gold/<split>.json` 含作者标注与逐项必要证据，仅供离线 evaluator。
运行器只接收 public case 与受限读取工具；固定 Scripted adapter 只用于 Dev 链路检查。
生产 M3 adapter 未接入时输出 NOT_RUN，评分为 NOT_EVALUATED，指标为 null。
先落盘 prediction/trace，再启动独立评分步骤读取 Gold。

评分使用现有确定性 metrics、bad-case taxonomy，以及 RawArtifact / EvidenceSpan 的完整性核验；新增部分限于 M3 的结构化答案比较。
现有 trajectory grader 面向固定工具断言、P2 runner 面向固定候选，均不能代替任务发现；本轮不改动它们。
归一化观察引用 `doc_id:消息序号(1-based)` 是评测适配层 locator，不是业务 task ID，也不修改 M1 ID。
变更声明使用独立类型 locator `change:doc_id:消息序号`，即使任务和变更共用原文，也不能混用端点角色。
当前快照的 tasks 只统计 CURRENT 观察；可读旧观察在 Gold discovery_exclusions 明示，仍可用于历史关系及阻断理由。
状态由 runner 持有，来源目录的 current 字段只表示版本当前性，不表示业务权威。

## 验收

1. 16 个问题有可读的 Gold 理由和必要原文证据；Dev/保留来源、项目和 case ID 不交叉。
2. Task Discovery Recall、Relation Precision/Recall、Task/Owner/Deadline Accuracy、Evidence Groundedness、False Merge/Revision、Abstention Accuracy 可独立评分，输出分子/分母，零分母为 null。
3. 关系 TP 必须方向、端点、状态及必要证据全部正确；漏发现仍计 FN。错误正断言同时计 FP 和 FN。
   所有修订比较状态均须有对应身份与修订关系判定，不能跳过前提直接给 BLOCKED/NOT_APPLICABLE。
4. 版本/hash/workspace/范围/原文不符、缺桥接证据、额外关系及虚假日期变化能被离线测试识别。
5. 未运行/缺接口不计任务成功或失败；工具轨迹与实际执行回执不匹配时不得宣称执行成功。
6. 成本 token/金额未知保持 null；记录工具动作、终止状态、失败阶段、adapter 和输入指纹。
7. 独立审阅数据歧义、证据缺口、Gold 泄漏及 runner 是否实际调用目标动作。
8. 只运行离线单测和固定 Dev Scripted；不调用 Gemini，不执行或评分保留集，不声称 M3 已实现。

## 验证计划

新目录内 pytest：指标反例、证据篡改、遗漏/额外预测、拒答、无执行及执行错误、工具回执、Gold 隔离和数据完整性。
只对 Dev 执行 Scripted 链路与缺接口路径。保留集仅静态标注完整性核验与人工审阅。
无新增依赖、数据库或生产功能。集成压力点是未来 M3 输出适配、M2 发现召回及 P4 持久来源快照；此次保持接口显式且不猜测它们。
