# M3-R 交付记录

正常 Product 已真实调用现有 LLM-first SemanticAnalysis。旧固定语法不再是新候选调查的
强制门槛；P4 仍只读取已有正式证明。这次完成的是非权威语义结果闭环，尚未建立独立
语义复核或认证关系批准，不能宣称正式任务修订和延期已经成立。

## 真实产品

正常 HTTP Product、真实 Chromium、公开合成来源、单次授权。LIVE run
`ui-987f33c9c7cf`；实际工具按序为 `inspect_task_context`、`query_task_relations`、
`analyze_task_semantics`。新语义请求 response ID `W0vZaonZEIyEuMYPnLnjoAw`，
输入 3437、输出 302、总计 3739 tokens，约 3.3 秒。

- SAME_TASK：模型 SUPPORTED，两个来源的引用有效，覆盖完整；仍为 MODEL_PROPOSED。
- REVISES_PLAN：模型 UNKNOWN，只引用后一来源，未覆盖原计划来源；证据层阻断，缺少正式替代或截止日期信息。
- Reviewer `evidence_sufficient`，只表示实际执行与来源/回执检查通过；语义仍 NOT_EVALUATED。
- 没有 P4 写入，没有发布可信日期差值。负责人和日期字段仍有 UNKNOWN，F 出现 OWNER_UNGROUNDED，不能把它们算作可靠业务字段。
- 修复了执行过语义但混合候选被整体 BLOCKED 隐藏的问题。UI 现在显示 PARTIAL、有效提案及未通过候选的缺口；未经核验的引用和端点不作为证据显示。
- 最终 Chromium 冷读：2 个提案、原始来源打开成功、再次读取成功、页面 errors=[]。使用无 Provider 的新进程，不能产生额外模型请求。

原 LIVE 浏览器在 UI 修复前看到阻断状态；最终 UI 验收复用该真实产物冷读，未重新烧一次模型预算。
截图见 [product-cold-ui.png](evidence/product-cold-ui.png)，实际收据见 [live-summary.json](evidence/live-summary.json)
和 [product-semantics.json](evidence/product-semantics.json)。本地完整数据库和 Runtime 产物保留于
`work/m3r-live-product-20261022/`，未提交数据库。

## 语义质量与历史

[new-cases.json](evidence/new-cases.json) 在查看模型结果前固定来源及预期；
[new-case-results.json](evidence/new-case-results.json) 保留实际输出和失败。

| 新案例 | 真实结果 |
| --- | --- |
| 不同自然语言描述同一交付安排 | SAME_TASK SUPPORTED；REVISES_PLAN SUPPORTED |
| 同负责人、独立工作 | SAME_TASK REFUTED |
| 进度汇报，排期不变 | SAME_TASK SUPPORTED；REVISES_PLAN REFUTED |
| 提议后撤回且未实施 | SAME_TASK SUPPORTED；REVISES_PLAN REFUTED |
| 两份纪要正反相争 | SAME_TASK SUPPORTED；REVISES_PLAN CONFLICT，保留双方证据 |
| 间接依赖且影响尚未评估 | AFFECTED_BY UNKNOWN |
| 关键附件缺失 | SAME_TASK UNKNOWN；REVISES_PLAN UNKNOWN |
| 授权/版本变化 | 请求前 BLOCKED、回读 EVIDENCE_INVALID，额外请求 0 |

七个付费语义案例的 verdict 均符合预先固定预期，引用校验通过；这只是小样本诊断，
不是产品成功率或泛化成绩。间接影响案例最初的 Harness 为 ChangeRef 填入原文没有的
project，产生 CHANGE_FIELDS_NOT_IN_SOURCE，真实请求 0；保留该失败，补正为原文已经
存在的组件范围后只调用一次，来源文本与预期未改。不能把这个前置失败归咎于模型。

H01–H04 历史 0/4 不变。本轮沿用 M3-Q 的真实诊断证据：H01 模型可理解修订而旧 P3
UNKNOWN；H02 超时，仍无有效模型结论；H03 模型保留 CONFLICT，旧 P3 漏正证；H04
模型区分纠错与修订，完整历史/生命周期绑定仍未完成。本轮修复的是正常产品不能
调用/展示已有语义能力，没有声称修复这四项原始可信业务评分。未修改 Gold/Seal/Frozen。

## Provider 与预算

模型 `gemini-3.8-flash`，`google-genai 2.22.0`，low thinking，SDK attempts=1。

| 范围 | 请求 | 成功 | 已知失败 | UNCERTAIN | 预算预留 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 已有 M3-Q 报告 | 32 | 24 | 5 | 3 | 约 $11.87 |
| 本轮 Product（F2/G1/Runtime4/Semantic1） | 8 | 8 | 0 | 0 | $0.300037 |
| 本轮新语义诊断 | 7 | 7 | 0 | 0 | $1.75 |
| 累计 | 47 | 39 | 5 | 3 | 约 $13.92 |

本轮 usage：输入 79,939、输出 4,049、SDK total 86,156 tokens（total 含额外思考计数，
不能机械等同 input+candidate output）。所有新增请求串行、发出前落盘预留，无自动重试。
以向上取整的 $13.93 占用计算，$50 上限尚余至少 $36.07。实际账单金额未知，不能视为 $0。
继续使用既有本机授权 API key 和 Google 默认 SDK 端点；generation 回执不暴露所属计费项目，
本轮没有独立取得计费项目/实际扣款证明，不能声称账单核验通过。无新增 Gemini 2.5 调用。

## 工程与限制

- 受影响专项及安全回归：173 passed；[regression.xml](evidence/regression.xml)。包含错误 Workspace、来源撤权/更新、热读/冷读、伪造引用与 SUPPORTED、Reviewer 拒绝、Provider 失败/UNCERTAIN、预算及 P4 前提。
- 最终能力元数据与安全展示补充复核：70 passed（与上述批次重叠，不累加为覆盖数）；JS syntax 与 `tests/ui/unified_ask_dom_test.cjs` 全部通过。没有重复运行未受影响的全仓库套件；远端 CI 未运行。
- 独立审查结论见 REVIEW.md；只有 P1/P2 关闭后才提交。
- 基线与上游远端回读仍分别为 `224d2d8…`、`67b91a15…`；PR #6 open、Draft=true、merged=false；未修改 master 或 PR。
- 目录 `work/m3r` 使用独立 Git 元数据和新分支。由于原仓库 `.git/worktrees` 写权限拒绝，采用独立隔离检出，未改动原 M3-Q 工作目录；没有强推、reset、rebase 或破坏性清理。

下一步最有价值的三项：F 的负责人/日期角色证据召回与字段落地；可审计的独立语义复核；
经认证的任务关系批准/撤销合同及其 P4 接入。当前任务结果的首屏问题摘要与缺口翻译也仍有改进空间。

复现：在仓库根目录设置 `PYTHONPATH=src;.`。evidence 中三个 runner 均保留原始输出目录的
存在性检查，禁止重复付费运行；仅在明确选择新的预算和输出目录后进行新的 LIVE 实验。
预算、实际输出和文件 hash 收于 [manifest.json](evidence/manifest.json)。
