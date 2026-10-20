# M3-P 产品交付

基线：远端 `codex/linkloom-m3o-ab-checkpoint` 的
`adedbb323cf39a417b2048bd9d1fab60145b5718`。本轮独立分支：
`codex/linkloom-m3-p-task-investigation-closure`，工作树 `work/m3p`。
最终远端 SHA 由交付消息和本地 Git 回读记录提供。

改动文件：`ui/task_investigation.py`（来源目录绑定及冷读重建）、
`ui/investigation.py`（Host 接线与保存结果读取）、`ui/task_result.py`
（既有对象的审查投影）、`ui/unified_ask.py`（现有 HTTP 路由回读）、
`ui/static/product.js`（原 Ask UI）、`tools/task_relations.py`（P4 依赖读取）。
另含两份测试文件、公共合成 HTTP fixture、离线验收脚本、路线图及本目录证据。
没有新建前端应用、Runtime、关系权威存储或通用编排层。

## 用户现在多能完成什么

以前，林枫的两场公开合成会议调查只显示通用“仍需确认”、计划问题和技术轨迹。
现在 Ask 直接展示两条独立观察、各自负责人和日期字段、可展开的原始消息、字段
证据，以及模型 SAME_TASK 提案的支持／反对／缺失证据。用户可以直接打开原来源。
页面明确解释模型建议为何不能解锁后续计划修订，并说明需要补充什么。

该例原文保留 `2029-06-20` 和 `2029-06-24`；现有 F 对第二条“截止日期调整为”
仍返回 `DEADLINE_ROLE_UNRESOLVED`。产品将第二条结构化截止字段显示为未知，
没有凭展示层重新解析日期或把它宣布为当前正式截止日期。

另一个公开 P3 输入案例是陈晨的星河项目 API-4：原计划在 `2026-10-14` 前完成，
修订计划在 `2026-10-18` 前完成。测试准备阶段用原 P1/P3 评估函数建立既有 P4
记录，产品执行只读，不自动种入关系。两项关系及比较记录均可重验时，实际
Runtime 查询支持身份和修订依赖，并返回 4 个日历日的计划边界差值。
`plan_currentness=NOT_ESTABLISHED` 仍被保留：可核算差值不等于当前已正式生效。

## 确认与阻断边界

| 角色 | 实际责任 |
|---|---|
| Model | F/G/语义层提出观察、调查步骤和关系候选；输出不是独立业务确认 |
| Host | 绑定 Workspace、完整 ObservationRef、来源／版本、范围、预算与可用工具；展示真实回读与阻断原因 |
| Runtime / Reviewer | 原工具执行及持久账本；重验回执、来源和 P4 回读完整性；不赋予模型业务授权 |
| P4 | 原 RelationQuery 重放既有 P1/P2/P3 证明、生命周期和前提；只有当前有效且端点／Predicate 匹配的记录可满足步骤 |
| Human | 本轮没有代替真人批准。现有 lifecycle actor 字符串不构成认证与授权；不存在可安全复用的关系确认接口，因此没有新增批准按钮 |

每次热读及冷读均重新使用原 `review_task_relation_result`，重新核查实际 Tool
Ledger、inspect、P4 query 和持久语义证据。来源变化、撤权或关系撤回后，旧
结果不再返回引用、轨迹或审查视图。当前 relation sidecar 路径只由本地 Host
按 Workspace 注入，不进入模型或 HTTP 参数。产品没有打开写入关系的 Store。
独立审查发现并修复了一项证据目录撤权 P2：即使原文和版本未变，所有原始
EvidenceSpan 也必须仍属于产品当前证据目录，不能从保留文本重新创建权限。

## 哪些步骤真正执行

- continuity/conflict/replan：F、G、inspect、P4 query、语义提案分析实际执行；
  无可信身份记录时 SAME_TASK 保持阻断，REVISES_PLAN 不继续。
- missing：有界 F、UNKNOWN 回执和 G replan；inspect/query 实际执行；未调用
  语义层，不把缺候选解释为任务不存在。
- 只有 identity 的既有 P4：身份读取有支持，revision/date 继续阻断。
- 两项关系和比较齐备：原 query 工具内完成对应依赖读取及比较重验，不新增执行器。
- 新的语义修订提案、clarify 对话、再次 F 扫描或任意自动 G 重规划没有新增；
  已有身份不能凭空补出尚不存在的修订关系。
- unsupported time/source scope 明确阻断，不静默全量查询。

## 验证证据

`evidence/before-http.json` 保留指定基线通过真实 localhost Product HTTP 得到的
四场景结果；不是依据文档猜测的 Before。`evidence/after-http.json` 保留七场景
状态、真实工具、步骤和完整工件哈希；`evidence/http-examples.json` 是精简响应
投影。完整未裁剪响应、Runtime ZIP、Before 响应及测试原始结果打包为
`evidence/offline-http.zip`。最终修复后的七场景位于包内 `after-review-http/`，
同时保留在本工作树 `work/after-review-http/`；修复前的执行材料也保留。
ZIP 包含原始合成来源、Product SQLite、P4 sidecar（若有）、请求／响应、Checkpoint、
Tool Ledger、来源恢复和 Reviewer 工件。工件内容不会整体暴露在产品答案中。

浏览器实际操作：现有 Ask 表单提交原始中文问题 → 检查独立任务字段及关系提案
→ 展开第二场原文 → 点击“查看来源”打开对应 exact evidence。截图在
`evidence/browser-original.png`。这是纯 FakeModel 浏览器接线验收，不是真实模型
语义验收，不是部署或 CI。

可复现命令（工作树根目录；输出／临时目录需使用新的路径）：

```powershell
$env:PYTHONPATH='src;.'
$env:PYTHONIOENCODING='utf-8'
python scripts/run_m3p_offline_acceptance.py --output work/my-new-http-run
python -m pytest tests/integration/test_m3p_product_review.py tests/unit/test_task_investigation_p4_steps.py -q --basetemp=work/new-tests
```

独立审查修复后的最终回归 **45/45 通过**，其中包含全部 **18 项**当前新增
专项；此前新增专项 **16/16 通过**，关联 15 文件 **211/211 通过**。
这些批次包含重叠用例，不累计成独立测试总数。Python compileall、JavaScript syntax 和
`git diff --check` 通过。未重跑全仓库测试；旧 M3-O 记录的更大范围问题未被
扩展修复，也没有全仓库绿色的结论。
测试和独立审查最终结果记录在 `evidence/verification.json` 及 `REVIEW.md`。
独立复审最终 **PASS，无未关闭 P1/P2**；首轮发现的一项证据撤权 P2 已修复。
本轮真实 Provider、Gemini、countTokens 请求均为 **0**。
`REAL_MODEL_SEMANTICS=NOT_EVALUATED`。

中间执行记录也保留：首轮沙箱阻断 localhost（WinError 10013），部分旧轮询
20 秒超时；解限后的基线 HTTP 成功。新增 cold-read RED 为 RUN_NOT_FOUND；
修复了持久语义调用身份中 `0` 与 `0.0` 的精确哈希差异。一次跨进程回读曾
返回 BLOCKED，相同已保存工件后续独立回读及新运行复测均通过；不把中间失败
抹去或计作成功。没有改 Gold、删断言或放松 Reviewer。

## 未完成项与下一步

1. 人工确认仍缺具备真实身份、权限、审计及精确证据绑定的业务审查接口。
   跨文档自由语义身份、来源冲突的业务裁决和正式取消不由本轮替人决定。
2. 冷启动只读取并重验本轮保存的已完成结果；不自动恢复完整 F/G 会话，也未增加
   另一套 Checkpoint。旧版本没有 run identity pointer 的结果不在回读范围。
3. 既有有限 P4 证明之外的关系仍是模型提案；日期字段的保守提取和不支持的范围
   未扩展。没有“所有跨会议任务都能闭环”的结论。

可以作为下一轮离线／受控产品验收基线；不能称为真实模型业务验收通过。
若验收目标包含人工确认，最少还缺第 1 项；若目标包含真实模型理解，需另行
明确授权并配置对应 Provider 路径。本轮没有发起这类调用。

后续建议仅两项：建立受控关系审核合同；随后单独授权少量真实模型产品验收。
