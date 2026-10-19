# M3-D 独立只读审阅

结论：**PASS，可交付；未发现 P1/P2 阻塞缺陷。**

审阅者：独立子 Agent `/root/integration_review`，按项目规则使用 gpt-5.6-luna / max。
主 Agent 负责实现及实际验证；审阅 Agent 只读代码、SPEC、A 接口和实际证据，没有修改文件。
审阅覆盖本轮集成差异，不将脚本接线验收等同于独立语义验收或生产模型成绩。

## 首轮问题及修复

| 首轮发现 | 修复与证据 |
| --- | --- |
| P1 原文解析 PARTIAL 仍可 READY | 每个原始 artifact 必须 COMPLETE 且 issues 为空，PARTIAL/FAILED 请求前阻断并报告解析状态 |
| P1 无 artifact 的 BLOCKED 可伪造接受状态 | 完整固定诊断投影校验，拒绝语义接受/发布性/身份/计数等变更 |
| P1/P2 Reviewer 未对账前两项回执 ID | 必须传入当前 run 的耐久 Ledger；核对全部回执、参数、顺序、状态、值、同任务身份，并重新投影整个结果 |
| P2 语义分支覆盖旧 P4 限制项 | 保留 BOUNDED_TOOL_RECEIPT_PROJECTION、UNKNOWN_IS_NOT_BUSINESS_REFUTATION，同时披露模型提案限制 |
| P2 回放 source_commit 含义混淆 | 来源实现提交从冻结 core_commit 读取并与 freeze.git_base 对账，A 导入提交另列 imported_from_commit |

新增 `tests/integration/test_semantic_review_guards.py` 的 17 项回归覆盖这些问题，
包括原文未解析块、伪造接受、回执跨 run、参数/顺序/状态/值变更及根结果的 COMPLETE/可发布伪造。

## 最终复审

独立 Reviewer 确认：

1. 原始授权快照、完整竞争观察、Typed 端点、版本/撤销/预算与来源覆盖检查正确，不读取 Gold。
2. 模型提案保持 MODEL_PROPOSED、SEMANTIC_NOT_EVALUATED、publishable=false，日期计算仅为条件性结果。
3. exclusive reservation、fsync、响应先落盘、缓存与冷恢复、未知结果不重发符合边界；
   外层 ledger/observation 提交窗口安全失败的限制已披露。
4. 现有 Runtime、注册、权限、CallBoundExecutor、模型循环和 per-run 回执审阅正确，未建立第二套框架。
5. 来源/冻结/导入提交、精确请求/Schema/hash 可追溯；126 项聚合通过、124 项旧回归通过和
   14 项既有失败、compileall/diff-check 均与证据一致。

保留一个低优先级观察：独立调用内部 `BoundSemanticAnalysis.review` 时，artifactless BLOCKED
的非空 errors 文本不是独立重新诊断的结果。它不会改变状态、发布性、Workspace 或计数；
正式 Runtime Reviewer 对完整 Ledger 值对账会拒绝文本篡改。后续若将该内部 helper 独立暴露，
应继续携带原始诊断回执。当前唯一发布路径包含 Ledger 对账，因此不阻塞本轮交付。

## 架构检查

正确性由原文/版本、覆盖、耐久身份和结果状态分别检查；权限仍由原 ToolRuntime 执行。
原始快照构建与耐久语义调用各归其工具模块，A 继续拥有语义数据模型、Prompt、解码与证据验证。
Reviewer 只读，不调用 Provider 或写 P4。来源、Token、请求和显示输出有明确预算；
没有增加数据库、依赖、Router、Agent，也没有修改 ContextAssembler 或正式 Decision Memory。

本结论只覆盖用户授权的离线集成验收。独立语义策略、生产 M1/关系问题发现、真实 Provider 新执行、
P4 模型 Receipt/Promotion 仍未实现或未运行，见 [DELIVERY.md](DELIVERY.md)。
