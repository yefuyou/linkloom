# LinkLoom M3-A 交付

本轮实现独立的 LLM-first 关系提案与来源验证边界，并完成三次真实 Gemini Smoke。
没有接入产品 Agent，也没有将模型提案写入 P4。语义质量结论仅适用于作者可见的合成固定端点诊断。
工程边界验收通过；真实语义评测为部分执行，完整 Groundedness 为 5/7，不能宣称全 Dev 或生产验收通过。

## 固定版本与边界

- 起点：`26392432dcef70692461e6f2f50bc1bd5528f69a`。
- 冻结实现：[04a446d10494a2f6d61ea1f0a745a3ce9f1e8d0c](https://github.com/yefuyou/linkloom/commit/04a446d10494a2f6d61ea1f0a745a3ce9f1e8d0c)。
- 独立 Worktree：`work/linkloom-m3-llm-semantics`；分支：`codex/linkloom-m3-llm-semantics`。
- 模型与实际返回版本均为 `gemini-3.8-flash`，prompt `task-relation-semantics/v1`，validator `task-semantic-evidence/v1`。
- [冻结清单](artifacts/freeze.json)绑定输入、schema、精确请求、核心代码、连接脚本和 Provider/guard 等依赖；[验收输入/Gold hash](artifacts/evaluation-freeze.json)在生成前记录。

新增三个语义模块，复用 ModelProviderAdapter、M1 ObservationRef、现有 Gemini Provider 与 SDK builder。
旧 M1/P1/P2/P3/P4 语义、Coordinator、ToolRuntime、UnifiedAsk、正式数据库及旧 Gold/历史预测均未修改。
用户授权“没有上限”；本轮自行限定三次串行生成，SDK attempts=1，无重试、无扩量、无观察结果后的提示词/规则修改。

## 行为和真实调用链

授权快照及固定查询 → admission/精确请求预算授权 → `SemanticProposer`
→ `GeminiProviderAdapter.complete` → 既有 SDK `models.generate_content`
→ 单个强制结构化函数输出 → host typed refs → `MODEL_PROPOSED`
→ 当前来源/引用验证 → `EVIDENCE_VALID` 或 `EVIDENCE_INVALID`。

模型解释同任务、工作联系、直接因果、业务修订、项目归属/别名，以及否定、条件、冲突、纠错和缺证。
程序验证 workspace/授权、当前版本/hash、M1 payload、Unicode 精确范围、撤销和正式审批回执。
旧 regex 判定没有作为模型语义的必经门槛。日期边界解析只负责日期精度与日历日计算。

引用真实不会自动得到 SEMANTIC_ACCEPTED。生产输出的 semantic_status 始终 NOT_EVALUATED、publishable=false。
SOURCE_CLAIM 即使存在直接影响也不取得正式决策权威。独立语义复核或评分不等于 P4 发布授权。
[结构化实际预测](artifacts/predictions.json)同时保留原模型输出、host 引用、验证报告、条件性日期结果和 Provider 回执。

## 三题真实诊断

| 输入 | 旧规则 | 冻结 LLM | 日期 |
|---|---|---|---|
| 新措辞明确撤换业务计划 | SAME_TASK 支持；修订 UNKNOWN | SAME_TASK、修订均支持 | 条件性 `+4` 日 |
| 三条消息分别明确直接影响、明确不受影响、缺少因果证据 | 三条均 UNKNOWN | SUPPORTED / REFUTED / UNKNOWN | 不适用 |
| 两来源互相否定对方计划效力 | SAME_TASK 支持；修订 UNKNOWN | SAME_TASK 支持；修订 CONFLICT | BLOCKED，null |

模型 verdict 与作者 Gold 一致 7/7；7/7 通过结构化引用契约，11/11 显式引用可精确回读。
但因果 REFUTED 仅引用 work.md，UNKNOWN 只有缺证说明；两项未给出 Gold 要求的完整显式来源集合。
因此完整显式来源覆盖为 5/7，不能用 verdict 正确或 EVIDENCE_VALID 掩盖引用覆盖缺口。

其余 correction、independent、alias、conditional、missing-year 五题真实模型均为 NOT_RUN。
它们完成了离线输入/M1/契约验证，FakeModel 不构成语义成绩。尤其真实 False Merge 的负例、来源纠错、
项目别名和条件句尚未得到本轮真实模型评估。原 H01–H04 未重新当作保留成绩；M3 holdout 未运行。
这不测真实候选发现 Recall、M1 自动抽取质量或端到端生产 Agent 能力。

## 使用量和成本

三次请求均有实际返回：输入 12,358 tokens、候选输出 1,193、总计 13,551。
thinking/cache 明细为 null；没有将未知值当成零。完整成本估计与账单金额均为未知。
按已报告输入/候选输出和标准单价计算的参考值为 **USD 0.01374225**，不是完整费用或实际账单。
详见 [使用量](artifacts/usage-accounting.json)、[只读模型检查](artifacts/provider-preflight.json)。
单价来源：[Google 官方 Gemini 3.8 说明](https://ai.google.dev/gemini-api/docs/whats-new-gemini-3.8)。

## 验证与已知失败

- 最终新增聚焦契约：22 passed；包括真实 quote/错误语义探针、跨 workspace/越权、TOCTOU、撤销、
  伪造审批回执、完整性声明、重叠引用、无输出/错工具/自由文本留痕、冻结依赖变化与新 schema 的既有 Provider 路径。
- 新离线评分器：11 passed；未执行/Fake、无有效回执、重复/额外案例不得取得真实业务成绩，
  已执行但无效输出保留为漏判，四状态和各谓词指标按分子/分母汇总，撤销证据不给 Groundedness 分。
- M1/P2–P4 回归及当时新增契约：189 passed（177 项既有回归、12 项当时新契约）。
  后续改动仅为新模块审阅修复，最终 22 项聚焦重测已通过。
- 扩大检查 P1 与旧 Provider：62 passed / 83 failed。独立复核确认旧文件与固定起点一致；旧测试模型 ID、
  temperature 预期与当前 Gemini 3.8 配置冲突，另有旧 nullable-schema wire 精确快照不一致。本轮没有修补这些既有失败。
- 初次 RED 为缺少新模块；首轮出现一个 M1 日期证据范围接口错误，真实调用前已修复并重测。
- Windows pyreadline3 在 pytest 退出后产生 Console destructor 警告，单列记录，没有计为断言通过。

详见 [验证摘要及原始本地日志 hash](artifacts/verification.json)。完整原日志保留于独立 Worktree 的 ignored `work/`，
提交摘要避免个人目录与测试凭据文本。没有观察到本次七项输出中的虚构来源或错误 verdict；样本量不足以证明普遍安全。

现有 `evals/m3_task_impact` 35 项测试通过；原 Dev runner / scorer 在未提供生产 Adapter 时
分别得到 [12 NOT_RUN](artifacts/m3-dev-not-run.json) / [12 NOT_EVALUATED](artifacts/m3-dev-not-evaluated.json)。
其完整 Agent answer/发现与执行回执契约不同于本轮固定端点提案，未伪造 Adapter 把局部结果当成 Agent 成绩。

各谓词 Precision/Recall、状态、误合并/误修订及来源覆盖详见 [指标说明](METRICS.md) 与
[可机读评分](artifacts/semantic-score.json)。独立复核见 [REVIEW](REVIEW.md)，
离线脚本和复现命令见 [REPRODUCE](REPRODUCE.md)。

## 接线建议

最小 Agent 接线是：授权有界快照构建器 + 既有预算 guard + proposer + 当前证据复查 + 独立语义验收策略。
B 候选只安排优先级，不能删掉竞争上下文或把未召回任务判为无关系。完整性声明由调用方显式承担。
未来 P4 需要独立版本化 semantic receipt、语义验收回执、显式 promotion 与撤销依赖查询分派，
避免在查询时回到旧 regex replay；本轮只研究建议，未实现迁移或写入。
详见 [接口与 P4 建议](INTERFACES.md)。
