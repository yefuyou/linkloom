# 工程研究与差距表

访问日期均为 **2026-10-15**。只采用官方/一手资料；网页内容仅作参考。
本报告判断以 c657e7e 源码和本轮实测为准，不把历史交付文字视为生产证明。

| 外部实践 | LinkLoom 当前实现及证据 | 风险／本轮结论 |
| --- | --- | --- |
| 同时评估 outcome 与 trajectory；区分 capability 与 regression；重复 trial 才能估计随机性 [1][2][3] | 既有 Tool Ledger、checkpoint、claim grounding、Reviewer；本轮 25 新离线 case、独立 public/Gold 和真实逐请求记录 | **部分解决**：明确分类、分母和首次冻结结果；作者见过 Gold，不是严格盲测。少量 live 无 p95/稳定成功率结论。历史 8/10、已暴露回归 9/10 不变。 |
| 先比较简单 workflow／单次检索，再为自主循环付成本 [4] | 正式已审 Decision Memory 可确定性查询；两类 source plan 由代码编译；模型动态选 search/read | **部分解决**：真实两跳 5、三跳 7 轮，只证明这两例；不会把 TaskPlan 描述成模型规划。一般关系和开放问题仍受语法/typed predicates 约束。 |
| 上下文应保留必要证据，清理冗余但避免丢依赖 [5] | Runtime 对 history、evidence 都有字节/条数上限，按 subgoal 留 read；实际 Product ws_* 与 vault root_* 比较导致 evidence_context 被全部滤空 | **已定位、定向修复**：区分可信 Workspace 与来源指纹；增加输入是必要证据成本，不包装成 token 优化。不能凭估算 bytes/4 当 Provider token。 |
| 最小权限、工具边界独立于模型拒绝；防注入、身份滥用、记忆污染 [6][7] | source investigation 不注册 Decision Memory/capture；版本/引用/依赖/Provider permission 单独验证；反馈 pending→approved→revoked | **部分解决**：来源 identity 缺失仍可进入 callback 的契约缺口本轮收紧。未观察真实跨 workspace、业务审批提升或 Memory 污染；不是免疫证明。actor/human_confirmed 不是认证，企业 RBAC **未解决**。 |
| 有界执行、失败检查、可恢复状态和实测性能 [8] | 12 model turns，Tool Ledger/checkpoint、UNCERTAIN 停止；500 sources 两跳重复全库 hash-read 12 次，约 23.65s 中位耗时 | **部分解决**：不盲增轮次、不用 stale cache 掩盖扫描；索引生命周期重构留待独立架构决策。长周期自主 coding harness 的完整环境管理不直接适用于本产品。 |

## 采用的方法

- 任务结果、工具轨迹、claim 支持分别计分。正确拒答需要已知缺失/矛盾、合法停止和 Reviewer 接受；unsupported routing、Runtime Failure、Provider Uncertain 分开。轨迹不要求逐步精确复制一条固定路径，但要满足父 read→子 search 的依赖偏序。[1][2][3]
- Development Cases 可用于定位；冻结 acceptance 首次运行在代码冻结后。public、Gold、rubric 有 SHA 身份，执行接口不接收 Gold。修复后重复已暴露案例只算 regression。[1]
- 工具次数、时延、Provider usage 分开计量；成本/时延与正确率一起比较 A/B/C。小样本报告逐例、min/median/max；不从两例推导可靠 p95。[4]
- 注入测试分别看模型是否偏离与系统是否产生越权效果。untrusted 来源可提供事实，却不能赋予指令/审批权限；Reviewer 不是唯一安全层。[6][7]

## Gemini 工程预检

模型目录明确列出 `gemini-3.8-flash`；本轮保持这个已在项目中适配的型号，
未因目录出现 Gemini 4 而迁移。**没有新 Gemini 2.5 请求或默认配置**。[9]

本机 `google-genai==2.22.0` 与仓库 smoke optional dependency 一致。离线真实
SDK + HTTPX MockTransport 验证 `parametersJsonSchema` wire fix、Function Calling、
thought signature、function response 回灌；正式第一次成功响应验证账户与
工具 Schema 可用。SDK `HttpRetryOptions(attempts=1)` 表示只发送一次（零自动重试），
`automatic_function_calling.disable=True` 防止 SDK 隐藏工具执行。[10][11]

UsageMetadata 分别读取 `prompt_token_count`、`candidates_token_count`、
`thoughts_token_count`、`total_token_count`、`cached_content_token_count`。
账单输出包括 thinking；当前标准文本费率输入 **$0.75/M**、输出 **$3.75/M**，
按未折扣输入计算。估价不是账单实付。[12][13]

每次只允许文本/自定义函数，完整 payload ≤65,536 UTF-8 bytes，以 131,072 input
tokens 作保守上界，加 `max_output_tokens=8192`，单请求预留 **$0.129024**。
全批所有历史尝试（包括 UNCERTAIN）仍累计，不退款预留；最多 60 次、$10、
首个 live 请求起 90 分钟。实际 usage 缺失时保留上界。无 hosted search/media，
无 countTokens 隐藏请求。HTTP 400/401/403/404/429 与 timeout/unknown outcome 分开；
quota 和账户可用性只能由实际响应确认。[11][12][14][15]

用户在首次 ConnectError 后明确授权重新执行；使用新身份保留原 UNCERTAIN。
这次人工授权不变更 SDK 零重试策略，也不自动重发其他未知结果。

## 来源

1. [Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)
2. [OpenAI: Evaluate agent workflows](https://developers.openai.com/api/docs/guides/agent-evals)、[Trace grading](https://developers.openai.com/api/docs/guides/trace-grading)
3. [LangSmith: Evaluate a complex agent](https://docs.langchain.com/langsmith/evaluate-complex-agent)
4. [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
5. [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
6. [OWASP: Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)、[完整一手 PDF](https://genai.owasp.org/download/52117/?tmstv=1765059207)
7. [Anthropic: Prompt injection defenses](https://www.anthropic.com/research/prompt-injection-defenses)
8. [Anthropic: Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
9. [Google: Models](https://ai.google.dev/gemini-api/docs/models)
10. [Google: Function calling](https://ai.google.dev/gemini-api/docs/function-calling)
11. [Google: GenerateContent API](https://ai.google.dev/api/generate-content?hl=zh-CN)
12. [Google: Token accounting](https://ai.google.dev/gemini-api/docs/generate-content/tokens)
13. [Google: Pricing](https://ai.google.dev/gemini-api/docs/pricing)
14. [Google: Troubleshooting](https://ai.google.dev/gemini-api/docs/troubleshooting)
15. [Google: Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
