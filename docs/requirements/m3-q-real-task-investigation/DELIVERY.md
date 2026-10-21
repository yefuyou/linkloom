# M3-Q 结果报告

结论：正常 Product Host 的任务 Provider 路径已实现，完成 **OFFLINE** HTTP、真实 Chromium
与冷读验收。真实付费模型未调用，状态为 **REAL_PROVIDER_NOT_EVALUATED**，不能宣称 M3-Q
真实模型完整验收。可信关系语义的 H01–H04 原始失败仍保留。

## 用户可见变化

普通 CLI 可以显式配置 Gemini 3.8 Flash 任务阶段，不再要求 `--offline-task-demo`。
配置仅准备 SDK/Adapter；导入来源保存本地原文，不隐式发送给模型。Ask 展示任务能力、
未配置原因与完整启动指引、当前来源名称/版本、单次请求/输入/输出/费用/时长预算。
用户勾选本次授权后，Host 将问题绑定当前 Workspace 和确切来源快照，执行 F/G 和现有 Runtime。

结果仍通过 Grounding/Reviewer：展示两场会议独立观察、负责人、日期角色、精确来源、
模型关系提案、既有 P4 可信证据、缺失前提和 PARTIAL/BLOCKED。刷新后重验完成结果，
普通无 Provider 进程也可以只读回看；来源更新或证据撤权会隐藏旧原文与 trace。
任务配置不隐式开启 Temporal Agent；通用 Provider 授权不授予任务阶段或关系批准权限。

本轮未实现认证人工关系确认。模型提出 SUPPORTED 仍不成为可信业务关系，不能自动合并任务、
继承负责人/日期或宣布哪版计划当前生效。原文第二个日期角色无法由现有 M1 合同确认时，
保留 UNKNOWN/DEADLINE_ROLE_UNRESOLVED，不补字段。

## Provider、预算与实际成本

配置模型：`gemini-3.8-flash`；SDK：`google-genai==2.22.0`。
检查了 [官方模型能力](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)
与 [SDK timeout/retry 合同](https://googleapis.github.io/python-genai/genai.html)，并通过
**实际 SDK + httpx.MockTransport** 验证结构化 function declaration、wire schema 与回包解析。
这证明本地接口兼容，不证明账号、模型线上可用性或语义效果。

所有新执行/默认模型均未使用 Gemini 2.5。真实 Provider 请求 **0**，真实费用 **USD 0**。
最终浏览器与脚本共执行两次独立调查，合计 16 次 Adapter 请求；每次为 F 2、G 1、Runtime 4、
语义 1。网络边界全部由明确的 scripted transport 替代。没有用 FakeModel 作为正常产品 Runtime 工厂。

每个问题建立共享预算，所有阶段先持久化请求 hash、Token/费用预留再调用。
预留取显式每请求估计与保守 Token 费用估计中的较大值；默认按输入 USD 1、输出 USD 5
每百万 Token 估计，高于本轮查询的
[官方入门价格](https://ai.google.dev/gemini-api/docs/latest-model)。未知实际账单记 null，不记免费。
预算日志保存失败、超时、UNCERTAIN、usage 和安全错误，不复制凭证或私有 continuation。
SDK attempts=1，单请求传输 timeout；Host 限制单请求与总时长，冷读还核验 Runtime 完成时间。
独立复审发现并关闭一个 P2：LIVE 终态读取必须有完整成功预算回执。缺失、PENDING、UNCERTAIN、
FAILED、WITHHELD、计数不一致或非法日志均阻断；还重验 Token/usage、费用预留与时限。
运行中 PENDING 和 FAKE/REPLAY 使用各自原有合同。热读与新进程冷读都不能绕过回执。
超时后无法证明远端未计费，因此保留预留并停止后续调用；不会自动重试未知结果。
后台运输线程不能强制取消，但其迟到答案不会发布或触发下一次请求。

## H01–H04：根因与本轮边界

原始 M2 记录来自精确依赖历史的
[原交付](https://github.com/yefuyou/linkloom/blob/dd76a25bd9e70544c8e0534538b694015928fea1/docs/requirements/m2-integration-acceptance/DELIVERY.md)，
原始结论 **0/4 FAIL** 没有覆盖。

| 案例 | 原始根因 | 本轮修复及仍未解决 |
| --- | --- | --- |
| H01 | P3 有限语法不识别“替代其原计划，改为”，输出 UNKNOWN | 正常 Provider 能收到完整关系候选，修订不再因非 ready 而被排除；可信 P3 原语义仍未修复，缺年仍不补年 |
| H02 | P2 不识别简写变更/具名否定的直接影响绑定，三题都 UNKNOWN | 复用现有 LLM AFFECTED_BY 提案合同；本轮未扩建 ChangeRef 发现或改写 P2，原失败仍未解决 |
| H03 | P3 漏正证，仅识别反证，误报 REFUTED | 新增结构校验：同时提交正反证却自称 SUPPORTED/REFUTED/UNKNOWN 会拒绝；有效 CONFLICT 不选赢家；并不能保证模型找到了遗漏正证，原 H03 未宣称修复 |
| H04 | P3 新旧修订、纠错和历史版本绑定失败，初始 +2 未建立 | 更新/撤权/冷读隔离通过；未让模型提案生成 P4 可信证明，原 +2/纠错/历史完整序列仍失败 |

实际链路与有限语法位置见 [AUDIT.md](AUDIT.md)。F 是模型发现 + M1 字段/非断言 veto；
G 是模型规划 + 确定性范围/依赖验证；旧 P1/P2/P3 与候选排序是有限规则；M3 SemanticProposer
是 LLM 结构化语义提案。规则未匹配保持 UNKNOWN，不等价于关系不存在。
三跳是 G 的显式预算，超限报错；截断/未处理来源不证明无反证。

本轮没有增加中文业务关键词或正则。模型负责发现语义及精确引用，代码验证来源身份、
引用位置、Workspace、生命周期、输出结构和执行前提；引文有效不能证明其语义成立。
完整计划只扩大**提案问题**，P4 的执行就绪条件、方向、身份/修订依赖没有降低。
将任意 LLM 语义转成可重验可信证明会要求改变现有 P1/P2/P3 与批准合同，超出本轮最小接线，
因此停在已授权的 P0 与定向结构改进，不擅自重写引擎。

## 变更与工程证据

主要生产文件：`ui/task_provider.py`（共享预算 Adapter）、`ui/task_investigation.py`（F/G/语义接线）、
`ui/investigation.py`（权限、现有 Runtime 组装与重验）、`ui/__main__.py`（显式 CLI）、
`ui/unified_ask.py` / `ask_admission.py` / `static/product.js`（单次范围授权与展示）、
`tools/semantic_analysis.py`（显式 LIVE/回执）、`task_relation_semantic_validation.py`（极性校验）、
`agents/providers/gemini_api.py`（工具回包暴露 Runtime receipt ID）。
没有第二套 Planner/Loop/权威库，没有修改冻结输入、Gold、原历史记录或 PR #6。

[verification.json](evidence/verification.json) 与 [manifest.json](evidence/manifest.json) 保留收据及 hash。
下面是工程合同验证，不是产品语义效果指标；批次互有重叠，不能累加成效果成绩。

| 检查 | 实际结果 |
| --- | --- |
| 原 Task workflow + 新合同 | 最终代码 582 passed；此前 568 passed 的收据保留 |
| Core | 原选择器在 Git 外 synthetic 临时目录完整重跑：303 passed、2 deselected；初始 302 passed/1 环境失败保留 |
| Legacy | 4 passed、22 deselected，原选择器未改 |
| Frozen | 5 passed，历史字节不变 |
| 正常 Host HTTP、导入与隔离 | 18 passed |
| SDK/CLI、预算、四态结构合同 | 22 passed |
| 最终回执、错误/总时长 guard、热读/冷读与失败路径 | 修复后合并检查 44 passed；独立 Reviewer 定向 14 passed；此前各批次保留 |
| DOM / JS syntax / Python compile | passed |
| Wheel | 本地离线构建 passed；hash 见 verification |
| 正式 HTTP → 普通 CLI 新进程冷读 | [PASS](evidence/http-summary.json)，配置 available=false/external_provider=false，session_resumed=false |
| 真实 Chromium | Chrome 155.0.8059.85，[errors=[]](evidence/browser.json) |

真实浏览器截图：[未配置](evidence/browser-unconfigured.png)、[范围与预算](evidence/browser-scope.png)、
[授权拒绝](evidence/browser-permission-denied.png)、[运行中](evidence/browser-running.png)、
[调查结果](evidence/browser-results.png)、[打开来源](evidence/browser-source.png)、
[刷新重验](evidence/browser-refresh-revalidated.png)。均为实际页面，没有生成或改绘截图。

RED/实际失败也保留：新增模块/工厂缺失；Gemini Provider 标识导致 temperature 不兼容；
工具 wire 缺 Runtime receipt ID；极性 3 个 RED；任务授权提示 DOM RED；随后均 GREEN。
还遇到沙箱 loopback/pip 临时目录权限、Windows 子进程 CP936/UTF-8 不一致、
浏览器 CSP 禁止 eval 与启动加载竞态。使用合法环境/DOM 等待修复，没有降低 CSP 或删断言。
开发中旧服务器的 journal 没有新增 started_at，重验新合同曾 BLOCKED；保留原产物，
最终新进程 [HTTP/CLI 收据](evidence/http-summary.json) 通过。

独立复审 P2 的 [RED](evidence/m3q-receipt-red.xml) 保留：缺失 LIVE journal 时热读仍返回 PARTIAL。
修复迭代中的 18 failed/37 passed、1 failed/16 passed 也完整保留；分别暴露 Windows 长路径、
将运行中 PENDING 当成终态，以及 Runtime 完成时间按整秒截断的精度差异。
终态校验与时间精度处理修正后，[最终组合回归](evidence/m3q-receipt-green3.xml) 为 44 passed。
时间上限不放宽，额外核验 journal 的单调时钟执行时长。

额外旧 `test_p85_gemini_api_adapter.py` 批次为 **82 failed / 4 passed**（另加新合同的批次为
82 failed / 6 passed）。[精确基线归档复跑](evidence/baseline-p85.xml) 同样 82 failed，
原因集中在旧 Gemini 2.5 / temperature=0 与现有 3.8 合同不兼容。未将它们删除、跳过或
改成新模型来制造绿灯；它们不属于当前 Core/Task/Legacy/Frozen workflow 选择器。
该历史单测技术债保留，不能宣称全仓库套件通过。

## Git、复审与未完成项

分支 `codex/m3q-real-task-investigation`，隔离目录 `work/m3q`，依赖 Head
`67b91a1520f6b129985905702f2a2bd19a631882`。主工作区已有修改完整保留。
应用管理器的另一个空 managed Worktree 延迟创建成功，也保留未清理；没有破坏性 Git 操作。
交付 Head SHA 与远端分支回读状态见本次最终消息；用户原请求已条件授权普通 commit/非强制 push，必要门禁与独立复审条件现已满足。
当前尚未创建 PR；PR #6 未合并前不创建面向 master 的集成 PR。
本地通过不等于远端 CI 通过；此新分支无 PR 触发时 CI 记 NOT_RUN。

独立只读 [REVIEW.md](REVIEW.md) 首轮记录 P1=0/P2=1，修复后定向复审为 **PASS、P1=0/P2=0**。
初期复审 Agent 曾遇到模型容量错误，随后独立 Reviewer 完成审查与签字，主 Agent 自检不替代独立复审。

下一步是真实 Provider 的明确预算授权后小样本验收与坏例复核，优先验证修订/反证召回，
然后再单独设计可重验的语义支持合同。认证任务关系确认、H01–H04 可信语义、普遍召回、
线上模型可用性和正式部署仍未解决。本轮不宣称完整 M3-Q 业务验收。

## 复现

在本隔离检出中使用新的输出目录；不覆盖原证据：

```powershell
$env:PYTHONPATH='src;.;tests/integration'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
python -m pytest tests/unit/test_m3q_provider_budget.py tests/unit/test_m3q_cli_config.py tests/unit/test_m3q_gemini_receipts.py tests/unit/test_m3q_semantic_polarity.py tests/integration/test_m3q_provider_host.py -q
python scripts/run_m3q_offline_acceptance.py --output work/my-new-m3q-http
node tests/ui/unified_ask_dom_test.cjs
```

浏览器：先启动上面脚本加 `--serve`，再启动普通无 Provider CLI（port 8774）；用
[browser_check.py](evidence/browser_check.py) 传入脚本打印的 localhost URL 和新输出目录。
门禁命令从当前 workflow 原样提取；最终 [Core runner](evidence/run_core_final.py) 和
[Task runner](evidence/run_task_final.py) 使用 Git 外系统临时目录；复现时选择新的短 basetemp。
Permission Gate 的 synthetic 测试不能放在 Git checkout 内。

以下仅是未来经用户明确批准真实请求后的启动方式，本轮没有执行：

```powershell
# GEMINI_API_KEY 只放本机环境，不写入项目或证据。
python -m linkloom.ui --database work/my-product.sqlite --allow-external-provider --allow-task-investigation --task-max-requests 16 --task-max-input-tokens 500000 --task-max-output-tokens 100000 --task-max-cost-usd 2 --task-max-seconds 120 --task-request-timeout-seconds 30
```
