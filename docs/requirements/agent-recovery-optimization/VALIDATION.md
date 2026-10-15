# 验证分层与已知限制

基线 `49736a7098b23c8b9f021f98f1fe6d3e7c70e755`，新独立工作树；原主工作树有大量无关未提交改动，本轮未修改。origin 为 `https://github.com/yefuyou/linkloom.git`；开始时远端基线 SHA 一致、新分支不存在。没有新依赖。

## 离线测试

- 初始关键基线 45 passed（来源 identity、证据上下文、Unified Ask/Investigation、RecoveryPolicy）。初次命令误用了不存在的测试文件名，0 tests；纠正后的结果另存，不覆盖错误记录。
- P0 红测试复现一次非法 alternate 造成部分事实丢失；随后恢复、身份拒绝、连续错误停止、伪造部分事实拒绝通过。预算测试起初配置低于计划最低回合被入口拒绝，改为合法但不足以完成的 5 回合后，发现 UI 缺失 budget 分类，已修复。不是放宽成功断言。
- `regression-corrected.xml`：133 passed，覆盖原有来源/身份、两三跳、冲突、Unified Ask HTTP、Feedback Memory、Provider permission/unknown、检索工具运行时、新恢复与实验限制。初始命令一个 Feedback 测试文件名不存在，0 tests；保留记录。
- `p1-closure.xml`：17 passed（ContextAssembler + 多段 evidence）。原相同 source_ref 去重使 Canary 首段介绍阻挡后续关系；改按 evidence ID 去重，原总数/Token/Workspace/Memory 来源去重继续有效。
- `final-contracts.xml`：47 passed；独立审查者另执行 49 项 focused closure，通过且无阻断发现。
- 最后增加评测报告/代码快照禁止覆盖保护。初次补丁落在错误的 loader 位置，4 failed/12 passed；已移至执行入口，`evidence-freeze-corrected.xml` 为 16 passed。该修正不改变生产推理或已完成的原始成绩。
- `live-canary-fix.xml`：17 passed（恢复、旧/新入口和预算约束）；`preflight-closure.xml`：16 passed（budget UI 分类/原投影与新实验限制）。这些是不同 focused 证据，不能相加冒充独立成功率。
- `node tests/ui/unified_ask_dom_test.cjs`：通过。确定性竞态覆盖连续提问、日期/问题/决策变更、Workspace 切换、延迟旧 poll、Feedback mutation、权限重置，以及未核验草稿隐藏。新增 unknown 文案测试初次 mock 没有外层 result envelope 导致 transport-shape 错误，修正 fixture 后通过。

## 数据与评分

历史首次 **12/16** 原样保留。旧 A/B citation 误读不存在 verified 字段，不重新打分冒充首次。

本轮 public/Gold 分开冻结：12 acceptance + 2 early dev + 2 指定 canary。作者参与制定 Gold，非严格外部盲测。新 observer 按实际 Product `evidence[].ref` 和完成态 `read_verified_note` 做关联，并独立检查 quote/content hash、claim support、计划依赖；名称 A/B 不赋予豁免。Gold 仅执行后 scorer 读取。

新 12 首次 raw 已保留，分数 **11/12**，完整 9、部分 3。唯一失败是冲突案：产品保留可信上游事实并标注 conflicting_evidence，两个争议来源均在引用；冻结 Gold 期待 refusal+空 facts，实际 partial+上游 fact，故分类/精确 facts 不匹配。首分不修改。先前 Canary 首次 1/2，P1 修复后已暴露回归 2/2；二者分别报告。

首次代码快照、P1 后快照独立保存；之后 mandatory identity prompt 和 UI 标题的收尾改动将另存 final manifest，不覆盖历史 freeze。12 输入/Gold 与旧历史集始终不改。

先前快照列表未包含 ContextAssembler/UI 静态文件，这一观测范围限制保留说明。完整生产变更文件的交付哈希另存 `code_freeze_manifest_delivery.json`；旧 raw 所引用快照不修改。未来执行创建新快照，已有同名文件将拒绝覆盖。

## 浏览器证据

实际浏览器连接本机合成 `UnifiedInvestigationFixture`，FakeModel/FakeTransport，不调用 Provider。已观察完整答案“陈宁”、缺失领导时保留 Riley Fox、冲突时保留 Tess Avery 并展示 Devon Hart/Casey North 双方来源、切换 Other 后旧 run 不覆盖新页面、以及未知状态无草稿 Final。未知主标题原先泛称中断，现明确为“本次调查执行结果不确定，没有可核验的最终结论”，已再次浏览器确认。

截图留 ignored `work/agent-recovery/browser/`。浏览器观测不是 CI，也不替代确定性竞态测试。未进行真实 Vault、公开部署或产品发布。

浏览器追加验证：通过 Sources 的 Save new source version 将合成“林舟 → 陈宁”改为“林舟 → 叶青”，重新 Ask 显示叶青、Reviewer 核验及新 artifact；Runtime Failure 页面没有成功答案；进行中修改精确日期立即清除旧结果。另在合成 Atlas 创建、批准并撤销 supplypartner 映射，进行中撤销后旧结果不再出现；连续提交第二个问题时保留第二个 unsupported 结果。DOM 测试覆盖延迟旧 poll 等确定性时序，未声称每一种网络时序都已人工复现。

真实批次最终 63 请求后累计两次严格 TLS 未知而停止，包含 61 Usage 与 2 保守预留；完整统计见 LIVE_METRICS.json。性能最后发现 inclusive retrieval/read_notes 重叠，修正 observer 并追加 9 次规模实测，历史 raw 不改；没有生产缓存或因果提速声明。

## 真实实验预检与资料

模型仍为 `gemini-3.8-flash`、SDK `google-genai==2.22.0`。在 2026-10-15 重新核对官方 [模型能力](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)、[Function Calling](https://ai.google.dev/gemini-api/docs/function-calling)、[临时价格说明](https://ai.google.dev/gemini-api/docs/latest-model)。输入 $0.75/M、含 thinking 输出 $3.75/M；使用估算，不是账单。

SDK MockTransport 预检使用原 Provider serializer/parser、签名与模型工具结果；FakeModel 的启动期限及无工具 Final 假设经过修正，不算真实网络结果。实际 Canary 另计，首批失败完整保留。真实请求尺寸从旧实验 64 KiB 调整为 96 KiB；仍保守按最多 131072 输入 tokens 加 8192 输出/thinking tokens 预留 $0.129024，字节限额检查发生在 SDK 发送前。未增加生产 max_steps。
