# 真实 Gemini 对照

新批次仅用实际返回 `model_version=gemini-3.8-flash` 的模型；兼容预检1请求，Canary6请求（含1次模型规划），累计7后进入三组预先安排的配对。全程串行，SDK总尝试次数1（自动重试0），未调整 token 上限、重试或查询策略。

各组使用同一冻结合成 SQLite、workspace/source IDs、来源版本、输入文件、规则计划和模型参数。旧代码来自原始 Adaptive commit `37e45b1`，新代码仅增加 Contract 修复。`result.variant=A` 表示规则规划；Contract 旧/新轴来自独立 `phase=A/B`，不混淆。配对文件哈希及计划相等已经离线复核；后补强制准入 guard 防止未来运行输入不一致，未重放真实试验。

| 场景 | 旧 → 新 | 请求 | 实际工具调用 | 已知 Usage 估算费用 |
|---|---|---|---|---|
| 正常两跳 | COMPLETED → COMPLETED，严格评分均通过 | 5 → 5 | 4 → 4 | $0.038648 → $0.046142 |
| 合法 Partial | INSUFFICIENT → INSUFFICIENT，严格评分均通过 | 4 → 4 | 3 → 3 | $0.026315 → $0.025082 |
| 冲突 | HTTP 503 FAILED → PROVIDER_UNCERTAIN | 4 → 6 | 3 → 5 | $0.012909 → $0.027235，双方各另预留 $0.079872 |

成功的两组均通过现有 scorer 的事实、计划、Grounding、Reviewer，以及保存的 Verified Read ledger/citation 审计。冲突两侧都未形成可评分 Final，不能用于比较成功率；工具调用数来自保存的 Runtime ledger，不能将失败报告中空 trace 当作零调用。

可比较的两组均为2/2，不能证明可靠性提高。总请求9→9；已知费用约$0.064964→$0.071224（约增加9.6%），累计输入 tokens 44,548→46,505。单次随机模型样本无统计结论，也没有在线观察到非法 Final 被修正后成功。格式恢复的有效性来自离线可重现故障注入，不冒称真实模型失败已全部解决。

新契约真实遇到严格不确定后，HTTP产品层正确返回 PROVIDER_UNCERTAIN，没有变成历史中的普通 FAILED，且没有重试。实验到此结束，不继续付费寻找好结果。共35请求，已知33次Usage费用$0.22179075；未知Usage两次（503及严格不确定）预留$0.159744；预算占用$0.38153475/$4，pending=0，严格不确定累计1（硬停止阈值2）。未开启 Query-only Stage B，未启用默认 Adaptive Planning。

机器记录见 LIVE_EVIDENCE.json：实际请求身份、原账本、code/input SHA、逐项评分、运行状态、provider账目、读取记录及比较限制。Gold只由运行后的评分器读取，未传入 Provider。此处均为新合成来源，非外部盲测、真实用户数据或20项冻结集成绩。
