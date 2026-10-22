# M3-S2 补验边界

用户在 `56732e316229744b6e2ebc60e68b2e22e1e6f510` 交付后再次要求“继续”，
本补验仅补齐该轮剩余证据。原接受标准、案例/Gold、代码及历史结果不变。

新增独立批次最多 8 请求、预留 $2：原 N01–N04 固定资料各一个 v2/v3 F 请求，
以及一个使用已修正 180 秒 HTTP 客户端的普通 Product LIVE，最多 6 请求。
这两项是用户继续指令下的人工开启补验，不是原失败请求的自动重试。每项一次；
SDK attempts=1，失败或 UNCERTAIN 停止所属阶段，不循环到成功。

不释放历史未知/失败预留；当前已知累计 95 请求/保守 $25.93。启动前重读原始
S1/S2 账本，并计入 S1 尚未消耗的现有分配，保持原累计 $50 上限。每请求发出前
持久化预算和请求；新结果另存，不覆盖初次 cohort 分数。计费账单仍未知。

产品代码、G、Runtime、SemanticAnalysis、P4、M3-S1 和冻结评测均不变；不创建 PR，
不操作 master 或 PR #6。结果完成后独立复核、在原分支提交并非强推。

首个 v2 F 请求实际 HTTP 500 后停止。原定但尚未发送的 v3 实验使用单独的一次性
子会话执行；程序要求前会话正好一个已知失败请求、v3 尚无结果，两个 F 会话合计
仍最多两请求/$0.50。失败的 v2 不重发，不通过循环寻求成功。

## 实际结果

新版 v3 的 N01–N04 独立请求成功：三个明确工作各保留一条观察，owner、deadline、
project、vendor 共 12 字段位全部正确 UNKNOWN；N04 条件性建议没有成为任务。
与首次已成功的 N05–N12 合并为**补验后的覆盖统计**：12 个固定案例，7/7 任务召回，
5 个非任务零误召回，6 字段正确接受、22 正确 UNKNOWN、字段误接收 0。
这不是首次 12 案例满分，也没有替换首次 TLS/协议失败。旧版 N01–N04 本次 HTTP500，
仍无完整新旧成对语义分数；失败不当正确 UNKNOWN。

普通 Product run `ui-9c16cb1c55ef` 完成 5 次真实 gemini-3.8-flash 请求：F1、G1、
Runtime3（inspect_task_context、query_task_relations、最终输出），全部 SUCCEEDED。
没有脚本模型或响应重放。实际执行两工具、Reviewer 接受执行证据、HTTP 返回预期的
PARTIAL。PARTIAL 表示未确认关系和未知字段，不能据此宣称获得 P4 业务权威。

“离线索引重建”四个字段保持未知；“兼容矩阵核验”保留苏遥及 `2036-04-17`，
边界词“之前”在原文引用中。实际 F request 的 v3 profile/schema hash、两条观察的
Workspace、版本/hash、精确 EvidenceSpan、canonical speaker 和来源重验均通过。
新进程无 Provider 冷读与保存观察相等，跨 Workspace 409，合成来源更新后冷读
BLOCKED、隐藏旧 evidence/trace/task_review，额外 Provider 调用 0。

本补验 7 请求，6 SUCCEEDED、1 HTTP500 known FAILED、0 UNCERTAIN、自动重试 0；
预留 $1.75，低于新增 8 次/$2 上限。SDK attempts=1，历史失败预留未释放。
六个成功响应 usage：输入 33,638、输出 2,697、SDK total 37,029；失败请求无 usage，
不按零计费。包含 S1 的项目累计 102 请求、保守预留 $27.68/$50，实际账单仍未知。
S1 账本、旧 S2 账本及原固定输入 hash 均未变化。

本补验只改验收脚本与证据文档，产品代码无新增改动；两脚本编译通过。原 549 用例
通过记录继续保留，没有机械重跑。真实正常 Product 路径、冷读及上面的实际请求即为
本次集成证据。完整请求/响应 14 份原件 hash 已归档，原件保留在 `work/m3s2-followup/`，
公开归档省略 opaque continuation。

证据：[汇总](evidence/followup/summary.json)、[字段结果](evidence/followup/f-results.json)、
[真实 Product](evidence/followup/product-live.json)、[预算](evidence/followup/budgets.json)、
[原件清单](evidence/followup/request-response-manifest.json)。独立复核结论补记在 REVIEW.md。
