# M3-S1：歧义任务调查的 Product 闭环

用户本轮完整任务即批准边界；固定基线 bde9d9e2b88c2e69132cf48d1534dc28d4fca764。

目标：原 M3-S 歧义合成资料的负责人、截止日未知时，正常 Product 有界规划、执行合法调查、
展示 UNKNOWN/来源/缺口，通过 Reviewer、真实 Chromium 和冷读安全验收。

实现边界：现有 InvestigationPlanner 的模型输入投影、Host 有界 discover/replan 状态，以及必要结果展示。
优先使用已有 typed handle、canonical hash 和来源引用机制去重；Host 保留完整计划和校验，不提高预算上限。
不改 F 字段机制、P4、ChangeRef、历史 Gold/Frozen/M2 评分或 CI 门禁，不另造 Planner/Runtime。

验收固定：
- 保存的原始 G 请求/响应离线重放，复现初次成功、重规划发出前 INPUT_BUDGET_EXCEEDED；量化载荷。
- 同来源/问题的压缩重规划在原token24576及byte24000边界内；Workspace、完整来源、版本/hash、端点、依赖和回执仍验证。
- discover没有新增信息只记录 UNKNOWN，不宣称完成实体发现；最多一次重规划，不能重复无益发现或无限循环。
- 不依赖缺失信息的合法任务读取仍可执行；owner/deadline UNKNOWN贯穿结果和UI，不能从日期提及推导截止日。
- 原问题的真实 F→G→Runtime→Reviewer→Product；关系步骤存在时使用现有 SemanticAnalysis，模型提案不成P4。
- 原文打开、刷新、新进程冷读无新增模型请求；撤权、版本更新、跨Workspace后失效结果不再显示。
- 独立只读Reviewer P1/P2为零，且歧义Product LIVE验收通过，才能标记PASS。

实施顺序：失败重放/RED → 最小载荷引用投影/GREEN → 停止条件及HTTP合同 → 固定LIVE预算/输入 →
真实Chromium → 冷读安全 → 独立复审 → 授权范围内提交/非强推；不创建PR。

本轮首次计划上限10个新Provider请求、$3程序预留、输入1,000,000/输出65,536 tokens、600秒。
前三次真实响应暴露保留步骤ID冲突后，固定LIVE_RECHECK作一次修复后复验；请求上限调整至12，
总费用仍$3，Token/总调用时长上限不变。原LIVE_CASE、来源、问题、预期和原收据不改。
沿用此前69请求/$19.43保守预留，项目累计$50上限不重置；LIVE前复核其他新增收据。
仅公开合成资料，Gemini3.8Flash/SDK2.22/low，串行、请求前持久预留、SDK attempts=1。
新单次超时60秒（仍有总时长预算），UNCERTAIN后立即停止所有后续付费，不重试未知请求。
账单/赠金/计费项目如无法核验保留未知，不把预留或未知描述成实际$0。
