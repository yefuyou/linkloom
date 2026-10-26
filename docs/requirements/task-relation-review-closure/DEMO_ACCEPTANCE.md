# 最终产品验收

**DEMO_P1_RESOLVED** — 2026-10-25。

依据用户本次授权的受限本地人工核验边界，P1 已完成真正的产品操作闭环；不把来源证明解释为正式业务批准。完整说明见 [DELIVERY](DELIVERY.md)，逐项证据见 [EVIDENCE_INDEX](EVIDENCE_INDEX.md)。

最终版本的主浏览器目录为 `output/playwright/task-relation-review/final-20261025-2005/`。Chromium 155.0.8059.85，由 Python Playwright 在独立 context 中运行，无用户浏览器状态。Product 服务及输入均为本地公开合成数据；首次 proof 仅由用户 UI 点击 CONTINUE 产生，脚本没有预先写入 P4。

| 用户要求 | 结果 | 实际验收 |
|---|---|---|
| 1 打开待核验关系 | PASS | 原示例与正向示例均在 Ask 结果中显示关系核验面板 |
| 2 双方任务、支持与反对证据 | PASS | 正向来源原文；冲突版本同时显示 canonical support/counter；状态独立于模型提案 |
| 3 合法人工操作有持久回执 | PASS | REVIEW 真实 POST 200，CONTINUE 真正生成 proof 后 200 COMMITTED |
| 4 重载后可见历史 | PASS | 页面 reload、重新打开上次调查，以及真正重启 Host 后回读同一新 run 与相关历史 |
| 5 满足前提继续修订/日期调查 | PASS | SAME_TASK、REVISES_PLAN 当前有效，重新发起 Runtime 后日期步骤完成，4 天边界差 |
| 6 缺少前提保留 UNKNOWN | PASS | 原青竹示例缺身份/日期前提仍 BLOCKED；模型建议与人工声明不覆盖它 |
| 7 来源撤回、版本变化降级 | PASS | 第二浏览器 UI 更新/移除来源，主页面拒绝旧核验并清除旧结论；catalog-only 撤权另经 HTTP 测试 |
| 8 重复、并发不破坏审计 | PASS | 浏览器精确重试同 receipt；改写重复 409；真实双连接 CAS、proof 捕获中 RETRACTED、持续 audit 故障与恢复经服务/HTTP 回归 |
| 9 跨 Workspace 隔离 | PASS | UI 切换新空间后查询旧空间 run，409 WORKSPACE_MISMATCH，未返回原文/历史 |
| 10 原 P1 复现步骤重验 | PASS | 前测无可用入口；后测可保存/撤回声明、保留失败审计并继续来源检查。证据本身不足时不会得出修订结论 |

正向 Workspace：`ws_b95997c98772497e967bdf4cfe4d6d61`。首次 proof run：`ui-3c0339cf7b86`；重新调查 run：`ui-dd20a4457c5a`。这两个真实 run 的历史被明确区分，旧 run 不参与新 run 的 CAS。

额外默认 Host 回归通过：不加 `--allow-local-task-review` 时，正常任务答案仍可见，只关闭本地核验操作。真实来源页面同时核验原 Demo 两份内容的 SHA 与原文引文，附独立截图、HAR、Trace。

独立审查曾发现重试回退和生命周期投影两项 P1；已修复并最终复核 PASS。主浏览器整轮在修复后重新执行，未用较早的通过截图替代最终版本。

主捕获含 146 条 API 响应、10 次核验 POST；3 个正向来源版本 SHA、80 项原文/子片段检查全部匹配。Console 保留 7 条 409 资源错误：6 条来自明确负向验收，另 1 条为空 Workspace 初始化时已有的候选列表请求；**0 pageerror、0 外部请求**。不声称 Console 全零。

原 Demo 结论仍 UNKNOWN；无真实 Provider、真实企业来源、API Key、真实 vault、认证操作者、组织审批或正式生效计划验收。来源撤回是 Product 的 Workspace 来源移除；不会将它描述为企业 RBAC/SSO 撤权。
