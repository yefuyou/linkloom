# M2 最终主线集成验收 — 2026-10-24

本报告补充原 `DELIVERY.md`、`REVIEW.md`，不重新验收 M2 语义能力。
目标仅为失效历史 REFUTED / CONFLICT 不得污染当前结论，并保留历史审计。

## 基线、Git 与 PR

- 实际 master：`7720390a796662ff42c27b5eef6f8685e5155802`。
- #23 已合并于原基线 `614c4164edd256d2cc63edf1bb9f66befe25cefb`；
  #19 已合并于本次实际 master。
- 沿用实际交付 Worktree `work/m2-semantic-regression`、独立分支
  `codex/m2-semantic-regression` 和既有 Draft PR
  [#24](https://github.com/yefuyou/linkloom/pull/24)，不重复创建 PR。
- 原已推送提交 `013c9743372ba5d4f8c3abd51eb8853015761edb` 完整保留；
  主线同步提交为 `1f7e8fbcfd2ebf3b1811735060bde9e494f44b0f`，无冲突。
  master 是该提交的祖先，未改写专项远端历史，未修改 master 分支。
- 最终 Head、实际 Git Tree、完整 Diff 和 Actions 回读记录更新在本专项
  PR 描述；本文件不预写尚未发生的远端检查结果。

## 集成影响与产品边界

与最新 master 比较，生产代码仍只修改
`src/linkloom/tools/task_relations.py:_relation_status`，9 行新增、4 行删除。
此生产 Diff 与原已验收提交逐字一致，新增测试、支持代码及分发归档也未变。

当前 verdict 仅汇总已有 ACTIVE + VERIFIED + current category 的 P4 读取。
历史行、原 verdict、失效原因及审计字段仍保留在完整读取中。有效负结论和
冲突仍可显示；失效否定不能覆盖当前 UNKNOWN，也不授予 SUPPORTED 或
ESTABLISHED。NONE 与 UNKNOWN 没有新增转换。

#19 的纯解析复用、Context capacity 与本补丁没有生产文件重叠；解析会话不
缓存授权或当前性判断，来源恢复仍核对权限、版本、Workspace 与撤回状态。
共同 workflow 的两组测试入口位于不同位置，均完整保留。#23 的引文修复
保持主线原样。本专项没有新增正则、缓存、状态推导、Prompt、依赖或业务接口。

## 冻结材料与未提交文件

- 原实验 `FINAL_SEAL.json` 的 **942/942** 文件匹配，零差异；封存文件 SHA-256：
  `30d575676154b28f5ffb5e7cd19dbd14ee8d3b4a6659df2700da46c4408b92fd`。
- 分发归档 **84/84** 文件与原封存字节一致，含 **18** 条正式回执；回归核对
  **79/79** 处原生 ID、版本与逐字引文，冻结参考未改。
- 分发 ZIP：201,280 字节，SHA-256
  `f47230c90c077907b091706a6fa88d27f7d528ad479414c0f9e500c3bf1c6766`。
- 本地原始 ZIP SHA-256
  `0354e0229e5c829bb41f3080dfdc3e6cf72c50eeb4b43cb9fd4dcef03afe12b8`；
  原始 SDK 流及 thought signature 继续仅保留在本地。
- 集成前记录的 **23** 份原材料哈希在主线同步后全部一致；未提交的原 XML、
  日志、诊断、原始 ZIP 均保留，不加入提交。新日志与运行材料置于本地
  `work/final-integration-20261024/`，不分发个人路径或不必要实验产物。
- 两份已审阅的 provisional analyst reference/assessment 明确 NOT_GOLD；
  没有引入 held-out/evaluator-only Gold、真实 vault 或凭证。

## 本次必要验证

本地 Windows / Python 3.13.5。Provider 密钥清空，Python 外部 socket 连接
禁止，仅允许本机回环 HTTP；各测试采用既有离线适配。没有真实 Provider 调用。
运行选择和 JUnit/日志保存在上述本地目录，避免覆盖原轮次验收材料。

| 必要集成检查 | 本次结果 | 本地证据 |
| --- | --- | --- |
| M2 专项、18 回执及 79 引文 | **25 passed** | `m2.xml` |
| M3 语义工具、P4 关系与任务信任边界 | **209 passed** | `m3-relations.xml` |
| #19 Context、解析复用、权限与 Workspace/版本边界 | **136 passed** | `pr19-context-permissions.xml` |
| #23 引文、来源身份、Grounding 与可信输出 | **214 passed** | `pr23-source-grounding.xml` |
| 历史冻结账本 blob/工作树/clean-checkout filters | **5 passed** | `frozen-ledger.xml` |
| 原实验封存与分发归档完整性 | **942/942、84/84 匹配** | `integrity-before.json` |
| 原生产补丁/回归/归档与主线同步前一致 | **PASS** | Git blob / Diff 比较 |
| `git diff --check` | **PASS** | 提交前与提交后检查 |

五组本地必要集成回归合计 **589 passed，0 failed，0 errors，0 skipped**。
未出现新的产品失败，也未删改或跳过测试。各组精确选择、退出码及时间保存在
本地 `results.json`；远端自动 CI 使用 workflow 中完整门禁，结果单独回读。

原轮次 949 项、Core、Legacy 与独立审查结果仅按历史结果保留，不冒充本次
运行。原 Core 唯一初始失败源于项目内 basetemp 被既有系统临时目录保护正确
拒绝，原定向复验已通过；本次不修改、删除、跳过或弱化测试以消除此记录。

## 审查与未验收能力

原两次独立审查继续有效。本次仅对新增 #19 集成交互、原补丁和 CI 入口的
保留情况做差分审查，不重复原完整审查。Reviewer
`/root/review_m2_final_integration`（gpt-5.6-luna / max）只读结论：**PASS，
无阻断项**。其核对了原补丁/测试/ZIP 未漂移、纯解析会话退出清理、当前授权
与 registry/version 的持续校验，以及双方 workflow 全部测试路径保留。
Reviewer 未修改文件或重跑测试；此差分结论不冒充远端 CI 或完整语义验收。

已经修复的是**失效历史关系造成的当前状态污染**。通用语义充分性、业务
REVOKED / 恢复、自然且未解决的纯跨会议冲突仍 **N/E**。79 处引文正确不
等于 79 个语义判断正确；错误模型判断仍可能作为不可发布的 MODEL_PROPOSED
分析展示。历史 Flash/Argon 各 8/9 及 M2 H01–H04 结果不重评、不提升。
受控来源撤权/版本/Workspace 回归也不代表企业 ACL、并发或真实客户验收。

本次没有部署、真实 Provider 验收或人工浏览器验收；PR 保持 Draft，最终
远端 CI 通过后仅交付人工审阅，不自动合并。
