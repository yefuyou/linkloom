# 证据索引

最终主目录：[final-20261025-2005](../../../output/playwright/task-relation-review/final-20261025-2005/)。主流程在最终两项安全修复后重新执行，`result.json.status=PASS`。截图全部由真实 Chromium 截取，没有合成截图。

## 主浏览器与来源

| 文件 | 用途 |
|---|---|
| [environment.json](../../../output/playwright/task-relation-review/final-20261025-2005/environment.json) | Chromium 155.0.8059.85、工作区及离线模型传输标识 |
| [actions.jsonl](../../../output/playwright/task-relation-review/final-20261025-2005/actions.jsonl) | 按 UTC 时间记录 UI 填写、点击、截图与 Host 重启；API 重放明确标为 ADVERSARIAL_API_CHECK |
| [http.jsonl](../../../output/playwright/task-relation-review/final-20261025-2005/http.jsonl) | 146 条实际请求/响应，包含 method、URL、request_body、status、body |
| [console.jsonl](../../../output/playwright/task-relation-review/final-20261025-2005/console.jsonl) | 7 条 HTTP 409 资源错误；0 pageerror。6 条为负向验收，1 条为空空间的 review/candidates 初始化请求 |
| [result.json](../../../output/playwright/task-relation-review/final-20261025-2005/result.json) | 真实回执、两项 canonical assessment、一项 comparison、精确重试及跨空间拒绝结果 |
| [source-inputs.json](../../../output/playwright/task-relation-review/final-20261025-2005/source-inputs.json) | UI 实际导入的两份会议文本，B 引用 UI 导入 A 后分配的唯一 source ID |
| [verification-summary.json](../../../output/playwright/task-relation-review/final-20261025-2005/verification-summary.json) | HTTP 行号索引；3 个来源版本 SHA、80 项原文 span/子片段检查全部匹配；0 外部请求 |
| [original.har](../../../output/playwright/task-relation-review/final-20261025-2005/original.har)、[original-trace.zip](../../../output/playwright/task-relation-review/final-20261025-2005/original-trace.zip) | 原 Demo 的真实交互网络与 Trace |
| [positive.har](../../../output/playwright/task-relation-review/final-20261025-2005/positive.har)、[positive-trace.zip](../../../output/playwright/task-relation-review/final-20261025-2005/positive-trace.zip) | 正向、刷新、冷重启、历史、跨空间、降级及冲突 |
| [source-change.har](../../../output/playwright/task-relation-review/final-20261025-2005/source-change.har)、[source-change-trace.zip](../../../output/playwright/task-relation-review/final-20261025-2005/source-change-trace.zip) | 第二个独立 Chromium context 通过 UI 修改/移出来源 |
| `*-server.log` | 原/正向/冷重启的 Product 进程输出；进程已停止，数据库保留在 `work/task-relation-review/final-20261025-2005/` |

Trace 可用本机 Playwright Trace Viewer 打开。HAR 内嵌响应内容。来源核验从已捕获 HTTP 原文及其 SHA 重算，不依据屏幕转录或模型概述；80 项包含重复引用，不代表 80 条独立任务。

## 真实截图

以下文件位于主目录，每张同时有 `.txt` DOM 文本记录。

| 截图 | 对应操作与结论 |
|---|---|
| [01-original-review-entry.png](../../../output/playwright/task-relation-review/final-20261025-2005/01-original-review-entry.png) | 原示例现在有人工核验入口、两端任务与缺口 |
| [02-original-still-unknown.png](../../../output/playwright/task-relation-review/final-20261025-2005/02-original-still-unknown.png) | 保存人工声明后，独立检查仍正确 UNKNOWN/BLOCKED |
| [03-original-history-after-reload.png](../../../output/playwright/task-relation-review/final-20261025-2005/03-original-history-after-reload.png) | 页面重载后回读 REVIEW 与 BLOCKED 审计 |
| [04-positive-imported-via-ui.png](../../../output/playwright/task-relation-review/final-20261025-2005/04-positive-imported-via-ui.png) | 两场会议实际导入；原 UX02 的 FAILED 标签仍可见，未修复或掩盖 |
| [05-positive-before-proof-capture.png](../../../output/playwright/task-relation-review/final-20261025-2005/05-positive-before-proof-capture.png) | canonical 来源判断与尚无持久 proof 分层展示；断言 relation sidecar 尚不存在 |
| [06-source-proof-committed.png](../../../output/playwright/task-relation-review/final-20261025-2005/06-source-proof-committed.png) | UI CONTINUE 首次创建 proof，真实 COMMITTED 回执 |
| [07-new-investigation-dates-and-related-history.png](../../../output/playwright/task-relation-review/final-20261025-2005/07-new-investigation-dates-and-related-history.png) | 新 Runtime 日期差 4，相关旧 run 历史明确标记 |
| [08-positive-reload-retains-history.png](../../../output/playwright/task-relation-review/final-20261025-2005/08-positive-reload-retains-history.png) | 刷新页面后重新打开调查 |
| [08b-cold-host-retains-source-proof-and-history.png](../../../output/playwright/task-relation-review/final-20261025-2005/08b-cold-host-retains-source-proof-and-history.png) | 真正停止再启动 Host 后回读同一新调查 |
| [09-local-withdrawal-keeps-source-proof.png](../../../output/playwright/task-relation-review/final-20261025-2005/09-local-withdrawal-keeps-source-proof.png) | 本地撤回回执，独立日期证明不被撤销 |
| [10-other-workspace-no-task-data.png](../../../output/playwright/task-relation-review/final-20261025-2005/10-other-workspace-no-task-data.png) | 切换空间后拒绝旧空间读取 |
| [11-second-browser-updates-source.png](../../../output/playwright/task-relation-review/final-20261025-2005/11-second-browser-updates-source.png) | 第二浏览器编辑来源，产生新版本 |
| [12-previous-positive-result-invalidated.png](../../../output/playwright/task-relation-review/final-20261025-2005/12-previous-positive-result-invalidated.png) | 旧正向结果、原文与可能含旧引文的备注清除 |
| [13-conflict-support-and-counter-evidence.png](../../../output/playwright/task-relation-review/final-20261025-2005/13-conflict-support-and-counter-evidence.png) | 重新调查后明确 CONFLICT，显示支持与反证，日期不计算 |
| [14-source-removed-from-workspace.png](../../../output/playwright/task-relation-review/final-20261025-2005/14-source-removed-from-workspace.png) | UI Remove from workspace；没有物理文件删除 |
| [15-removed-source-clears-old-evidence.png](../../../output/playwright/task-relation-review/final-20261025-2005/15-removed-source-clears-old-evidence.png) | 来源撤回后拒绝旧核验并清除引用 |

额外 [default-host](../../../output/playwright/task-relation-review/final-20261025-2005/default-host/) 含原 Demo 两份来源页面截图、SHA/quote 核验、`disabled-host-retains-answer.png`、HTTP/Console `result.json`、HAR 与 Trace，验证默认关闭核验不会抹掉原有任务答案。

## 关键 HTTP 行号

`http.jsonl` 按从 1 起的行号定位，原始请求和响应在同一行：

| 行号 | 操作 | 实际响应 |
|---|---|---|
| 15 / 16 | 原示例 REVIEW / CONTINUE | 200 RECORDED / 409 BLOCKED，INSUFFICIENT_IDENTITY 等 |
| 61 / 62 | 正向人工声明 / 日期 CONTINUE | 200 RECORDED / 200 COMMITTED，首次捕获来源证明 |
| 63 / 64 | 原请求精确重放 / 改写同 request_id | 200 COMMITTED、相同 revision 2 / 409 REQUEST_ID_COLLISION |
| 95 | 本地 WITHDRAW | 200 RECORDED，正式关系生命周期未改变 |
| 102 | 切换 Workspace 后读旧空间 | 409 WORKSPACE_MISMATCH，无原任务数据 |
| 121 | 来源更新后的旧 REVIEW | 409 TASK_REVIEW_UNAVAILABLE，无旧回执成功 |
| 143 | 明确冲突后日期 CONTINUE | 409 BLOCKED，CONFLICTING_IDENTITY_EVIDENCE 等 |
| 146 | 来源移除后的旧 REVIEW | 409 TASK_REVIEW_UNAVAILABLE |

精确重试与跨空间探测是浏览器内明确标记的 API 负向检查；没有把 fetch 重放冒充用户提交主流程。source-update/remove、REVIEW/WITHDRAW/CONTINUE、重新调查、刷新与重启后的重新打开均使用真实界面。

## 回归、审查及兼容证据

| 文件 | 结果 / 实际范围 |
|---|---|
| [proof-contracts.xml](evidence/proof-contracts.xml) | 111 PASS，既有有限证明、store/query 合同；核心文件未改，未重复凑覆盖率 |
| [focused-final-green.xml](evidence/focused-final-green.xml) | 13 PASS、7 FAIL；文件名不是通过标志。7 个失败是 Windows 长路径下 artifact_write_failed |
| [focused-network-short.xml](evidence/focused-network-short.xml) | 对上述 7 个受影响用例使用短 basetemp 后 7 PASS；合计 20 项焦点检查通过 |
| [commit-boundary-final.xml](evidence/commit-boundary-final.xml) | 最后两项安全修复后 2 PASS：持续 audit 失败、fresh capability、幂等恢复、捕获窗口真实撤回、STALE 后两个 proof status 同降级 |
| [ui-final.json](evidence/ui-final.json) | 最终 JS 语法与 4 组 DOM 全 PASS，含默认 Host、失效结果清理、双语和 proof 状态分层 |
| [pr25-final-overlay.json](evidence/pr25-final-overlay.json) | #25 精确 head 的影子目录实际应用补丁后，8 项 apply/语法/DOM 检查 PASS；不是该 PR 浏览器验收 |
| [git-boundary.json](evidence/git-boundary.json) | 分支/HEAD、生产文件 SHA、Git diff 范围；8 个核心/重叠保护文件无变更 |
| [final-browser-manifest.json](evidence/final-browser-manifest.json) | 最终浏览器归档每个文件的长度/SHA，最终生产文件哈希与冻结版本一致 |
| [original-evidence-hashes.json](evidence/original-evidence-hashes.json) | 原 Demo 96 个归档文件与原 manifest 全匹配 |
| [REVIEW.md](REVIEW.md) | 独立只读安全审查，曾发现两项 P1，最终修复后 PASS |
| [recovery_worker.md](evidence/recovery_worker.md) / [backend_worker.md](evidence/backend_worker.md) | Worker 的 RED/GREEN 命令、故障事实与最终回归记录 |

## 失败尝试没有被覆盖

- `http-first.xml`：实际新接口签名错误，后来修复；`http-green.xml` 为对应回归。
- `recovery-first.xml`：proof 写入后 receipt 失败的重试不能恢复，后来修复。
- `commit-boundary-red.xml`：COMMITTED audit 失败后的原 capability 恢复问题，后来修复。`commit-boundary-first.xml` 是环境 PYTHONPATH 收集错误。
- `focused-final.xml`：8 PASS、12 个 localhost WinError 10013，属于沙箱网络阻断；不是产品失败或通过。
- `host-composition-red.xml`：名字来自原计划的 RED 步骤，实际结果 **2 PASS**，未冒称先失败。
- `baseline/`：实施前原 P1 的真实 Chromium 截图、HAR、Trace。
- `current/`：早期交互截图与 HTTP/Console/actions 仍保留；会话被中断，**HAR/Trace 未封存，不算最终完整证据**。
- `final-20261025-1945/`：沙箱中的 Chromium 启动/通信受阻，无完成验收，不算 PASS。
- `final-20261025-1950/`：已封存 HAR/Trace；验收脚本错误等待 `/api/sources`，实际导入是 `/api/sources/import`。导入成功，未因原 UX02 标签误判导入失败。
- `final-20261025-1955/`：正向与冷重启通过，随后脚本未按页面提示刷新来源范围，收到 INVESTIGATION_CONTEXT_CHANGED；不是伪造 source-update 成功。
- `final-20261025-1958/`：完整浏览器 PASS，但早于最后两项安全差量；最终版本改用 **2005 整轮重跑**。
- PR25 第一次嵌套目录 `git apply` 忽略了根路径，组合检查因此失败。最终显式 `--directory` 应用到影子目录再核验，记录在 JSON，不宣称原来已正确应用。

所有失败材料保留本地，没有改写原 Demo、冻结实验、Gold 或历史评测。

## 可复现命令

在本分支工作区，使用已有 Python Playwright 与 Chromium/Chrome：

```powershell
$env:PYTHONPATH = Join-Path (Get-Location) 'src'
$env:PYTHONUTF8 = '1'
python docs/requirements/task-relation-review-closure/browser/acceptance.py --label unique-local-label
python docs/requirements/task-relation-review-closure/browser/summarize_evidence.py unique-local-label
python docs/requirements/task-relation-review-closure/browser/default_host.py unique-local-label
```

每个 label 必须是新目录，以保留已有证据。脚本只启动自己拥有的本地 Product 进程，结束时停止进程；数据库/证据保留。正向 host 只脚本化模型传输，未导入或调用 TaskRelationStore 来预置证明。复现需要允许 localhost 与隔离浏览器进程通信，不需要 Provider 或 Key。
