# P1 主线集成与独立 Draft

本报告追加 2026-10-25 集成事实。上一轮 DELIVERY、DEMO_ACCEPTANCE、DEMO_SCRIPT、EVIDENCE_INDEX、REVIEW 及全部 Chromium 证据保持原样；其“未提交、未推送”描述是上一轮交付时点，不代表本轮新授权的发布状态。

## Git 与集成边界

- 仓库：`yefuyou/linkloom`；分支：`codex/task-relation-review-closure`。
- 开始 HEAD：`7720390a796662ff42c27b5eef6f8685e5155802`。
- fetch 实际 master：`f2ce6b82d635a7b78b17a6d100556c354f7a461a`，仅专项分支 `merge --ff-only` 到该提交。未改变本地或远端 master，未改写历史。
- #24 的 `tools/task_relations.py` 与新回归原样来自 master，本专项对该文件没有 diff，未复制另一套状态修复。
- #25 仍为独立 Draft，初检 head `3d4c05025c64aed0f51a15b6aa7d476febc972f8`，执行期间更新到 `aa3473d18b5c4542b665a88df1cfbb7dcea88ed5`（base 也是 f2ce6b8），已重新 fetch 并审查。未修改、合并或混入 #25 分支。
- `PRE_INTEGRATION_MANIFEST.json` 保护原 287 个文档与证据文件；本地 `work/task-relation-review/integration-20261025/` 保留未提交内容 ZIP/patch。所有原证据须在提交前再次逐项核对 SHA。

最终 commit、remote head、base 与实际集成 tree 见 PR 元数据及本地 `work/task-relation-review/integration-20261025/PUBLICATION.json`；生产代码完整差异可由 `git diff <base> <head> -- src/linkloom/ui` 回读。包含原始请求/临时 capability 的完整 HAR/HTTP/Trace 保留本地；本 Draft 仅提交合成来源截图、SHA 核验摘要及必要文档/回归结果。

## 集成新增差量

没有新增产品功能、关系谓词、审批身份、业务状态或证明语法。继续保留已验收的本地人工声明与独立来源证明闭环。当前集成额外修正两项审查发现：

1. 进程内投影缓存限制 32 条，capability 缓存限制 256 条；颁发时清理过期项并逐出最老项，关闭 Host 释放两类内存。逐出只能要求重新取当前视图或返回不可用，不授予权限、不改持久化历史。
2. proof 写入后 receipt 尚未落库的恢复分支，要求 capability 的 Workspace/run/source/catalog/pair 与当前视图一致，并匹配首个 PENDING 审计的原请求指纹。指纹覆盖 actor_label、note、step、run、request ID、revision 及证据绑定，复用现有 details_hash 列；改写主体、备注、步骤或跨空间 capability 不能冒充精确恢复。独立复核进一步发现 fresh capability 和新 run 同 request ID 的绕行，现将指纹校验放在所有 capability 路径之前，并把 PENDING 查询与 statement 的 Workspace/request ID 唯一约束对齐。

CI 注册本专项后端/HTTP/故障恢复/缓存测试及新核验模块 DOM/语法检查，保留原 Core、#24 与冻结检查。没有改 Gold、冻结 Canary、历史模型结果或业务权限逻辑。

## 信任与安全核验

人工 REVIEW/CHECKED 仅是自声明本地核验记录，actor_label 不是认证用户。Host opt-in、localhost/same-origin 和 capability 是本地写操作边界，不宣称 SSO/RBAC。持久回执与审计绑定本地声明主体、当前 Workspace、来源 snapshot/catalog hash 和端点 pair；正式业务批准始终 `false`。

可信关系仍由既有 `assess_same_task`、`assess_plan_revision(new, old)`、`compare_revision` 和 RelationQuery 当前性校验决定。模型建议或人点击不能补足 SAME_TASK、方向、反证、有效时间和日期角色。来源日期 2026-10-14 → 2026-10-18、差 4 天只表示已证实的计划日期边界比较，**不表示当前正式业务计划已批准**。

来源更新、目录撤回、历史 RETRACTED/STALE、冲突及跨空间访问分别测试。本地 WITHDRAW 只追加个人声明事件，保留审计，不撤销独立来源证明。新标识正则仅检查有界的 ID/run ID，没有扩展语义正则或加入重复业务枚举。缓存超限、过期、未知来源和旧引用都按不可用处理。

## #25 的冲突与合入后步骤

直接重叠是 `product.js` 的任务结果 post-render 区、`product.html` script include、`.github/workflows/offline-pr.yml` 检查列表。#25 还改动 task renderer、CSS、i18n 与多个 DOM/Chromium 脚本；P1 对这些原文件保持无业务改动，独立模块负责核验。

`PR25_COMPATIBILITY.json` 记录初检 head 的 13 项 apply/语法/DOM 全通过；`PR25_LATEST_COMPATIBILITY.json` 对最新 aa3473d head 的 11 项检查也全部通过。新 head 已含 #24，因此不重复叠加 #24 补丁。该组合没有作为当前分支产品发布，也没有最终组合 Chromium 验收。#25 自身的 Chromium 验收不能替代 P1 + #25 的最终组合验收。

#25 合并后最小同步：在最终主线保留其语言/布局治理；将核验 hook 放在 `renderTaskAnswer` 后，保留 `askLocale`、view token、Workspace 和失效清理守卫；保留新 script include；并集合并双方 CI 测试注册。随后执行双方 DOM/双语、完整 P1 Chromium 及桌面/移动布局验收，重新核对“审批不可用”文案与受限本地核验的区别。不得以此时的 CI 或影子 DOM 通过代替最终组合验收。

## 原 P2 与剩余合并前提

原三项 P2 保持未修复：UX02 已保存来源显示 FAILED；UX03 离线引导/示例不匹配；UX04 导航到来源后丢失问题/结果。新集成不重构 Sources、旧 task renderer、CSS 或 i18n。

合并前仍需：人工审阅完整生产 diff、本地身份/业务批准边界、#25 最终组合 UI 与 Chromium 验收、最终 head CI 通过。大型新服务模块的结构整理和 CHECKED API/UI 策略一致性仍是已披露限制，不在本轮扩展范围内。

最终交付状态以远端 CI 回读和本轮独立复核为准；#25 未完成最终组合时只能报告 **DRAFT_READY_PENDING_UI_INTEGRATION**，不得自动合并。
