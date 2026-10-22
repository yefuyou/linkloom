# PR #6 最终主线同步与产品验收

本轮仅集成与收敛现有产品；保持 [PR #6](https://github.com/yefuyou/linkloom/pull/6) Draft，不自动 Ready、合并或发布。全部数据为公开合成输入，全部模型为现有 FakeModel / scripted FakeProvider，无外部 Provider、countTokens 或 LIVE 调用。

## Git 基线与集成

| 项目 | 实际值 |
|---|---|
| fetch 后 master | `8f9227d39f3d4425524f8dfb39209e22e3d66d17` |
| fetch 后 PR #6 Head | `13adb03c61df8c882a32ce71004b006f7a94fd9b` |
| 用户给出的旧 master | `33e9ed0e4d2156e202679040568c998c48f46e90`，已是上述 master 的祖先 |
| 正常 merge commit | `3e5b6e643666d185f11fa581325964b6e8456ff7` |
| 隔离分支 | `codex/m3p-final-mainline-sync` |
| 远端更新目标 | 既有 `codex/m3p-mainline-integration`，仅非强制 fast-forward |

从核验的 PR #6 精确 Head 创建独立 worktree 后，正常 merge 最新 master。没有文本冲突；Git 自动合并了工作流、roadmap、retrieval agent、materialization 和 product.js。**无冲突不等于有效逻辑通过**，以下三处实际问题经过 RED → 修复 → 回归闭合：

1. 普通文档的严格日期角色规则曾覆盖时间戳会议，阻断合法候选审批。现在普通文档仍必须有显式生效日期角色；会议保留原有原文字面日期合同，同时复用既有中文显式生效日期规范化，绝不从文档日期/录入日期推断生效日期。
2. 保存结果有 HTTP 冷读但刷新后没有 UI 入口。现在同一标签页只存 workspace → run ID 指针，Ask 中显式点击“重新核验上次任务调查”；每次 GET 都由后端重验，不缓存原文、不自动恢复会话。
3. 保存的 Reviewer 结论不是接受时，旧实现虽返回 BLOCKED，仍携带含原文的 trace。现在在冷读投影前拒绝未接受结果，返回空原文、空 trace、无 preparation。缺失 Ledger 同样阻断。

实际 master 已于本轮开始前合入 #7（PR Head `6c6c107e03fcdcc0e6f49f05dac0cd9516825eda`）。本轮继承最新 master，未更改 #7 分支、提交、描述或状态，未修改其专属实现/测试。既有主工作区和其他 worktree 未提交文件保持原样。

最初沙箱禁止写主仓库 Git 元数据且无法解析 GitHub，曾在 `work/m3p-final-integration` 做本地 #8/#9 合并预演。该目录和 `33952cef6` **不是最终集成候选**。受控 fetch 成功后从头建立上述正式 worktree，未将预演历史带入 PR。

## #8 / #9 与 CI 合同保留

| 合同 | 结果与证据 |
|---|---|
| E07 搜索但未 read 不得可信 Final | PASS；原始攻击 integration tests 保留并执行 |
| E15 已知反证不得仅引用正方获批 | PASS；read receipts 与 observed counter-evidence 均保留 |
| Task Investigation 的有限 P4 合同 | PASS；独立 task workflow 分支早于普通 source Final 校验，不强加全局 read_verified_note |
| 未解析规范关系 | PASS；真实浏览器显示指引，批准禁用，无虚假的关系修正输入 |
| 无 Provider 保存 | PASS；真实来源已保存，页面明确说明语义提取不可用，未声称提取成功 |
| 响应式顶栏 | PASS；900/390px 真实截图，390px 时 viewport/topbar/scrollWidth 均为390 |
| 任务观察与来源审查 UI | PASS；两场会议、字段证据、提案、前提、下一步均可见 |
| offline-pr.yml | Core 安全回归、三组 DOM、task 专项、Legacy、双系统 Frozen Ledger 全部保留 |

## 真实浏览器：场景 A

Playwright CLI + Chromium 155 在运行原 Product HTTP 服务的同一本机隔离 worktree 中操作；没有直接调用内部函数代替此浏览器流程。

通过 UI 选择 Workspace → Ask → tasks → 输入含负责人/截止日期的原林枫任务问题 → POST Ask → job 轮询 → 等待真实终态 → 展开两条原始消息和 owner/deadline 字段证据 → 支持/反对/缺失证据 → 查看精确来源 → 刷新 → 从 Ask 重新核验原 run。

| 检查 | 实际结果 |
|---|---|
| 两场会议 | 两条独立 observation；各自 source version 和原始消息保留 |
| owner | 两条均为林枫，字段证据可展开 |
| 第一条 deadline | `2029-06-20` / TASK_DEADLINE |
| 第二条日期 | 原文 `2029-06-24` 保留，deadline=null / UNKNOWN / DEADLINE_ROLE_UNRESOLVED |
| SAME_TASK | SUPPORTED 仅为模型提案；未成为可信业务关系 |
| 反对证据 | 实际为空，页面显示“未提供”，没有编造反证 |
| 缺失证据 | “产品不创建正式关系记录”及独立确认需求可见 |
| 后续 revision | BLOCKED；identity 前提没有可信支持，不继续修订 |
| 最终结果 | PARTIAL，Reviewer accepted=true；有原文读取和明确下一步 |
| 精确来源 | 打开第二场实际 source/version/quote，lines 1–1 |
| 刷新 | 原 run `ui-518470c08950` 仅经 GET 重验；未新建模型运行 |
| 控制台 | 任务页 errors=0 / warnings=0 |

截图：[任务与字段证据](task-review.png)、[精确来源](exact-source.png)、[刷新重读](refreshed-revalidated.png)、[390px](responsive-390.png)。原始截图、DOM、控制台、请求体/响应体和 trace 均在 `evidence.zip`。

## 授权与持久状态：场景 B

使用独立合成 Workspace、SQLite、Runtime 及 P4 sidecar。保留 before/hot/cold JSON、run ID、来源版本与副本。冷读使用新的 Python 子进程，热读和冷读均通过原 Product HTTP 服务。

| 情况 | 结果 |
|---|---|
| 来源更新为新版本 | PASS；旧结果 BLOCKED，无旧原文/trace/task_review |
| 从 Workspace 移除来源 | PASS；热读/冷读均不得重新发布旧内容 |
| 当前 Evidence Catalog 撤权，原版本和文本不变 | PASS；不能用保留文本重建已撤权证据 |
| 已撤回的 P4 关系 | PASS；旧结果 BLOCKED，不解锁后续步骤 |
| 跨 Workspace run 读取 | PASS；拒绝访问 |
| Ledger 缺失 | PASS；受控合成副本冷读阻断 |
| Reviewer 拒绝 | PASS；修复后冷读阻断且不泄漏技术轨迹 |
| 普通无模型 CLI 合法冷读 | PASS；available=false、external_provider=false、SAVED_RESULT_READ_ONLY、session_resumed=false、Reviewer 重验通过 |

Catalog SQL 撤权、P4 lifecycle helper、Checkpoint/Reviewer 完整性注入都是**现有受控测试/合成副本操作，不是普通用户 UI 功能**。来源更新和移除使用现有 Product HTTP API。普通 CLI 冷读额外启动原 `python -m linkloom.ui --database ...`，没有 FakeModel 工厂或 F/G adapters。

## 已有产品：场景 C

独立空合成 Workspace，在原 HTTP 服务上使用仓库已有 scripted FakeProvider 配置；只是测试 Host 配置，生产接口没有改造或伪造返回。

Sources 导入 `launch-decision.md` → Review 审批具备合法字段的 Birchline → Decision Memory 查看日期/原文/独立审核收据 → Ask 当前查询和 `2026-10-02` 历史查询 → 打开原文 → 保存 Wrenwell 新来源版本 → Review Attention 显示旧 Birchline STALE。另导入未映射的 `negotiates with` 候选，页面禁止批准并说明处理方式。

结果：**PASS**。截图：[审批](valid-approval.png)、[Decision Memory](decision-memory.png)、[当前 Ask](current-ask.png)、[历史 Ask](historical-ask.png)、[未解析关系](unresolved-relation.png)、[来源更新提醒](review-attention.png)、[无 Provider 保存](no-provider-saved.png)。

真实控制台记录保留：空 Workspace 启动时 `/api/review/candidates` 返回预期的无 Workspace 409，页面捕获后可正常创建 Workspace；没有 JavaScript pageerror。未把此记录删掉或声称整个浏览器会话零 HTTP 错误。

## HTTP / 浏览器超时调查

按浏览器启动、页面初始化、提交、任务创建、后台 Runtime、job 轮询、前端终态和结果渲染逐段检查。trace 内134个请求全部保留，最长 **611.656ms**；唯一 HTTP 错误为上述39ms的空 Workspace 409。任务 POST 返回202，随后同一 run 连续 GET 到终态，Runtime 工具三步和 Reviewer 时间戳可与 trace 对齐。

本轮没有复现原先 Product HTTP 轮询超时或后端完成前端不刷新，不能追认历史超时已经永久修复，也不能断言历史根因。分类：A 产品阻塞/死锁、B 状态未刷新 **未复现**；历史事件仍为 **NOT_EVALUATED（缺少当次完整时序，无法定因）**。未增加产品或测试 timeout、无限轮询或跳过失败。

已确认的本轮控制/环境故障：旧 Node18 不满足 Playwright 要求、npm 默认缓存冲突、Chromium 尚未安装、FFmpeg 下载 TLS 重置、CLI run-code 函数格式错误、刷新后的旧 ref 失效，分别属于 D 环境和 C 自动化条件/脚本问题。切换已有 Node24、独立缓存、安装 Chromium、使用新 snapshot 和正确函数格式后继续完成了验收。不是产品死锁证据。

后端默认不输出 HTTP access log；保留实际 CLI startup、Runtime/Checkpoint/Ledger、浏览器请求/响应；另用仅增加 handler 计时的测试包装器运行原普通 CLI，日志在证据包 `no-provider-server.log`，没有修改生产 handler。

## 测试与原始失败

最终完整批次的准确结果见随附验证收据和 `evidence.zip` 中 `work/final-closed/`。测试不去重相加冒充一次全仓库结果；本轮未运行全部历史私有 LIVE-log 回放，也未宣称全仓库通过。

| 最终检查 | 实际结果 |
|---|---|
| Core（含原始E07/E15、TeamDecision、TaskPlan、安全/产品及#7再审） | PASS：303 passed，2 deselected，204.02s |
| Task 全专项（含F/G、P4、Context、HTTP、撤权和新增冷读） | PASS：533 passed，193.31s |
| Legacy Historical Gate | PASS：4 passed，22 deselected，14.54s |
| Frozen Ledger bytes | PASS：5 passed，1.34s |
| 冷读与失败终止 HTTP 追加专项 | PASS：19 passed |
| 日期/P2 authority/observation/权威回读 | PASS：46 passed；另一次候选+P2+再审58 passed |
| 当前授权独立原始HTTP/冷读证据 | PASS：update/remove/catalog_revocation/relation_retract 四组 |
| 保存副本完整性注入 | PASS：Ledger缺失、Reviewer拒绝两组 |
| 严格离线 Product HTTP / FakeModel | PASS；新进程冷读、provider_requests=0 |
| Unified Ask / 首次使用 / stale revalidation DOM | PASS |
| JavaScript syntax / Python compile / git diff --check | PASS |
| 最终 Wheel 构建及安装目录 smoke | PASS；SHA256 `f84c0ee58b2fc7e9f6e6f490b418a5c8758bdad332a99dd91c0b78f1483187b8` |
| 既有 Gold/Seal/Frozen 命名文件 Git blob | PASS：45个原有blob未变 |

已保留的真实失败：

- 沙箱预演测试：68 passed / 29 failed / 36 errors。错误来自不可写系统临时目录，失败是隔离 checkout 缺少旧 phase9/11/14 LIVE 回放文件，标为 BLOCKED_ENV；没有改原断言或补造历史文件。
- 首轮正式 Core：285 passed / 1 failed / 17 errors。1例为测试临时目录不在系统临时根内，使用一致的专用 TEMP 修正运行环境；17例为上述真实日期回归，标为 FAIL_PRODUCT，已修复。
- 日期首个新测试曾用错属性，保留脚本失败；纠正属性后真实 RED 为1 failed/1 passed。
- 第一版日期修复后的 Task 长跑：522 passed / 9 failed，均为中文规范化日期兼容遗漏。后续46项和58项专项通过；最终完整批次另行记录。
- 冷读 Reviewer 完整性 RED：1 failed / 1 passed；修复后19项 HTTP合同与两项同源注入均通过。
- 授权证据脚本首轮 Windows 子进程编码错误保留，显式 UTF-8 后四种情况全部通过；不是产品通过记录。

没有删除断言、skip、xfail 或修改历史 Gold、Seal、Frozen Ledger。45个既有受保护 Git blob 与原 PR6 精确 Head 相同；Frozen Ledger 原字节测试另跑。**M2 H01–H04 仍为0/4 FAIL**，只保留历史结论，不把有限 P4 合同解释为语义能力通过。

## 独立复审、远端与人工决定

独立只读 Reviewer 使用 `code-review-and-quality` 技能检查完整集成差异、日期修复、刷新指针和冷读边界，报告见 [REVIEW.md](REVIEW.md)。其首轮结论之后主 Agent 补充发现了 Reviewer 拒绝冷读 trace 问题，已另交增量复审；不把首轮批准冒充覆盖后续改动。

最终 Head、非强制 push 收据和该 Head 的 GitHub Actions 状态以 PR6 的最终更新及本轮交付消息为准。旧 Head 的绿色 CI 不代表新 Head 已验证。无论结果如何，PR6 保持 Draft。

范围与状态：

- PASS：明确离线任务调查、来源授权/持久状态、现有产品 FakeProvider 流程、依赖可信性和保护性回归，以最终收据为准。
- FAIL_PRODUCT：上述日期与 Reviewer 冷读问题曾真实失败，已修复并保留 RED/GREEN。
- BLOCKED_ENV：早期沙箱 Git/临时目录、旧运行时、安装/控制失败，已通过受控权限/隔离依赖继续完成。
- NOT_TESTABLE：普通 UI 中的 Catalog撤权/P4业务批准/Reviewer注入；本轮通过明确标注的测试接口检查合同，未实现这些 UI 能力。
- NOT_EVALUATED：真实模型语义泛化、付费 Provider、生产部署、认证的任务关系审批、历史超时的确定根因；M2保留原失败结论。

建议人工仅按**默认关闭、显式离线可用的有限任务调查增量**评估 Ready/合并；须以最终无开放 P1/P2、完整关键门槛和该 Head 的 Actions 成功为前提，不代表真实模型任务调查已经可发布。
