# M2-P2 Task Relations Dev fixtures

本目录只物化 M2-P2 关系评测的 Dev 输入与独立 Gold。输入包含 D03、D06、D07、D08 和 V01–V07；D01、D02、D04、D05 的任务身份基线仍在 `tests/fixtures/task_identity/`，H01–H04 不在本目录。

输入与 Gold 严格分离：

- `inputs/dev.json` 是公开输入，schema 为 `task-relations-p2-input/v1`。它只含合成来源原文、已经用 M1 `TaskProposal` 形状核对的观察、问题端点、有限的候选证据，以及必要的 scope/authority 请求；问题不含 verdict、解释、Gold reason 或预期日期差。
- `gold/dev.json` 是 evaluator-only Gold，schema 为 `task-relations-p2-gold/v1`。它按 `case_id` 和 `question_id` 记录 verdict、解释、reason codes、证明/反证证据、权威期望和必要的 revision comparison。Gold 不应被公开输入 loader、检索目录或模型提示读取。

每个问题的 `evidence` 都是未受信任的候选证据，引用使用 `source_id`、`segment_index` 和有限原文 `quote`；实现必须重新读回 workspace、来源版本和原文边界。`segment_index: null` 且 `scope: "artifact"` 只用于 D08 的标题/上下文证据：它表示由 fixture builder 额外注册的整份 artifact EvidenceSpan，不是 M1 任务观察 segment，也不改变 M1 API。端点使用以下固定表示：任务为 `{type: "task", label}`，项目为 `{type: "project", workspace_id, name}`，供应商变更为 `{type: "change", source_id, segment_index, before, after, project}`，审核变更可再带 setup-only `decision_id`。`authority` 只表达问题要求检查的层级：`REVIEWED_DECISION` 或 `SOURCE_CLAIM`，不是审核结果。

D06 的 `review_decision_setup` 只有合成来源到决策记录的设置引用和替代链关系，没有 `approved: true` 等伪造权威字段。正式 D06 运行前，测试 harness 必须通过现有审核流程生成并回读合法回执；仅有原文“正式”或该 setup 对象不能把来源 claim 提升为已审核 DecisionRecord。

D07 保留 M1 的原始五行语义边界：A2 的 `project` 是 `星河项目`，B2 的 `project` 保持 `null`；B2 的截止日期仍是观察自身的 2026-10-18，不从 A2 复制或自动推出延期。D08 的 B3/B6 也保持 M1 `project: null`，项目归属只作为 M2 派生关系输出。

D08 依照设计保留同一 B.md 中的 Markdown 标题与 timestamped speaker 混排。当前 timestamped-text parser 会将标题和非 timestamp 行作为部分解析上下文（B3 的 segment 还会覆盖后续标题，结果为 `PARTIAL`）；这是已知的 parser 边界，不能通过改写原文或给普通 Markdown 补负责人来掩盖。关系实现应按原文的标题顺序和下一个同级标题计算作用域，不能让 `# 银梭` 泄漏到 `# 天河项目` 下的 B6。普通 Markdown 变体 V01/V07 使用无 speaker 原文，M1 不凭文档内容补 owner。

V01 测试同一普通文档中的多项目标题作用域，V02 测试显式别名不能跨 workspace，V03 测试明确无影响，V04 测试候选证据引用已不在当前来源中的旧句，V05 测试同一供应商但没有直接因果，V06 测试直接因果但仅有未审核 `SOURCE_CLAIM`，V07 测试明确跨文档项目桥。V04 的旧句故意不在当前 source snapshot 中，预期是证据失效/不可评估，不能用当前“供应商现为星帆通信”句替代旧版本证明。

本次只完成 fixture 和 Gold 的冻结前物化：没有调用 Provider、没有访问或修改真实 Obsidian vault、没有执行 holdout（H01–H04）评测，也没有把 Gold 用作生产输入。正式运行前仍应由独立审阅者核对每条原文、证据边界、Gold hash、split 和审核回执设置；在实现/阈值固定后，holdout 只运行一次。
