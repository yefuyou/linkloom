# M3-S2 交付

从固定基线 `bde9d9e2b88c2e69132cf48d1534dc28d4fca764` 修复 F 的任务发现与
日期输出协议，分支 `codex/m3s-task-recall`。Product LIVE F 使用 `task-recall/v3`。
G、Runtime、SemanticAnalysis、P4、M3-S1、冻结评测、master 和 PR #6 未修改；不创建 PR。

原始收据证明 F04 是模型整条漏提议，H02 是日期 Token 混入“前”；不是来源投影失败。
原 F05/F06 在旧 veto 下误拒，v2 已保留。详见 [归因](AUDIT.md)。

原 M3-S 全部 16 组、17 条预期任务，历史 v2 真实响应与本轮新 v3 真实请求对照：

| 指标 | 原 M3-S v2 | 本轮 v3 |
| --- | ---: | ---: |
| 任务召回 | 16/17 | 17/17 |
| 误召回任务 | 0 | 0 |
| 字段正确接受 | 31 | 32 |
| 正确 UNKNOWN | 32 | 36 |
| 字段误接收 | 0 | 0 |
| 字段拒绝 | 1 | 0 |
| 因整条遗漏而缺失的字段位 | 4 | 0 |

这是新旧真实响应的有限对照，包含模型随机性；不是对历史输出的自动修补，也不保证普遍召回。
F04 四个空字段已随任务保留；H02 日期为 `2029-07-06`，边界词仍在角色引述里。

独立预先固定的 [12 组中英文案例](cases.json)，在首个调用前固定输入/预期 hash，
最终 [hash 重验](evidence/fixed-inputs.json) 一致。每版均有 8 组可评分：

| 指标 | 新案例 v2 | 新案例 v3 |
| --- | ---: | ---: |
| 预期任务/召回 | 4/4 | 4/4 |
| 非任务案例/发生误召回 | 4/0 | 4/0 |
| 字段正确接受/正确 UNKNOWN | 6/10 | 6/10 |
| 字段误接收 | 0 | 0 |
| 不可评分案例 | N01–N04 | N01–N04 |

N01–N04 的旧版响应因未知 PROPOSAL 类型整批解析失败；新版对应实验 TLS known
failure，无模型输出。两次失败都保留、不重试、不计为正确 UNKNOWN。对成功批次，
无效日历值没有被接受，月日精度没有补年；撤回、背景、建议和引用案例均未误召回。
未取得完整 12 组新案例的有效 LIVE 泛化分数。

原件、请求/响应 ID、usage、prompt/schema/config hash、来源与逐字段结果在
[LIVE 结果](evidence/live-results.json)、[预算](evidence/live-budgets.json)、
[汇总](evidence/live-summary.json) 与 [原件 hash 清单](evidence/request-response-manifest.json)。
公开归档省略 opaque continuation，原始未删文件在本工作树 `work/m3s2-*`。

Product 的一次真实请求通过普通 Host/单次来源授权完成 F1/G1，准备产物有两条观察：
N01“离线索引重建”保持 owner/deadline 等四字段未知；N07“兼容矩阵核验”保留苏遥和
`2036-04-17`。新进程加载 TaskObservation 后来源验证成功，实际 F ModelRequest 的
profile 与 Schema hash 均已核对，见 [真实 Product 接入](evidence/product-real-preparation.json)。

该验收脚本误用离线测试的 3 秒 HTTP 客户端超时，F/G 成功后客户端已断开，完整 LIVE
Ask/Runtime 没有完成，冷读返回 SAVED_RESULT_NOT_COMPLETE。未重发该付费请求。
脚本已改为 180 秒。使用完全相同真实 F/G arguments 做明确标注的离线重放，脚本
Runtime 正常执行 inspect_task_context/query_task_relations，HTTP 得到 PARTIAL；
Reviewer 接受执行证据。无 Provider 新进程冷读与原 observations 相等；跨 Workspace
409，合成来源更新后冷读 BLOCKED 且隐藏旧 evidence/trace/task_review，额外付费 0。
见 [离线 Product 重放](evidence/product-offline-replay.json)。这证明 F 观察能进入 Product，
不冒称新的完整 LIVE Runtime 或 M3-S1 歧义重规划验收。

真实新请求共 16：15 个 Provider SUCCEEDED（其中一个输出 MALFORMED）、1 个已知
TLS FAILED、0 UNCERTAIN、自动重试 0。配置 gemini-3.8-flash / google-genai 2.22.0 /
low thinking / SDK attempts=1。已返回 usage：输入 38,995、输出 13,559、total 52,554；
失败请求没有返回 usage，不按 0 计费。每请求先落盘预留，本轮预留 $4，上限 20 次/$5；
累计已包含 M3-S1 的 10 次/$2.50，为 95 次/保守预留 $25.93，低于原 $50。
检查容量时还计入 S1 尚未用完的现有分配。预留不等于实际账单；计费项目及账单未知。

验证：新增协议与字段/投影焦点测试 80 通过。任务、来源、历史/冻结兼容相关 549 个
不同用例全部取得通过记录；首次 544 通过/5 子进程 UTF-8 环境失败，修正执行环境后
分批复验。中途还记录一次本地 3 秒 HTTP 超时和新增测试误把 Gemini nullable 映射后
Schema 当成原 Schema 比 hash 的失败；最终两项通过。失败收据均保留，详见
[逐用例最终状态](evidence/test-reconciliation.json) 及对应 XML。不是宣称首次整批全绿。
全单元集收集受基线缺三个 MemoryAgentBench 脚本阻断，未扩大范围修复。
没有 CI、部署或真实浏览器验收声明；本轮核心范围使用 HTTP 与冷读证据。

实现与 F 接入行为已经验证。剩余限制是 N01–N04 独立对照不可评分以及完整 LIVE Ask
未完成；本轮不继续重试付费请求。独立复审及最终 Git 结果见 REVIEW.md 和交付消息。
