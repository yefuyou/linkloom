# M3-O 离线交付

基线 `44a1f3b4a822d2e64a8e50cf6b826072e466badf`，分支
`codex/linkloom-m3-o-quality-convergence`。A/B/C 的离线工程验收已完成，独立
只读审查最终 **PASS**，两项 P1、四项 P2 全部闭合。正常提交和推送后的 SHA
由最终交付消息及本地 `work/m3-o/GIT_DELIVERY.json` 记录。真实 Provider 与
countTokens 请求数均为 **0**。
`REAL_MODEL_SEMANTIC_EFFECT = NOT_EVALUATED`。

## 实际改动

- F 评测：`src/linkloom/evaluation/task_profile_ab.py`、`task_profile_score.py`
  和 `scripts/run_m3o_ab.py`。专用于冻结的 13 对请求；执行和评分分开；默认
  Mock、精确请求 Replay、关闭的 LIVE；独立持久预算先预留再发送，未知结果停止。
- G/Host：`semantic_ingestion/task_relation_question_planner.py` 接收 Host
  执行能力说明；`ui/task_investigation.py` 生成真实能力和可执行 inspect 绑定；
  `tools/task_relations.py` 经原 Runtime 执行目标读取，产生且重验 COMPLETED
  回执。观察仍为 UNCONFIRMED，不产生业务确认。
- 准入：`ui/ask_admission.py` 把敏感词检查缩至未引用的明确命令；任务拒绝器
  `semantic_ingestion/task_discovery_veto.py` 不再把人名内部的“若”当条件；
  `text_scopes.py` 共享已有引号/代码范围扫描，保留原文偏移。
- `.gitattributes` 保证新冻结 JSON 字节和参与代码哈希的 LF 字节可跨 checkout
  复现。未改变历史 F Prompt 内容、原始 Canary 包、旧账本/Gold/响应/成绩。

## A/B 准备

`ab-v1/` 包含来源、独立标签、允许的显式续述分组、评分规则、统一配置和
SHA-256 清单；代码哈希同时绑定 Extractor、投影/拒绝规则、评分器和执行脚本。
9 条已知开发案例、3 条公开保留案例（不是盲测）、原始五消息失败案例。
请求中没有标签或分组评分规则。原始模型输出保留，按原始消息工作单元评分，
提案数量另列；Host 校验后单独计分，格式失败、Host 拒绝和模型遗漏不混算。

模型版本 `gemini-3.8-flash`；相同 Schema、3072 输出上限、low thinking、
省略 temperature、每来源每组一次、无自动重试。只改变旧 F Prompt 与
`task-discovery-cross-message/v1` 提示和消息上下文。

拟运行 26 次，每次保守预留 USD 0.079872，总预留 USD 2.076672，授权上限
USD 3。费用使用冻结费率上限假设，不是实时价格报价。未来需单独人工授权精确
manifest、模型、费率、请求数、费用和带时区有效期，并安装固定 SDK 2.22.0。
LIVE 预算固定在独立的 `.linkloom/m3o-ab-v1`；换输出目录不能重置预算。
预留未完成、未知 Usage/Thinking、模型版本不符、重试、过期或超限均停止。

离线命令（工作树根目录，输出目录必须新建）：
```
python scripts/run_m3o_ab.py --output work/m3-o/my-mock
python scripts/run_m3o_ab.py --mode replay --replay work/m3-o/my-mock --output work/m3-o/my-replay
```
未来获得独立授权后才可执行：
```
python scripts/run_m3o_ab.py --mode live --enable-live --grant work/m3-o/human-grant.json --output work/m3-o/my-live
```
本轮没有创建或批准 LIVE grant。Mock 主要返回空候选，仅对已知失败来源在两组
复用同一旧输出，因此模拟分数不是模型质量。历史严格三来源回放仍为 4/5，
字段 24/24、Evidence 5/5。M3-I/L/M 真实业务完成状态未被改写。

## G/Host 能力矩阵

| 操作 | 可规划/Host 准入 | 实际调用与回执 | 状态 |
|---|---|---|---|
| inspect | 单个授权观察、可执行无时间范围步骤 | 原 Runtime → inspect_task_context → Tool Ledger；COMPLETED、来源绑定 | 新增最小接线；UNCONFIRMED |
| discover | 可提出缺失目标 | F 已作有界单次扫描 → UNKNOWN 回执 → 有界 G 重规划 | 不二次扫描，不证明不存在 |
| relation | 仅就绪、类型合法、无不支持的来源/时间范围 | analyze_task_semantics → 原始语义提案 + 证据有效性回执 | 没有独立语义确认，不解锁前提 |
| compare_dates | 类型计划及身份/修订依赖 | query_task_relations 能读取已有 P4 可信比较 | F/G 不生成所需已确认关系，正确阻断 |
| clarify | 可规划 | 可见缺口；无对话工具 | 不宣称可执行 |

同一 Owner 问题不额外 discover，inspect 通过实际原始结果生成步骤回执。
混合 inspect/relation 计划也只返回 inspect 绑定目标；计划外 anchor 被拒绝。
已有 result_verifier 可消费经 Tool Ledger 重验的 inspect 回执；没有增加自动
产品重规划循环。当前自动重规划仍仅处理 F 的 UNKNOWN 回执。来源版本变化会
使回执失效，关系 UNKNOWN/CONFLICT/REFUTED 不满足前提。独立可信语义确认并
写入 P4 的服务、额外业务授权或新 Runtime 均为 OUT_OF_SCOPE。

## 正则回归

最小 RED 复现六个只读问句误拒绝和两个人名误拒绝，GREEN 后为 0。
包括“大家为什么讨论绕过权限”、引用危险指令、历史删除记录、复合只读问题、
“周若负责日志迁移”。明确执行写入/取密钥/绕过权限的八条新控制全部拦截，
此前 27 条写入控制、M3-J 取消/讨论/条件/引用合同及 Fast Path 同时复测。
有界输入的性能检查使用子进程和 8 秒超时。

限制：命令句与名词短语的任意歧义仍可能需要澄清；句首本身类似条件词的人名
不在本次最小修复的保证内。旧 source-relations Planner 保留自身更严格范围
规则。没有增加另一套语义正则分类器、独立 LLM 分类器或工具权限。

## 验证和独立审查

最终精简证据见 `evidence/verification.json`；阶段原始记录位于工作树
`work/m3-o/PHASE_A.md`、`PHASE_B.md`、`PHASE_C.md`。
中间失败、完整 XML、Mock/Replay 请求响应及临时测试文件保留本地，不整体提交。
不同测试批次重叠，不累加为唯一用例数：

- 新 A 评测合同最终 13 通过；B 首轮 92 / 产品链 70 通过；C 首轮 171 /
  HTTP 与 Canary 94 通过；最终混合计划及 Host/工具/产品链 34 通过。
- 最终受影响 60 文件首轮 937 通过、5 失败；五项均为过长 Windows 临时路径
  导致的 `MODEL_ARTIFACT_WRITE_FAILED`，使用新短路径精确复跑后 5/5 通过。
- 全量尝试 2834 通过、241 失败、23 跳过、4 个收集错误。精确复跑后 14 项
  临时环境失败通过，227 项仍保留；分类只是诊断，不证明每项均为历史问题。
  缺失录制、旧 Provider 合同/Schema 预期和 Windows 路径相关问题不在本轮
  扩展修复；完整节点清单见 `evidence/full-suite-failures.json`。
- 四个已知收集错误来自缺失的 MemoryAgentBench G3 / Stage C 脚本；未编造
  脚本或关闭断言。当前 Schema 与指定基线相同的核对见精简证据。
- Mock/Replay A13/B13：请求参数和 Schema 相同、Profile 指纹不同、精确回放
  成功；原三来源严格 Replay 4/5、字段 24/24、Evidence 5/5，未提升历史分数。
- compileall、`git diff --check` 通过；ruff 未安装，未声称通过。无 CI/部署结论。

独立审查两轮要求修改，共两项 P1、四项 P2；补最小复现和源码修复后最终
PASS。闭合记录见 `REVIEW.md`。上述保留问题不被“离线验收”覆盖为全仓库通过。

## 最小后续动作

1. 单独批准冻结 A/B LIVE 的模型、manifest、预算和有效期，再评估语义质量。
2. 需要依赖执行时，先明确独立语义确认进入已有可信 P4 的产品授权边界。
