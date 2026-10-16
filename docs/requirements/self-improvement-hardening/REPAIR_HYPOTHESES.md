# DEV 定向修复假设（Final 前登记）

初始 32 例 DEV 对照中，V1 routing 从 Baseline 23/29 提高到 25/29，但让原来通过的 v2-dev-011、012 失败。不得用整体提升覆盖这两例退化。

第一轮假设：英文 relation_candidates 在 FakeController 内过滤了可被现有 Grounding 解析的中文关系。保留 relation_terms 查询，但 read_mode=all，可能恢复中文支持，同时保留查询改写的排序收益。风险是额外读取导致上下文和逻辑轮次增加。允许 Gemini 在既有白名单内独立提出配置；此假设不把具体答案或 Gold 提供给模型。

退出条件：任一 Baseline 正确案例退化、冲突召回下降、安全违规，或工具/上下文开销超过预设 10% 上界，即拒绝。若有限空间无法满足，不扩展任意源码修改能力。不以 Final 反馈修复。

## 封存后的 Trace 复核

初始“中文漏读”假设仅部分成立。dev-012 的目标候选覆盖1、Read覆盖0.5；dev-011 的候选和Read覆盖均为1，却以 WRONG_ABSTENTION 失败，原 Runtime 明确报告已验证关系链满足全部 subgoal。这是控制器路由/证据消费失配，不能归因为源文档没召回。query-only 配置恢复两例，但这不证明更复杂的控制器根因已经被自动修复。此复核未改变任何候选，未重跑 Final。
