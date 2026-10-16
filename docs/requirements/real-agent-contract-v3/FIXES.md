# V3 生产修复

1. **严格 Provider Uncertain**：`gemini_api._exception_code` 先识别既有 `ProviderOutcomeUnknownError`；`complete` 不再让通用嵌套超时分类将其覆盖为 known_failure。对应三条历史 Adaptive trace，离线测试含字符串 code 和嵌套 Timeout。仍无自动重试。
2. **一次 Final 格式修正**：RetrievalAgent 将既有 TeamDecisionResult 形状校验接入 ModelLoop，非法 JSON/Schema 以 `final_rejected` 持久化。RecoveryPolicy 只允许一次额外模型回合，工具列表为空，完整计入现有请求、上下文与步骤限制；无预算时保留明确 Contract Error，不发请求。再次无效或再调工具停止。冷恢复复用哈希响应，保留修正次数。
3. **来源 Schema 一致性**：model-facing search source_context 声明 Host 已绑定的实际身份字段与固定值；执行器仍拒绝遗漏/错误 Workspace、Index、document_count。没有填入模型未知的父实体，没有改变依赖语义校验。历史四条 ToolInvalid 是 Harness 二次时间戳包装导致关系无法解析，不能宣称本修改修复这四条。

格式检查只决定是否允许新回合，不提供事实，也不接纳答案。最终请求的 durable evidence 可见性、Verified Read、原关系方向、Grounding、Reviewer、Freshness 与 Workspace 校验仍完整执行。排序兼容复用原有引用并集规范化，不补缺字段、不续写截断答案。

## 明确保留

不修改 Grounding 语法，不剥离任意多层转发包装，不将别名当项目，不删除冲突证据。AP-D06 的实体后缀、H04 的缺失 alias hop、V01 的缺失冲突引用继续由原 validator 拒绝。typed claim-proposal Final 暂未增加格式恢复；零工具时 provider 返回 undeclared_tool 仍失败。Query-only 尚未启用。

## 负例证据

针对性测试覆盖一次真实计数的离线模型新回合、再次非法、无剩余预算、伪造事实、provider proposal Final、两个冷恢复断点。HTTP 新测试实际经过 Unified Ask → Runtime → Search/Read → 无效 Final → 新回合 → Grounding → Reviewer → 返回中文结果；模型明确为 OFFLINE_FAKE_MODEL。真实收益以 LIVE_COMPARISON.md 为准。
