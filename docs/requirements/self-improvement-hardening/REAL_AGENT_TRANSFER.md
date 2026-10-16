# 实际 Agent 策略迁移

`ExperimentalQueryStrategy` 的 relation_terms 作用于 Host 生成的依赖查询，采用已核验的父实体；这部分可以迁移。V1 的 relation_candidates/first_hit 则修改 `SourceOnlyController` 的 Pending Reads，没有 Controller 时 Gemini 不会自动执行相同筛选。因此 V1 的整体效率收益属于 FakeController 策略收益。

V2 的 `StrategyRuntimeEngine` 仅在实验子类中调用原 `_build_runtime_agent_adapter` 工厂，再为该实例注入查询策略和观测代理，不修改生产默认值，不做全局 monkeypatch。原 Runtime、ToolRuntime、Ledger、Grounding 和 Reviewer 完整保留。所有候选仍可读取；不在 Host 端静默删除冲突证据。

真实 Gemini 的阅读优先级通过已有 ModelTurnRequest recovery_guidance 提示，属于自愿建议；limit 也不能被描述为真实模型强制遵守。Search quote 不被升级为可信 Evidence，最终声明继续要求 Verified Read。实际查询、Search/Read、引用支持、Provider 请求和用量各自记录。

真实阶段显式启用，模型仅限 gemini-3.8-flash。新独立批次最多120次 GenerateContent、估算上限$6、三小时、串行、SDK零重试。先兼容性和提案，再实际 Runtime Canary；12次内不能通过独立评分即停止。每次发送前追加预留；缺 Usage 保留费用上界。严格未知不重发同题，累计两次停止整个付费批次。原始请求/响应/Ledger 仅保留 ignored work 目录。

预登记真实 A/B DEV 案例：003正常、018排名干扰、014同文件多关系、024冲突、021缺失、011中文来源；每题 Baseline 与 V1 routing 按固定顺序执行，不挑选最好结果。真实结果以 METRICS.json 实际记录为准，未执行项必须标记 N/A。

## 实测

兼容性与提案实际成功，模型版本回执为 `gemini-3.8-flash`。Gemini 提出两个新配置：relation_terms/relation_candidates/6、literal/relation_candidates/4，均经过 Schema 和独立离线评分。加上 Codex 的 query-only 配置，共一轮三个生成候选，没有第二轮修复。

Canary 通过后共完成11个真实任务、64次生成（2次兼容/提案，62次 Runtime）。第六类中文题的 Baseline 回执缺少 output Usage，原 Ledger halted；未继续该题的 V1 试验。没有重放，严格 Outcome Uncertain 为0。费用是用量估算而非账单：已知约$0.508542、缺失预留$0.0584085、总上界$0.5669505。

完整的五组配对结果：

| 指标 | Baseline | V1 查询+阅读建议 |
|---|---:|---:|
| 任务正确（包括正确 partial） | 2/5 | 0/5 |
| Wilson95% | 11.8%–76.9% | 0%–43.4% |
| 工具 / Verified Read | 24 / 14 | 24 / 14 |
| 输入 token | 201,580 | 187,387 |
| 输出 / Thinking token | 4,161 / 19,678 | 3,776 / 18,914 |
| 序列化上下文字节累计 | 770,406 | 724,935 |

已执行的11项中，1项完整正确、1项正确 partial；6项 TEAM_DECISION_CONTRACT_ERROR，另有 MODEL_RESPONSE_MALFORMED、MODEL_ADAPTER_FAILED、MODEL_RESPONSE_UNSUPPORTED 各1项。后者是模型产物/协议失败，不是 Planner Unsupported，也不能归为检索漏召回。Search/Read 与最终产物的失败必须分开。

候选丢失了正常题和缺失题的原正确结果；调用数量没有下降。token 降低伴随正确性下降，不能算可信优化。样本很小、每题仅一次、阅读建议也不是强制策略，因此没有建立真实 Agent 稳定收益或因果提升；live gate 明确不具备接纳资格。耗时仅为观察值，不是隔离负载下的性能基准。
