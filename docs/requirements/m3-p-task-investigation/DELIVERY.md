# M3-P 主线产品集成交付

分支：`codex/m3p-mainline-integration`。干净基线：`ca2e775ce17581f8b69b2e1f9daf813a1230fb1c`。选择性移植来源：`dd76a25bd9e70544c8e0534538b694015928fea1`。本轮授权止于 Draft PR，不自动 Ready、合并或发布。

## 完成的产品增量

现有 Ask 表单新增显式任务调查域，展示每场会议独立的任务观察、来源版本、负责人/日期角色、字段证据、UNKNOWN 和问题。关系模型提案、有限 P4 可信读取、依赖阻断和下一步分别呈现；查看来源可打开实际证据。没有经过认证的业务关系确认按钮。

普通启动能够显示能力未启用状态，并在不配置模型的情况下冷读既有完成结果。新任务执行仅在显式 `--offline-task-demo` 中通过合成来源和 FakeModel 启用；现有 Provider 开关不授予任务阶段权限。实际普通 CLI 子进程冷读验证了 available=false、external_provider=false、SAVED_RESULT_READ_ONLY、session_resumed=false。

冷读逐次重验 Workspace、来源/版本/hash、**当前 Evidence Catalog 成员资格**、P4 lifecycle、Ledger 和 Reviewer；同文本同版本的证据撤权也会阻断历史发布，不重新调用 F/G 或模型。

## 移植、依赖与架构检查

[DEPENDENCIES.md](DEPENDENCIES.md) 列出 master、F/J/N、G/K、E、I、P1/P2/P3/P4 和 M2 的实际关系。[production-files.json](production-files.json) 为全部 **63 个生产文件**记录来源和 SHA256，其中 47 个与旧分支内容一致（忽略换行），其余为必要主线接线/兼容修复。没有合并旧分支的开发历史。

复用原 RuntimeEngine、SingleAgentModelLoop、ToolRuntime、Ledger、Grounding、Reviewer 和 typed InvestigationPlanner；M1 task payload 扩展原 candidate/receipt 路径，不增加第二套任务权威库。保留 master Recovery 行为；本次修复 unresolved-time 的错误优先级，原 Core 回归通过。

未移植 Canary LIVE grants/ledger、旧 AB/checkpoint/评测平台、adaptive planning 补丁和大批历史证据。仅保留三个原始公开 replay 小文件供既有 Context/Reviewer 合同重放，来源未改写。Profile B 不接入、不改全局默认；旧 13 对 A/B 不是本主线成绩。

**M2 H01–H04 仍为 0/4 FAIL**。单独通过审查的 P4 有限显式输入 store/query/lifecycle 合同不等于 M2 语义泛化验收。本轮只接入该有限只读证明重放；ACTIVE 标记或模型 SUPPORTED 不能单独放行步骤。API-4 需匹配 SAME_TASK 和有向 REVISES_PLAN 后才给出 2026-10-14→18 的 4 天差值，plan_currentness 保持 NOT_ESTABLISHED。

## 真实 HTTP Before / After

[Before](evidence/before-http.json) 来自实际 master 源码归档运行的 POST /api/product/ask：同一公开林枫会议和用户原问题得到 HTTP 200 / NEEDS_SCOPE / UNSUPPORTED_QUERY，无任务观察。

[After 请求](evidence/after-request.json) → [最终结果](evidence/after-final.json) → [新进程冷读](evidence/after-cold.json) 是实际 Product HTTP 链路：合成源 → 显式 tasks/snapshot admission → F 观察 → G typed plan → 原 Runtime/FakeModel → inspect_task_context/query_task_relations/analyze_task_semantics → Ledger/Grounding/Reviewer → 引用式 PARTIAL。Reviewer accepted=true；两条观察分别显示负责人林枫；首个 deadline 为 2029-06-20，第二条原文保留 2029-06-24 而 deadline=null/UNKNOWN/DEADLINE_ROLE_UNRESOLVED。缺少可信 SAME_TASK 时 revision 保持 BLOCKED，不编造业务结论。

[实际浏览器截图](evidence/browser-task-investigation.png) 显示任务观察、来源、提案和阻断；[点击查看来源后的截图](evidence/browser-opened-source.png) 展示实际版本及引文。浏览器 errors=[]；DOM 文本一并保留。截图为当前版本真实运行结果，未伪造模型能力。

[完整合成 HTTP/Runtime 证据包](evidence/offline-http.zip) 包含前后响应、合成数据库、准备阶段、持久化 Runtime/Checkpoint/Ledger、普通 CLI 冷读和原始 JUnit；[archive-manifest.json](evidence/archive-manifest.json) 记录各文件及归档 SHA256。无真实 Provider 请求、countTokens 或 LIVE 评测。

## 验证与实际失败

[verification.json](evidence/verification.json) 保留重叠批次及 hash：**839 个不同 observed test nodes 的最近一次结果通过**，不是一次全仓库套件成绩。

| 检查 | 实际结果 |
|---|---|
| 最终 Core | 270 passed，2 deselected |
| Task 全专项 | 529 passed / 1 failed；比较 projection 修复后对应 P4/HTTP 13 passed |
| Legacy Gate | 4 passed，20 deselected，原历史输入不变 |
| Frozen Ledger | 5 passed，原字节/Seal 保护不变 |
| Unified Ask | 30 passed |
| 权限、预算、无进展、UNKNOWN HTTP | 5 passed |
| SDK 不能伪装 FAKE/REPLAY | 4 passed |
| 材料化兼容/authority | 41 passed |
| DOM、JS syntax、Python compile | passed |
| Wheel 构建 | passed；远端 CI 另对最终提交构建 |
| 严格 HTTP 合成链路/普通 CLI 冷读 | PASS；不恢复模型会话 |

已遇到并保留的失败包括：初次移植缺少公开 fixture/import；LIVE-policy RED 未拒绝；Core unresolved-time 错误优先级；identity-only compare step 投射了不完整前提 ID（均已修复并有后续通过证据）。沙箱临时目录/回环权限、Worker 单独一轮 5 个 Windows socket timeout、浏览器脚本 CSP/定位/编码及 CLI 验证脚本 config 封装路径错误属于环境或验证脚本失败，均未通过删断言、skip、xfail、改 Gold 或放宽安全边界解决。Worker timeout 对应场景在主 Agent 最终专项中通过。旧失败没有改写成历史通过。

端到端覆盖：独立会议观察、同名异任务不继承字段、未知日期角色、无身份证明阻断、有效 P4 按前提读取、来源 update/remove/关系撤回/当前证据撤权不泄漏、可信 PARTIAL、真实执行失败不冒充成功、跨进程只读回看和原产品 Recovery/Decision/Reviewed State/Source Relations 回归。

## 独立复审与建议

[REVIEW.md](REVIEW.md)：独立只读 Reviewer 最终 **P1=0/P2=0**；检查任务身份、证明升级、冷读撤权、Workspace 越界、失败状态、单一 Runtime/Planner、有界上下文及代码/资源边界。Reviewer 没有运行全仓库测试；其 Windows 临时目录受阻尝试和使用的主 Agent 证据均已注明。

本轮产品集成验收满足；真实任务 Provider 语义能力、业务关系授权、M2 泛化、Profile B 和部署均未验收。建议将此作为**显式离线可用、默认严格关闭的产品增量**供 Draft 审查，在最终 GitHub Actions 通过后考虑合入；不建议宣称真实模型任务调查已可用。下一增量是任务阶段显式 Provider 能力/来源合同及经认证的关系人工审查。

## 复现

使用新建的合成数据库和输出目录：

```powershell
$env:PYTHONPATH='src;.;tests/integration'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m linkloom.ui --offline-task-demo --database work/my-new-demo.sqlite --port 8765
python scripts/run_m3p_offline_acceptance.py --output work/my-new-http-evidence
python -m pytest tests/integration/test_m3p_product_review.py tests/unit/test_task_investigation_p4_steps.py -q
node tests/ui/unified_ask_dom_test.cjs
```

PR、最终 Head 和 GitHub Actions 收据由本轮 Draft PR 及最终交付消息给出；不得把本地通过当作远端 CI 通过。
