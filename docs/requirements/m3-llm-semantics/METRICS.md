# 作者可见固定端点诊断指标

范围：8 个新 Dev 中预声明的 3 题，7 项关系；5 题真实模型 NOT_RUN。
模型/实现/输入在生成前冻结，预测先落盘、再独立读取 Gold。不是保留验收或端到端 Agent 评测。

| 谓词 | 正例 TP / FP / FN | 正例 Precision | 正例 Recall | verdict 正确 |
|---|---:|---:|---:|---:|
| SAME_TASK | 2 / 0 / 0 | 2/2 | 2/2 | 2/2 |
| AFFECTED_BY | 1 / 0 / 0 | 1/1 | 1/1 | 3/3 |
| REVISES_PLAN | 1 / 0 / 0 | 1/1 | 1/1 | 2/2 |
| RELATED_TO | 未执行 | NOT_EVALUATED | NOT_EVALUATED | 未执行 |
| PROJECT_ALIAS | 未执行 | NOT_EVALUATED | NOT_EVALUATED | 未执行 |
| IN_PROJECT | 未执行 | NOT_EVALUATED | NOT_EVALUATED | 未执行 |

上表仅针对已执行的固定候选正例判断，正例均有完整显式来源；不代表其他谓词或完整 Dev 成绩。
verdict 为 SUPPORTED 4/4、REFUTED 1/1、UNKNOWN 1/1、CONFLICT 1/1。
verdict 正确不自动取得完整证据分。

| 分离维度 | 实测 | 限制 |
|---|---:|---|
| 引用原文精确回读 | 11/11 | 来源真实性；不是蕴含正确性 |
| EVIDENCE_VALID 提案 | 7/7 | 仍 NOT_EVALUATED、publishable=false |
| Gold 完整显式来源覆盖 | 5/7 | REFUTED 少 choice.md；UNKNOWN 无显式引用 |
| label + 完整来源 + 引用有效的 Groundedness | 5/7 | 无模型 confidence 兜底 |
| 条件性日期结果 | 2/2 | 修订 +4；冲突 BLOCKED/null；缺年仅离线契约验证 |
| False Merge 提案 | 0 / 2 个 SUPPORTED SAME_TASK | 未执行真实“明确不同任务”负例，不能证明防误合并能力 |
| False Revision 提案 | 0 / 1 个 SUPPORTED REVISES_PLAN | 另一个修订 query 保持 CONFLICT；来源纠错真实样本未跑 |

旧规则同题 7 个 verdict 中 3 个匹配：两个 SAME_TASK、一个缺因果 UNKNOWN；
修订支持、冲突、直接影响与明确不受影响未识别。该对比使用相同 public 输入和完整有界 M1 inventory，
没有使用 B 候选筛掉端点，没有改写旧规则，没有调用 P4。

“实际发现 Recall”本轮没有测量：任务与查询端点由 public fixture 固定提供，M1 为已验证构造输入。
旧候选发现若漏召回不能据此宣称无关系，本提案模块也不把未返回结果默认为 REFUTED。
新 Dev 剩余的纠错、独立任务、别名、条件和缺年仅有 FakeModel/契约证据，不能加进真实模型分母。

源码 `evals/m3_task_impact` 的旧 Dev/Gold 采用完整 Agent answer 与工具执行回执契约，
与此固定 typed-endpoint proposer 不同。没有伪造生产 Adapter 将本次局部提案包装成 Agent 成绩。
复用其默认 Dev runner 与独立 scorer：12 Dev 全 NOT_RUN / NOT_EVALUATED；基础设施 35 tests passed。
公开 Dev 的未来语义接线需真实发现/M1/模型调用的 Adapter 后再评，不能用预填端点替代发现分母。

可机读指标见 artifacts/semantic-score.json；实际预测、旧规则输出和使用量分别保存在同目录。
本地回执只是审计证据；真实调用由独立 Reviewer 对冻结清单、文件、时序、来源和 SDK 回执交叉复核。
新 Dev Gold 的 required_sources 为文件级集合；自动 Groundedness 采用标签、文件集合与验证报告，
不独立证明每段引文对结论的语义蕴含。quote_authenticity 自动计数也是验证器回执的统计。
本次另由只读 Reviewer 逐条核对原始消息与 Gold；这些自动指标不能替代后续独立语义验收。
