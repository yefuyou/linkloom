# 集成验证记录

Base master：`f2ce6b82d635a7b78b17a6d100556c354f7a461a`。最终生产模块 `task_review.py` SHA-256：`e08877ab443882a2cb423fd8eed5816b82e8a00099c6efbecabc3285a8812195`，与独立审查绑定一致。

## 离线与 UI

- `REGRESSION_SUMMARY.json`：**1325 个唯一用例最终通过**。从当前 CI workflow 提取 frozen、Core、quote、F/G、P2、task proof、Runtime、Product HTTP 与本专项检查，再加最终恢复绑定用例；重复用例按 classname/name 去重，不能将重跑累加为新覆盖。
- Core 初次 302 PASS、1 个测试因 `work/` 临时目录被既有安全规则拒绝；`core-system-temp.xml` 在全新系统临时目录复验通过，未修改权限逻辑。
- 35 个唯一既有 Product 用例初次遇 Windows 长路径 artifact 写入错误；`affected-short-path.xml` 只重跑这些用例，35 PASS，未改断言、Gold 或实验结果。
- `final-recovery-suite.xml`：最终 17 PASS，覆盖旧/new capability、actor/note/step/run 改写、精确重试、持续 audit 失败、STALE/RETRACTED 及缓存边界。
- `cache-red.xml`、`recovery-binding-red.xml`、`fresh-capability-red.xml` 均保留真实失败。`recovery-binding-green.xml` 名称不是通过标记：它实际 10 PASS、3 FAIL（2 个既有 3 秒 HTTP timeout；1 个运行中故障注入包装器未转发 details）。`recovery-binding-final.xml` 单独复验 3 PASS；最终冻结版又以 final-recovery-suite 全通过。
- #24 `test_m2_semantic_currentness.py` **7 PASS**、`test_m2_real_failure_regression.py` **18 PASS**；本专项对 `tools/task_relations.py` 零 diff。失效历史 REFUTED/CONFLICT 不成为当前结论。
- `UI_FINAL.json`：3 项语法、5 组 DOM 共 8 项 PASS。
- `PR25_LATEST_COMPATIBILITY.json`：#25 head `aa3473d18b5c4542b665a88df1cfbb7dcea88ed5` 的 11 项补丁/语法/双语 DOM 组合 PASS；仍缺最终组合 Chromium，不可推断可合并。
- CLI help 与 wheel 构建已通过，wheel 含新服务及核验静态资源。此构建验证包资源注册，不作为真实 Provider 或业务批准验证。

测试框架名称中的 LIVE/provider 是既有测试合约，执行使用 Fake/离线传输；没有实际 Provider、Key 或真实企业数据。本地 pyreadline 清理告警、初始失败与日志均保留，不重新计算历史冻结分数。

## Chromium 证据层级

所有新目录在 `output/playwright/task-relation-review/`，旧目录和旧报告不改动。

- `integration-20261025-f2ce/`：集成主线及缓存限制后的完整 PASS，含真实 UI 导入、回执、日期差 4、刷新、Host 重启、撤回、跨空间、版本降级、支持/反证 CONFLICT 和来源移除。201 条 HTTP、10 条核验 POST、80 项 quote 检查全部匹配，0 pageerror、0 external。`default-host/` 验证关闭 flag 仍保留任务答案。
- `integration-20261025-final-binding/`：保留失败；第二次导入前异步 source polling 重渲染表单，浏览器没有发出该次导入 POST，response wait timeout。不将原 UX02 FAILED 标签当作导入失败。
- `integration-20261025-publish/`：保留失败；重新调查的 Runtime 最终返回 PARTIAL、review GET 200，但本地主机负载下超过原 60 秒 locator 等待。不是 proof 失败或通过的替代证据。
- `i-final-1025/`：最终恢复绑定代码冻结后的串行验收 **PASS**。213 条 HTTP、10 条核验 POST、3 个来源版本、80 项引文校验全部匹配；0 pageerror、0 external，7 条预期/已记录的 409 Console 资源错误。新包装器仅延长异步 Runtime 可见性等待、等待上一份来源轮询完成，未改来源、业务断言、HTTP 响应或证明内容。真实结果/重启回读/失效/冲突截图副本见 `browser-evidence/final/`。

原图及完整 HTTP/Console/HAR/Trace 留在各目录。`browser-evidence/` 的三张 PNG 是真实合成来源截图的逐字节复制，可随 Draft 审阅；不是合成图。最终归档清单与提交前保全校验另记在本目录 JSON。

原始冗长 pytest 失败输出与旧验收证据不删、不覆盖，完整保留在本地；Draft 只提交必要摘要、最终安全/冻结回归 XML、聚焦 RED 证据与截图，未上传包含临时 capability 的原始 HTTP/HAR。原报告中的本地证据链接仍依赖对应本地归档，不能把 GitHub 中未附原始日志说成已公开上传。

生产、测试、CI 与新集成 Markdown 的定向 whitespace 检查通过。旧独立报告的 Markdown 双空格换行/EOF 和聚焦 RED XML 中 pytest 原样捕获的空白保留，未为通过 whitespace 检查改写旧文档或证据。

## CI 与发布

最终 Draft PR 的 remote head、base、tree 和 CI 结果由创建后回读记录。等待期间不得将 pending 写成 success。原三个 P2 不在本轮修复范围，详见 INTEGRATION.md。
