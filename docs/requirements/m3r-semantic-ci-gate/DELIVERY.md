# M3-R Product SemanticAnalysis 离线 CI 防回归

## 范围与固定基线

- 仓库：`yefuyou/linkloom`
- 基线：`codex/m3r-semantic-evidence-closure`
- 固定 Head：`417595a2ffc4e02566b7a0dd6e96f143f472e5ca`
- 交付分支：`codex/m3r-semantic-ci-gate`
- 按用户已批准的 A–F 验收边界，只修改测试、既有 CI 和本说明。
- 独立 clone 开发；故障实验在另一 detached worktree 执行。
  M3-S 工作目录、master、PR #6、M3-R 原分支未修改。
- 未修改生产实现、Provider 配置、业务规则、正则或冻结 Gold/Seal/Ledger。

## 1. 原有覆盖审计与最小缺口

| 层级 | 既有测试 | 已覆盖 / 本轮处理 |
| --- | --- | --- |
| F/G、Product HTTP | `test_m3q_provider_host.py`、`test_m3p_product_review.py` | 正常执行、授权、原文展示、语义四态；旧 G fixture 的 `source_handles=[]` 漏掉真实触发条件。本轮增强既有 Host fixture。 |
| 源句柄转换 | `test_task_investigation_semantic_projection.py` | M3-R 已检查 proposal 转换允许来源句柄、正式 P4 转换拒绝，但仅为局部检查；此前也未加入 CI。复用并接入。 |
| ToolRuntime / Ledger | `test_tool_ledger_runtime.py`、`test_semantic_review_guards.py` | 未注册工具、参数/调用身份/顺序/结果篡改；本轮在原未知工具测试中补 `analyze_task_semantics` 参数。 |
| 语义权限与证据 | `test_task_relation_semantic.py`、`test_semantic_context_builder.py`、`test_semantic_runtime_boundaries.py` | Workspace、撤权、版本、伪造引文、错误端点、缺失/截断来源；复用，本轮只补“授权部分来源”控制。 |
| 正式关系限制 | `test_task_investigation_p4_steps.py`、`test_semantic_runtime_integration.py` | P4 来源作用域、依赖、受信记录、日期比较限制；复用。 |
| 持久化与恢复 | `test_semantic_invocation.py`、`test_semantic_runtime_boundaries.py`、`test_m3p_product_review.py` | 语义结果缓存、响应持久后恢复、checkpoint 中断、独立服务/新进程冷读；复用。 |
| 预算与失败 | `test_m3q_provider_budget.py`、`test_m3p_execution_failures.py`、原 Host 失败测试 | FAILED/UNCERTAIN、预算、无进展、禁止重复计费、结果收据缺失或篡改；复用。 |

真实缺口是缺少一项覆盖正常 LIVE **组合路径**的离线测试：G 输出
带 `source_handles` 的两个关系步骤后，能否到达真实工具执行和 Product 投影。
另一缺口是 Product 没有有效任务候选时，是否仍可能制造语义执行/结论。

## 2. 新增与增强

`SourceBoundPlanningStage` 只改变 scripted 模型输出：从实际 G 输入的
`allowed_evidence` 取得句柄，为 SAME_TASK / REVISES_PLAN 返回来源绑定，
并要求依赖二者的正式日期比较。F 观察和 G 计划均经原生产验证器。
这个 fixture 增强了既有 Host 的正常、权限、freshness、语义状态、失败和冷读用例；
unscoped 导入路径仍由原 `empty_provider_product` 用例保留。

新增两项 E2E：

1. `test_m3r_source_bound_product_chain_matches_durable_tool_receipts`
   通过真实 HTTP 授权和提交，核对 F 观察、G 关系句柄/绑定、TaskPreparation、
   模型可见工具 schema、语义启用提示、独立 checkpoint reader 读到的真实 Ledger、
   ToolCall 的 Workspace/调查 ID/调用 ID/顺序/参数、持久语义 request/result 的 SHA、
   原 Reviewer、来源引文与 Product 投影。检查两个关系步骤的提案均被执行，而非只检查对象存在。
2. `test_m3r_no_task_candidates_cannot_manufacture_relation_or_semantic_call`
   F 返回无任务候选，G 无合法端点，Product 必须 BLOCKED；无语义请求、无关系结论，
   请求数有界。

Host 模块增加 loopback socket guard；真实 HTTP 只允许本机连接。
注入的是现有 Gemini adapter 的 scripted transport，无真实 Provider 网络请求、无需 Key。
没有 mock 掉准备、注册、执行、Reviewer 或来源注册表。

断言使用 PLANNING、PREPARATION、TOOL_REGISTRATION、TOOL_EXECUTION、
EVIDENCE_VALIDATION、REVIEWER、PRODUCT_PROJECTION、SOURCE_FRESHNESS
定位主要边界，未新增生产状态。

## 3. 故障注入有效性

固定基线的第二份 detached worktree，复制新 E2E；每项实验前恢复干净生产内容，
仅在该临时检出注入下列故障，实验后恢复所有生产文件。没有提交或推送任何故障。

| 对照 | 操作 | E2E 实际结果 |
| --- | --- | --- |
| 正常控制 | 固定 M3-R 生产实现 | exit 0，1 passed |
| M3-Q 原 Host | `task_investigation.py` 使用父提交 `224d2d8df64f7b209c446ff04359733d95619af3` 原文件 | exit 1，PREPARATION：`SOURCE_SCOPED_OPERATION_REQUIRES_HOST_BINDING:identity`，manifest 为 BLOCKED |
| 单独恢复错误过滤 | `_proposal_queries` 增加 `and not step.source_handles` | exit 1，PREPARATION：`no SemanticAnalysis` |
| 移除真实注册 | `register_task_relation_tools` 的 semantic 注册条件临时改为 False | exit 1，TOOL_REGISTRATION：Runtime produced no model turn |
| 错关语义标志 | Runtime adapter 强制 `semantic_analysis_enabled=False` | exit 1，PREPARATION：semantic_analysis_enabled was false |

以上为实际 pytest assertion failure，非未收集到测试、导入失败或静态推断。

## 4. 正式关系安全

新 E2E 同时要求：两项模型提案均 SUPPORTED / EVIDENCE_VALID，Reviewer 接受执行及证据；
但每项仍为 `SEMANTIC_NOT_EVALUATED`、`publishable=False`。
Product 正式关系、正式结论和日期 comparison 为空；配置的缺失 P4 sidecar 不得被创建。
所有 P4 步骤仍 BLOCKED，保留 `SOURCE_SCOPED_OPERATION_UNSUPPORTED`，无 assessment ID。
EVIDENCE_VALID 从未被当作语义批准。

## 5. 来源、权限、恢复与失败

- 既有 Host hot/cold 测试现在从带来源句柄的 G 计划开始：来源更新、单独撤销 evidence、
  切换 Workspace 后，分别 BLOCKED / 409；失效结果不展示 evidence、trace 或旧 task_review。
- `test_task_relation_semantic.py` 保留伪造引文、未知关系端点和错误授权校验，
  加入只授权部分 artifact versions 的拒绝控制，均不发起内层请求。
- 新 E2E 使用新服务读取已落盘结果，task_review 完整一致；标明
  `SAVED_RESULT_READ_ONLY` / `session_resumed=False`，语义请求仍只有一次。
- 原恢复测试继续覆盖响应持久后恢复不重复调用，以及 Runtime checkpoint 中断后的不重复计费。
- 原 UNKNOWN / REFUTED / CONFLICT 不被提升为正式关系；FAILED、UNCERTAIN、预算耗尽，
  缺失/篡改 Provider receipt 均保留真实阻断，无自动付费重试。
- 未注册 semantic 工具使用原 ToolRuntime 未知工具参数化测试，必须返回 TOOL_UNKNOWN，
  不创建 pending Ledger、不消耗调用配额。

## 6. CI 与本地验证

在 `.github/workflows/offline-pr.yml` 既有 `task-investigation` job 的安装步骤后，
加入命名为 **M3-R Product semantic tool chain (offline, loopback HTTP only)** 的 step：

```sh
python -m pytest -q --junitxml=m3r-semantic-chain.xml \
  tests/integration/test_m3q_provider_host.py \
  tests/unit/test_task_investigation_semantic_projection.py \
  tests/unit/test_tool_ledger_runtime.py::test_unknown_tool_does_not_create_pending_record
```

Host 文件从后续旧大命令移出，避免重复；同一 artifact 上传步骤增加
`m3r-semantic-chain.xml`，没有第二套框架或额外 job。

环境沿用 job：Python 3.11、`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`、
`LINKLOOM_RETRIEVAL_MODE=bm25`、`PYTHONPATH=src:.:tests/integration`，Provider Key 为空。
本地为 Linux / Python 3.12，按现有 `.[dev,smoke]` 依赖执行；新专项本身也在未安装 Provider SDK 时通过。
所有数据库和来源均在 pytest 临时目录创建。

最终本地验证：专项 **26 passed / 35.93s**；既有 task-investigation 安全回归
**564 passed / 69.06s**，合计 **590 passed、零 skip/xfail**；`git diff --check` 通过。
最初安全回归缺少既有 smoke SDK 依赖，补齐后仍有一项 SDK mock 测试受本机 SOCKS
代理缺少可选 socksio 影响；最终安全回归仅在测试子进程清除代理变量后全通过。
没有改 Provider、SDK 测试或仓库配置来规避失败。
工作流仅监听 master PR 与 workflow_dispatch；本轮不创建 PR、不 dispatch，
没有远端 Actions 运行结果，不能声称远端 CI PASS。

## 7. 独立复审

只读 Reviewer 独立检查 diff、关联生产流程和故障实验记录，并独立运行专项与语义安全测试：
41 passed、无 skip/xfail，`git diff --check` 通过；诊断增强后独立复跑主 E2E
1 passed、最终 freshness 三项 3 passed。最终只读结论 **PASS，P1=0、P2=0**。
未删除原断言、添加跳过或降低生产门槛。

## 8. Git

正常 commit / 非强制 push 仅针对 `codex/m3r-semantic-ci-gate`。
最终提交与远端回读 SHA 由任务回执提供；不自动创建或合并 PR。

## 9. 剩余边界

1. LIVE-only：scripted 输出验证调用链和安全边界，不能证明真实模型会生成正确计划、
   主动选择正确工具或作出正确语义判断；本轮没有真实模型评测或费用。
2. 当前恢复机制在 Ledger terminal / observation 尚未提交的中断缝隙中仍会 fail closed；
   既有回归确认不重复语义请求，本轮没有扩大为恢复实现修复。
3. 未触发远端 Actions；GitHub runner 上的执行结果需后续 PR 或显式 workflow_dispatch 验证。
