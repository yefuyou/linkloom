# Integration D 组合交付

F v3 与 G 有界重规划已组合，离线、正常 Product LIVE 和权限/冷读验证完成。
业务输出为 PARTIAL：这是对未知字段和未确认关系的诚实结果，不是 P4 业务确认。
独立复审及集成结论见 [REVIEW.md](REVIEW.md)。
结论：**READY_FOR_INTEGRATION（本次组合边界）**，没有未解决的 P1/P2。

## 合并边界与图

- 仓库：`yefuyou/linkloom`。
- 共同祖先：`bde9d9e2b88c2e69132cf48d1534dc28d4fca764`。
- 第一父提交（固定 S1）：`4157dae1fb9331bb95888eff3ec331aa0b612459`，
  原分支 `codex/m3s-ambiguous-product-closure`。
- 第二父提交（完整 S2 历史）：`1d3a3363757e00bf8c9970564ad2bfb99fa27fbd`，
  原分支 `codex/m3s-task-recall`。
- 新分支：`codex/m3s1-s2-combined-acceptance`。
- 独立工作树：`work/m3s1-s2-combined`。主工作区已有改动未参与。

```text
bde9d9e2 ── S1 历史 ── 4157dae1 ──┐
                                    ├── Integration D（正常双亲 merge）
bde9d9e2 ── S2 完整历史 ── 1d3a3363 ┘
```

使用 `git merge --no-ff --no-commit <固定 S2 SHA>`，验收/独立复审后正常提交。
没有 squash、rebase、reset 或 force push。新提交 SHA、双亲和远端回读在交付消息中报告。
没有修改原验收分支、master、PR #6 或 CI 配置；不创建 PR、不自动合并。

## 共同文件与语义合并清单

双方共同修改的生产文件只有 `src/linkloom/ui/task_investigation.py`。
Git 自动合并成功，文本冲突 **0**，手动冲突解决 **0**；没有整文件 ours/theirs。

| 位置 | 保留的语义 | 来源 |
| --- | --- | --- |
| LIVE F profile 选择 | `TASK_RECALL_V3`，日期 token 协议、显式工作与字段可用性分离 | S2 |
| discover 回执 | 来源/发现结果 hash 绑定，结果 UNKNOWN，不冒充发现完成 | S1 |
| 唯一一次重规划 | `unavailable_operations=('discover',)`，Schema 和 decode 同时限制 | S1 |

其他生产变动是两个输入分支的并集，共六文件；Integration D 没有额外改生产代码。
S2 的 profile 名称唯一，仍使用现有 TaskProposal/TaskFieldBinding/TaskObservation 合同。
S1 的完整 Host 计划、来源投影、保留步骤 ID 和积极依赖条件均未被覆盖。
两边既有测试、来源证明、预算、失败记录和报告全部保留。

## 组合场景

| 场景 | 离线 Product HTTP/Runtime/Reviewer | 新 LIVE |
| --- | --- | --- |
| A：明确任务 + 明确负责人/截止日 | PASS；乔雨、`2037-08-19`，角色引述保留“之前”，日期 token 不含边界词 | PASS，与 B 同一次普通 Product 问题 |
| B：明确任务 + 四字段未知 | PASS；任务观察保留，独立 inspect 完成，PARTIAL | PASS；索引重建的 owner/deadline/project/vendor 均未知 |
| C：discover 无新信息 -> UNKNOWN -> 有界重规划 | PASS；完整初始计划/请求/响应/Host 回执/重规划/Runtime/最终结果/冷读轨迹 | **未命中，不声称通过** |

[A 轨迹](evidence/offline-A.json)、[B 轨迹](evidence/offline-B.json)、
[C 完整轨迹](evidence/offline-C.json)。这些明确标注 OFFLINE_SCRIPTED_TRANSPORT，
只替换网络响应，使用真实 Product HTTP、Host、Runtime 和 Reviewer。

C 的重规划保留原步骤 ID/依赖、来源绑定 handle、完整外层来源表/端点，
`locate` 的原步骤和 UNKNOWN 回执保留；`still_dependent` BLOCKED，独立读取 COMPLETED。
实际含 framing 预留的重规划输入 **16,053 bytes / 24,000**，原上限未放宽。
Schema 禁止 discover，Host 也拒绝重新引入；没有新增 F、循环或越权执行。
UNKNOWN/CONFLICT 均不能满足积极步骤前提，未完成 discover 不算完成。
关系提案及 Reviewer 的执行验收均不构成 P4。

## 回归、兼容和失败保留

新增组合测试 6 项通过。相关 Task/Frozen、规划、上下文、权限、Runtime、HTTP 合同
共 **651 个不同用例最终通过，0 skip**，不把重叠批次相加。

- 首次较广回归：624 PASS / 27 FAIL，原 [XML](evidence/regression.xml) 保留。
- 27 项在第一个 Runtime 工具后因 Windows 长路径写入失败；具体工具产物路径达
  262 字符。改用短临时路径后，完全相同的 27 个用例 [全部通过](evidence/regression-short-path.xml)。
  未修改生产代码、断言或历史 Gold。
- [逐用例去重对账](evidence/regression-final.json) 记录每个最终结果，独立 Reviewer
  核对两批失败 ID 集合完全相同。不是声称首次全绿。
- 前期缺工作目录、沙箱回环阻断，以及新合成提问触发既有动作词拒绝的失败 XML 均保留。
  新测试资料在付费前固定为当前版本；没有放宽产品权限规则。
- v2 `_field_prompt`/`_field_schema` 与共同祖先 AST 相同；旧 profile 默认行为、
  `tests/eval`、`tests/fixtures`、Frozen、历史 Gold 和 `.github` 未改。
- JavaScript 语法与 unified Ask、stale revalidation、first use 三组 DOM 脚本通过。
  这是本地验证；没有远端 CI 或部署声明。

## 新 LIVE 与预算

固定 [LIVE_CASE](LIVE_CASE.json) 包含 A/B 两条新合成来源，未使用真实 vault。
正常 Chromium 界面创建 Workspace、导入 Sources、选择 Tasks；未勾选单次来源授权时
实际请求 **0**。授权后普通 Product 完成 `ui-2c99e40003f1`，返回 PARTIAL、error=null，
Reviewer accepted。真实工具为 inspect_task_context / query_task_relations。

真实 F 的 v3 profile/schema、两条观察各自的版本/hash/精确来源 span、字段绑定在新进程中均核对。
真实 G 选择六个 inspect 步骤，没有 discover，只有一个计划；**没有新的 LIVE 重规划通过声明**。

配置只用 `gemini-3.8-flash`、`google-genai 2.22.0`、每个实际 SDK wire 的 LOW、
SDK attempts=1。Runtime 通用 ModelRequest 没有显式 thinking 字段时，由本次验收
Bridge 按用户要求写入实际 LOW 配置，原 wire hash 保留；没有 Gemini 2.5。

| 项目 | 结果 |
| --- | ---: |
| 新增真实请求 | 5：F1 / G1 / Runtime3 |
| Provider 结果 | 5 SUCCEEDED，0 FAILED，0 UNCERTAIN |
| 自动重试 | 0 |
| 新增保守预留 | $1.25 / $3；请求 5 / 12 |
| 已返回输入 / 候选输出 / SDK total tokens | 32,919 / 1,705 / 34,899 |
| 项目累计请求 / 保守预留 | 107 / $28.93，低于 $50 |
| 浏览器/来源/冷读安全新增调用 | 0 |

开始前重读 13 份 S/S1/S2 原始当前账本，加前驱 47/$13.93 checkpoint 得到 102/$27.68；
另清点 19 份更早/嵌套账本，没有发现其他新付费批次。保留 S/S1/S2 原始 envelope
尚未用完的 $4.25，连本次完整 $3 容量占用 $34.93。旧阶段的剩余额度不重复相加；
旧 Product 单问题 max_cost 不是新的持久拨款，已有 FAILED/UNCERTAIN 预留不释放。
这是当前列举路径的账本快照，不是跨进程全局事务锁或通用未来预算证明。

每次真实请求都有 Product 与全局两层 fsync PENDING 预留，然后才进入 SDK。
前驱账本 hash 在本轮结束仍一致；嵌套账本不重复计费。
预留不是实际账单；Google 计费项目、实际扣费/赠金扣减仍未知。

[汇总](evidence/live-summary.json)、[授权与预留](evidence/live-reservation.json)、
[原件 hash 清单](evidence/live-original-manifest.json)、[初次完整结果](evidence/live-result.json)。
完整原件位于本工作树 `work/m3d-live/{requests,responses,wire,global-provider}`，
公开报告省略 opaque continuation；未提交 SQLite 或密钥。

## 浏览器、冷读与已修复的验收脚本问题

5 次真实调用已使 Product 正常完成后，验收脚本错误地用 `product://` 和当前 ingestion time
重建原件，而 Product 的真实来源使用 `linkloom://` 和保存的 version_created_at，导致
脚本来源复核断言失败。该 **harness FAIL 保留**，不是 Provider/Product 失败。
修正验收脚本元数据后，使用同一保存的真实结果进行无 Provider 复验，没有重发付费请求。

[保存结果复验](evidence/live-saved-verification.json) 为 PASS：真实 Chromium
155.0.8059.85 展示两条任务、已知/UNKNOWN 字段、精确原文，打开原始来源与刷新成功，
页面错误 0。无 Provider 新进程冷读保留相同观察；撤权、来源更新后的热/冷读均 BLOCKED，
不返回旧 evidence/trace/task_review；跨 Workspace 热/冷读均 409。
安全测试前后真实 Provider journal SHA 相同。
离线 C 的真实浏览器冷读展示另见 [offline-browser](evidence/offline-browser.json)。

截图：[LIVE 字段](evidence/live-fields.png)、[原始来源](evidence/live-source.png)、
[离线 C 字段](evidence/offline-C-fields.png)。原 UI 中通用 Sources 提取器 unavailable 表示未配置通用入库 Provider，
Task Provider 已按本次明确授权独立完成上述真实调用，不影响 Tasks 的已保存来源核验。

## 剩余边界与下一步

没有未解决的本次组合生产代码缺陷；未知业务字段仍需正式来源确认。
本轮 C 只有完整离线验收，不能推断一般 LIVE 重规划稳定性；更广泛模型泛化未测。
Windows 超长临时目录仍需使用短路径，未扩展范围修改 Runtime 路径策略。
本轮没有 CI、部署、P4 提升或面向 master 的 PR。

等待 M3-S 上游进入 master 后，再准备独立增量 PR；不自动创建或合并。
