# M2 最小语义防回归交付

基线：最新拉取的 master `614c4164edd256d2cc63edf1bb9f66befe25cefb`。
独立分支：`codex/m2-semantic-regression`。原实现轮次只做本地修改；后续用户已
授权本专项 commit、普通 push 和面向 master 的独立 Draft PR，不授权合并。

## 已证实的缺口与最小修复

M2/P4 已正确把来源失效、证据权限撤销或关系撤回后的记录降为历史记录。
但 M3 `tools/task_relations.py:_relation_status` 汇总 `include_history=True`
的读取时，仍把历史 assessment 的 REFUTED/CONFLICT 用作当前步骤状态。
历史 REFUTED 还可以覆盖当前 UNKNOWN。这是工具投影缺口；默认不返回历史
记录的路径不等于发生了相同问题，也未发现它能直接写入业务状态。

修复仅改这一处汇总，使用现有 `ACTIVE`、`VERIFIED`、当前 category 过滤
参与结论的记录。失效历史行、原 verdict、阻断原因和审计内容继续保留。
没有新增语义规则、关系正则、Prompt、依赖、持久化或业务状态接口。

修复前本地 `red-currentness-02.xml` 有 **7 个断言失败**；不是收集或环境错误。
覆盖 REFUTED/CONFLICT × 撤回/版本变化/权限撤销，以及旧否定覆盖当前未知。
修复后对应新旧聚焦检查 **17 passed**，见 `green-currentness.xml`。

## 真实失败 → 产品路径

| 记录 | 已有保护 / 实际边界 | 离线证据 |
| --- | --- | --- |
| R07 Argon：身份/负责人/范围未证实，却给 NONE | `task_relation_semantic_models` 没有 NONE；`_decode` 不接受研究 JSON。没有 NONE→UNKNOWN/REFUTED 自动转换。P4 `assess_same_task` 在原文缺身份桥梁时仍 UNKNOWN | 原始失败保留；明确构造“用真实引文声称 SAME_TASK/REFUTED”的测试变体，可通过逐字引文校验，仍 MODEL_PROPOSED、不可发布、不能作为 P4 assessment 保存 |
| R02 Flash：撤销误分类为 REVISES_PLAN，requires_review=false | `validate_proposals` 已有反证极性检查、同任务前提；`SemanticAnalysis` 固定语义未验收/不可发布；P4 重放身份和修订证明 | 原反证保留时，肯定修订变体被极性检查拒绝。再构造省略反证、假定同任务的对抗变体，仍不能产生可信修订或日期差 |
| R01/R03/R05：负责人、完成/展示程度过推断 | `build_task_observation` 对无负责人角色证据的 C 清为 UNKNOWN；任务观察固定 SUPPORTING_ONLY/UNCONFIRMED；原型完成不是语义 API 的业务状态字段 | 检查孤立 you 片段、无可信说话人/事件时间、拒绝塞入业务 COMPLETED 字段。M3-S 字段绑定的语义解释仍未验收 |
| R04：设计范围/供电方案修订 | M3 有向查询不能倒置；P4 要独立同任务证明；摄取/会议顺序不能补成生效时间 | 真实文本的英文桥梁不满足有限 P4 证明时保持 UNKNOWN；没有伪造“P4 成功识别”。反向提案被请求绑定拒绝；无截止日期或生效时间补写 |
| R05：设计前置于评估 | 研究 AFFECTS 与生产 task AFFECTED_BY ChangeRef 不同 | 保留原回执；不把前置条件改造成已发生延期、延期天数或传递影响 |
| R06：后续会议内矛盾，随即被纠正 | 实验 CONFLICT 主类型与生产 typed predicate + CONFLICT verdict 不同；模型冲突提案无业务恢复权限 | 检查两锚点确属同一会议；对抗 typed 变体仍不可发布。未计作纯跨会议未解决冲突成功 |
| R08/R09：拒绝访问 / 当前版本缺失 | `context_errors`、当前 registry、source snapshot、冷读 review 已有降级机制 | 保留两组真实 UNKNOWN；使用 R07 原始引文构造权限/版本/观察撤回变体，在现有 M3 验证与冷读中失效；P4 历史负结论投影缺口由上述补丁修复 |

这组实验的 Flash/Argon **各符合冻结参考 8/9** 保持为历史诊断；冻结参考仍是
分析者参考，非人工 Gold。本轮没有新模型评分，也没有模型总体优劣结论。

## 原始材料与测试层次

本地原始 `tests/fixtures/m2_real_failures.zip` 保留 18 条正式成功请求、原始
SDK 流、响应、提案、输入和冻结参考的字节，共 681 个原始文件 / 818,139 字节
归档。原实现轮次测试重建原响应并核对 **79/79** 个原生 ID / 版本 / 逐字引文。
原实验全部 **942 个封存文件** 再次核对无变化，见 `original-integrity.json`。
没有复制或改写真实 Provider 账本、历史 Gold、其他 PR。

归档里 `evaluator/` 下的两份文件是本轮已经查看的
`PROVISIONAL_ANALYST_REFERENCE_NOT_GOLD` / `PROVISIONAL_ANALYST_ASSESSMENT_NOT_GOLD`，
不是 held-out 或 evaluator-only Gold；没有引入其他评测答案。
**79/79 只表示逐字引文、原生 ID 和版本匹配，不表示 79 个语义判断正确。**

本轮提交使用独立的 `tests/fixtures/m2_real_failure_receipts.zip`（201,280 字节）：
保留其中 84 个必要文件的原始封存字节与哈希，包含 18 条 wire 请求、原始
assembled 响应、提案和回执、9 组输入及上述冻结参考。原始 SDK 流中有 18 个
非空 thought signature，属于 Provider 续传元数据，因此整个 SDK 流层只在
本地保留，不进入提交。原归档及原实验完全不变，测试答案和冻结参考也未
改写；分发回归改为核对封存 assembled 响应与提案的一致性及原有 79 处引文。
原始流重建是先前本地结果，本轮 CI 不声称包含该运输层检查。

原研究响应不能直接作为 M3 的 typed Tool receipt。本轮保留这一接口差异；
`tests/m2_real_failure_support.py` 的本地 Markdown 信封与 typed 对抗变体明确
标成测试适配。它们验证生产信任边界，**不称为原生 Provider Runtime 回放**。
引文存在的证明、语义充分性、当前有效性和业务权威分别报告。

两份新增 pytest 文件已接入现有 `.github/workflows/offline-pr.yml` 的离线
Task investigation 检查。原有关系、生命周期、Runtime、Reviewer 和 DOM
检查用于保护既有行为；本轮没有付费调用或真实 vault 操作。

## 剩余风险 / N/E

- 引文正确仍不证明语义正确。错误 SUPPORTED/REFUTED 可作为未确认模型分析
  展示；当前机制阻止其自动成为可信关系，但不解决模型理解错误。
- 生产没有研究枚举的 REVOKED。`RETRACTED` 是关系证明记录撤回，不是业务
  任务取消；完整业务取消、纠正后的恢复与历史状态机验收仍 N/E。
- 自然、未解决的纯跨会议冲突；负责人/日期/位置修订；实际延期与传递影响
  仍 N/E。受控权限/版本测试不等于企业 ACL、并发竞态或真实客户验收。
- 不提高 M2 历史 H01–H04 成绩；本轮不进行真实模型或浏览器人工验收。

## 原实现轮次的本地验证与独立审查

| 本地检查 | 结果 / 证据 |
| --- | --- |
| Task investigation（现有 CI 选择集，含新增回归） | **949 passed**；`task-investigation.xml` |
| Core（现有 CI 选择集） | **302 passed**；初次另有 1 个临时目录设置导致的失败，见下文；2 deselected |
| Core 唯一失败的定向复验 | **1 passed**；`core-system-temp.xml` |
| Legacy Historical | **4 passed**, 22 deselected；`legacy-temporal.xml` |
| Frozen ledger | **5 passed**；`frozen-ledger.xml` |
| 独立审查者运行新增/聚焦回归 | **25 passed**；`REVIEW.md` 记录实际命令与终端结果 |
| DOM / JavaScript 语法 / CLI 和 UI 启动入口 | **PASS** |
| 编译 / 最终 diff 检查 | **PASS** |

Core 的唯一失败是 `test_validate_target_root_accepts_synthetic`：整组使用的
项目内 basetemp 不属于 `tempfile.gettempdir()`，现有保护正确拒绝该目标。
只把这一项放回全新系统临时目录复验即通过，没有修改保护或测试来过关。
原始 `core.xml` / `.log` 的失败记录保留在本地；未重跑已通过的 302 项。
本地 `verification.json` 保留精确命令；入库的 `final-verification.json` 保留
结果与环境原因。表中的 XML、原始日志、诊断和本机命令记录不入库，避免
携带主机名、个人临时目录和不必要的运行产物；这些文件名是本地证据定位。

独立审查 **PASS for Draft PR review**，无 P0/P1/P2 发现。
**本轮最小防回归边界已满足，具备独立 Draft PR 条件**：基于最新 master，
单一已复现产品缺口、最小生产补丁、离线回归、封存材料和独立审查均齐备。
这是审阅准备结论，不是 M2/M3 模型能力或生产发布验收。

## 本轮提交与远端交付边界

提交范围为一处已验收的关系状态汇总补丁、两份必要离线回归及其支持代码、
201,280 字节的必要回执分发归档、CI 测试入口和本专项文档/可移植摘要。
生产权限、Grounding、Reviewer、Prompt 和历史评测账本均不变。

提交前回读 master 仍为上述基线。PR #19
（`codex/m3-context-minimal-integration`，核对 Head
`f06f3fd96470cceb577acac8469583d624580a16`）的生产文件与本专项不重叠；
共同的 `.github/workflows/offline-pr.yml` 在不同位置添加测试，本专项不依赖
其解析复用或 context capacity 改动，也没有修改该 PR。

本轮分发兼容修正后，新增回归与既有 P4 步骤合同共 **35 passed**（离线，
无真实 Provider），见 `submission-verification.json`。分发归档的每个文件均
与原封存文件逐字节一致；没有 thought signature、密钥或个人路径。

远端 Head/Base、实际 Diff、合并状态和 Actions 验收以本专项 Draft PR 的
描述及检查记录为准；本节不预写尚未发生的 CI 结果。用户保留人工审阅和
合并决定，本轮不合并 master。

实际交付 Worktree 为 `work/m2-semantic-regression`。应用托管检出曾挂接停滞，
未使用的检出和本轮临时诊断材料保留，没有执行删除清理。

验证环境为本地 Windows / Python 3.13.5。HTTP 测试使用本机回环服务；沙箱
曾以 WinError 10013 拒绝回环连接，修正权限后执行离线门禁，子进程 API
密钥为空。初始运行脚本/沙箱诊断保留在 `work/`，不算作产品门禁结果。
DOM 和 JavaScript 语法检查通过；CLI/UI `--help` 启动入口通过。原实现轮次
没有运行云端 CI；本轮在 Draft PR 创建后验收自动触发的相关 Actions。
没有真实 Provider 验收、部署或新的人工浏览器验收。
