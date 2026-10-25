# P1 交付：任务关系人工核验与修订调查

最终结论：**DEMO_P1_RESOLVED**。

2026-10-25，独立分支 `codex/task-relation-review-closure`。执行初始 master 为 `7720390a796662ff42c27b5eef6f8685e5155802`，HEAD 保持该提交，所有补丁仍在本地工作区。没有 commit、push、创建 PR 或合并 master。

这次解除的是“用户没有实际核验与继续调查操作”的 P1。用户现在可以在任务结果中核对双方原文、支持与反对证据，保存受限的人工声明，再让 Host 独立重验来源合同、持久保存证明并重新调查日期。原青竹示例的证据缺口仍然存在，因此它正确保留 UNKNOWN；不能通过人工确认把它变成已成立关系。

## 实际用户流程

1. Sources 导入 A.md、B.md。正向合成来源明确引用 Product 分配的唯一 source ID，并明确新计划替代旧计划。精确输入见最终浏览器证据 `source-inputs.json`。
2. Ask 选择任务领域，提交“调查星河项目 API-4 的任务延续、计划修订和截止日期变化。”
3. 在核验面板核对任务 A/B、来源合同判断、支持证据、反证与缺失前提。填写自声明本地执行人标签和备注，保存核验声明，获得持久回执。
4. 在日期步骤点击 CONTINUE 独立来源核验。后端重新验证当前空间、能力令牌、任务/关系身份、来源版本/SHA/目录、反证、SAME_TASK、新旧方向及生命周期。只有既有合同独立支持的证明被捕获。
5. 再次提交调查：实际 Runtime 显示 2026-10-14 → 2026-10-18，日历日差 **4**。展开历史可见旧 run 的核验及提交记录；这些只读历史不参与当前 run 的 revision/CAS。
6. 刷新页面，在 Ask 点击“重新核验上次任务调查”。最终验收还真正重启了 Product Host，同一新调查的结果、证明和相关历史仍可回读。
7. 可追加本地撤回事件；它不删除历史，也不调用正式关系的 RETRACTED。来源在第二个浏览器被修改或移出当前空间后，旧任务详情和结论被清除，用户需刷新来源范围再调查。

主证据：[浏览器验收](DEMO_ACCEPTANCE.md)、[证据索引](EVIDENCE_INDEX.md)、[独立复核 PASS](REVIEW.md)、[演示顺序](DEMO_SCRIPT.md)。

## 状态与权限边界

| 概念 | 实际映射 | 含义与限制 |
|---|---|---|
| MODEL_PROPOSED | 原有语义提案字段 | 模型建议，不能作可信关系或业务批准 |
| EVIDENCE_VERIFIED | 精确 span、版本/SHA 与 canonical `source_verdict` | 当前来源合同核验；SUPPORTED 也不等于已持久化或业务批准 |
| HUMAN_REVIEWED | 独立 review journal 的 REVIEW / outcome / note | 本地人工声明，不是来源证明；actor_label 没有认证身份含义 |
| ESTABLISHED | 既有 RelationQuery 的 `current_supported` / `CURRENT_CONFIRMED` | 仅满足相应来源关系合同，保留生命周期、前提与完整证据重验 |
| UNKNOWN / CONFLICT | canonical 来源判断与 blockers | 不被人工 CHECKED、模型建议或历史回执升级 |
| COMMITTED | 操作回执和 COMMITTED audit 均完成 | 表示本次来源证明操作完成，不表示当前正式业务计划已批准 |

没有新增重复的业务状态枚举。无 SSO、RBAC、组织审批或正式业务计划确认；`business_approval=false`。`--allow-local-task-review` 是显式本地 Host 授权，辅以 localhost、同源、服务器颁发且过期的 capability，不宣称生产身份认证。撤权验收覆盖 Product 来源撤回以及 evidence catalog 失效，不能外推为企业身份系统的令牌撤销。

日期比较仍必须满足可信 SAME_TASK、方向正确的 REVISES_PLAN，以及有效 TASK_DEADLINE、DAY 精度和兼容的日期边界。日期不同本身不是修订证据。来源日期可比时，正式业务计划仍明确未确认。

## API、数据库与 UI Diff

| 文件/接口 | 最小职责 |
|---|---|
| `GET /api/product/task-review?workspace_id=W&run_id=R` | 返回当前安全视图、双端任务、canonical 诊断、支持/反对证据、独立 P4 状态、capability、当前/相关历史；失效时隐藏原文和自由文本备注 |
| `POST /api/product/task-review` | REVIEW、WITHDRAW、CONTINUE；固定 run/step，拒绝 verdict、端点、路径或来源覆盖；返回真实回执或阻塞 |
| `src/linkloom/ui/task_review.py` | 新 journal、能力令牌、当前性投影、独立重验/捕获及恢复协议；复用原 assessors、TaskRelationStore、RelationQuery |
| `src/linkloom/ui/server.py` | 两个新入口、Host/Origin 校验、共享 Host relation sidecar 映射、关闭服务资源 |
| `src/linkloom/ui/__main__.py` | 显式 `--allow-local-task-review`，默认关闭 |
| `static/product-task-review.js` | 独立核验表单、分层状态、历史与错误处理；新模块与旧 task renderer 分离 |
| `static/product.js` / `product.html` | 仅 post-render hook 与 script include；原任务 renderer 和 CSS 没有改动 |

POST 字段：`workspace_id, run_id, step_id, action, request_id, capability, expected_revision, actor_label, note`，REVIEW 另带 `outcome=CHECKED|UNSURE|DISAGREE`。

没有修改 Product 主库 schema。新增固定 Host 路径 `<product-db>.task-review.sqlite`，含 metadata、append-only statement/audit 表与禁止更新/删除的触发器；使用 Workspace/request 唯一约束、payload hash、SQLite `BEGIN IMMEDIATE` 和 revision CAS。默认 `<product-db>.relations.sqlite` 与 Runtime 共用；显式可信 Host 映射优先。请求不能选择存储路径；没有明确持久路径的内存 Host 不开放写入。

来源证明库与 review 库不是跨库 ACID 事务。实现 PENDING → 既有 proof writer → RelationQuery 回读 → receipt/COMMITTED audit；失败保留审计并阻塞，精确 capability/request 可安全重试。持久化收据和 audit 完成前不返回成功，重试不重复证明。最终 proof-head 校验与提交窗口受关系库事务保护；外部 lifecycle 变化会拒绝或降级。人工撤回仅进入本地 journal。

## 验证结论

- 最终 Chromium **155.0.8059.85**，实际 UI 导入与提交，无 P4 预置；完整 HAR、Trace、截图、HTTP 和 Console 已封存。
- 原 Demo P1 步骤重新验收：现在可人工记录、刷新回读与继续检查；原证据不足仍阻塞。
- 正向首次 CONTINUE 捕获两项 canonical 关系证明、一项日期比较；新 Runtime 显示 4 天边界差及旧 run 历史。冷 Host 重启与页面刷新均通过。
- 两个真实浏览器验证版本变化清除旧正向结论；明确反证产生 CONFLICT，日期阻塞；移除来源后旧证据清除；跨 Workspace 不返回任务数据。
- 20 项焦点 Python 检查通过；最后两项重试/生命周期修复又经独立 XML 回归通过。既有 111 项 proof/store/query 合同通过且核心文件未改。最终 4 组 DOM、2 项 JS 语法与 PR25 组合检查通过。
- 原验收归档 **96 个文件哈希全部保持一致**。失败尝试和环境阻塞均保留并解释，没有改写历史通过率。

## 与 #24、#25 的关系及 Draft 条件

执行期间 [PR #24](https://github.com/yefuyou/linkloom/pull/24) 已合入 master，origin/master 为 `f2ce6b82d635a7b78b17a6d100556c354f7a461a`；本分支没有合入或复制其 `tools/task_relations.py` 修复。正式集成时仍需基于主线同步并验证。

[PR #25](https://github.com/yefuyou/linkloom/pull/25) 最后回查仍 OPEN Draft，head `3d4c05025c64aed0f51a15b6aa7d476febc972f8`。对该 head 的真实影子目录使用显式 `git apply --directory` 叠加本次 hook/include，再运行新模块和该 PR 的 DOM 检查，全部通过。没有覆盖它的 renderer、CSS 或文案重构。这是语法/DOM 组合验证，不声称 #25 的完整浏览器验收或无冲突自动合并。

**具备独立 Draft PR 的本地代码、SPEC、测试与证据条件。** 尚未创建或推送；未来集成须同步最新主线并复查 #25。大型浏览器证据保留本地，未上传或公开发布。

## 保留的限制

原验收的三个 P2 原样记录、没有顺手修复：UX-02 已保存来源显示 FAILED；UX-03 离线引导/示例与能力不一致；UX-04 来源导航丢失问题/结果。

独立复核另外记录了非阻塞限制：恢复重试受原 capability 生命周期约束；直接 API 的 CHECKED 声明策略比 UI 宽，但绝不升级证明；新服务单文件较大，应另行做结构整理。都没有扩大本轮范围处理。

没有评估真实 Provider 的语义质量。正向浏览器使用明确标识的离线模型传输；会议、模型提案与调用回执的来源分别可核对，未伪造模型调用或正式审批身份。
