# LinkLoom M2 集成与最终保留验收

日期：2026-10-18。**M2 整体验收 FAIL，H01–H04 为 0/4；B/C 集成成功。**
本轮已完成集成、冻结执行和失败交付，没有启动 M3 Agent、真实 Provider 或
大规模评测。M2 语义未达到原 SPEC §6.5 的 Recall、修订、冲突与生命周期
完整序列门槛，不能据此宣称可以进入生产 M3 业务验收。

## 集成与实现冻结

仓库 `yefuyou/linkloom`；从 P4 基线
`eee882f4c676e900ee8895ac5b09f5fb2b3e6e36` 新建独立 Worktree，独立分支为
`codex/linkloom-m2-integration-acceptance`，目标远端为
`codex/linkloom-m2-progress`。A/B/C 和主工作区均未修改或共用。

共同祖先为 `af001a4d968676c30979254ac70dfb750e8f9ad6`。B 指定提交
`98e6fde17d34886e1e35bb8eae77e1b0ef6a9422` 仅新增 19 个专属文件，C 指定
提交 `526d59ef0f216940b2677402bb4ba5ff6d81cd32` 仅新增 22 个专属文件，
路径不冲突。分别 cherry-pick 为 `58db89a` / `d75ca74`；没有拿 B/C 的旧树
覆盖 P4。既有 P1/P3/P4、M1、Decision Memory 以及原 Dev/Gold 未修改。

唯一生产修复：`task_work_proofs._target()` 对引用片段调用 `strip()`。
这是 C02 尾空格接口兼容，未增加语义规则。首次 RED 为 1 failed / 1 passed，
实际两个方向均 UNKNOWN；修复后真实证明方向 SUPPORTED，反方向 UNKNOWN。
C10 召回限制保持原样。

冻结实现提交：`312d717893b3c5d4d759701b4fc3364650401c22`；首次执行提交：
`06a44af7171f3173a746e569ac0e98152366a0ec`。
[FREEZE.json](FREEZE.json) 固定实现、输入、规则、来源和评分口径，保护 233 个
文件。规则为 same-task/direct-relations/plan-revision/candidate-discovery 的
既有 v1，直接关系另记录上述空白兼容补丁，P4 schema 为 task-relation-store/v1。

| 冻结材料 | LF 归一化 SHA256 |
| --- | --- |
| 独立保留输入 | `ea00234535537ace891638610cb20d1c9a0e5b5331381b6f2f19ba386138dc1c` |
| evaluator-only Gold | `fb1b41d40d56a5fa6953786910f54c4504615e4c2eff3a19a29d224e1040a667` |
| M1 与来源快照 | `d78ead569fe4e48c747230154dc3e9f306342fea11f6996e21fcba454fda4db5` |

输入和 Gold 分目录，预测进程禁止打开 Gold/expected 路径。Unicode offsets 和
source content hashes 由 M1 实际构造；H03 保留 A2/B2 全部续行，H04 各阶段只向
判定器提供当期来源与完整当前观察。来源版本、取证引用不从旧版本偷换到新版本。
案例设计和 Gold 曾公开，故这是**作者可见保留验收，非严格外部盲测**。

## H01–H04 首次逐项结果

| 案例 | 实际输出及未通过原因 |
| --- | --- |
| H01 | SAME_TASK SUPPORTED；REVISES_PLAN UNKNOWN，comparison BLOCKED/null，原因 NO_REVISION_EVIDENCE。没有识别“替代其原计划，改为…”的修订，应为 SUPPORTED + 缺年 NOT_COMPARABLE。owner/deadline 仍 null、MONTH_DAY 正确，没有补年。 |
| H02 | 三题 AFFECTED_BY 均 UNKNOWN / NO_UNIQUE_DIRECT_CHANGE_BRIDGE；应为 SUPPORTED、REFUTED、UNKNOWN。简写供应商更换及具名任务否定句超出现有因果/变更语法。第三题缺口说明正确。SOURCE_CLAIM_ONLY、AUTHORITY_NOT_ESTABLISHED、正式决策 0 均正确。 |
| H03 | SAME_TASK SUPPORTED；修订 REFUTED / EXPLICIT_NO_BUSINESS_REVISION，comparison NOT_APPLICABLE/null；应保留正反证并为 CONFLICT / BLOCKED。现规则识别了反证，却漏掉“本条是执行计划，替代A.md的14日计划”的正证。没有按较晚时间选计划，但冲突状态仍错误。 |
| H04 s0 | SAME_TASK SUPPORTED；修订 UNKNOWN / NO_REVISION_EVIDENCE，BLOCKED/null；未确认原文 20→22 的修订，未得到 +2。 |
| H04 s1 | 旧 S/R 过期且不发布，新 S SUPPORTED、新 R UNKNOWN/BLOCKED；应为新 R REFUTED/NOT_APPLICABLE。纠错声明仍未被正确绑定。 |
| H04 s2 | 旧 R 为 RETRACTED，依赖比较 BLOCKED/null、不复活；新 R 仍 UNKNOWN。由于 s0 没有 +2，不能声称完成“历史 +2 保留”的整个序列。 |

全部原始预测、比较、证明原文、依赖和查询读回在
[首次预测](evidence/holdout/first/)；完整逐原子评分和失败阶段在
[首次原始 metrics](evidence/holdout/first-metrics.json)。四个预测 SHA256：

| 预测 | SHA256 |
| --- | --- |
| H01 | `cde64b10055a8544c66cc862c5f6b511fb835ed4b84446f3090ee2fae93c0556` |
| H02 | `3692f1fcfb6edba1d5e6bacdf5e165e8c41b4b20740f6184a2e813c83573732a` |
| H03 | `9601f5c7a43f04ea0e4a83c49fac4660cff3eafc1f4c324528793c59360a7fc8` |
| H04 | `81da3ecb9781d55e962ace1b6b44dfaf351f87227511e599050e6af9ae5579d6` |

## 语义指标

以下按原子端点、谓词、作用域和 snapshot 计分；H04 s1/s2 的新判断分别计分。
旧 S/R 的不可发布状态也进入安全检查。0 分母为 N/A。

| 谓词 | 原 D01–D08 Dev P / R | 保留 P / R |
| --- | --- | --- |
| SAME_TASK | 4/4；4/4 | 5/5；5/5 |
| RELATED_TO | 1/1；1/1 | N/A；N/A |
| AFFECTED_BY | 1/1；1/1 | N/A（0/0）；0/1 |
| REVISES_PLAN | 2/2；2/2 | N/A（0/0）；0/2 |
| IN_PROJECT | 2/2；2/2 | N/A；N/A |
| PROJECT_ALIAS | 1/1；1/1 | N/A；N/A |

保留 micro Precision 5/5，Recall **5/8 = 62.5%**，低于 90% 门槛；没有用
全弃答规避 SAME_TASK 正例，但修订和影响无正例确认。Dev 原子去重评分见
[Dev metrics](evidence/dev/metrics.json)，补充 V 案例与原 D 案例分开记录。

- False Merge：Dev 0/4，保留 **0/4** 个唯一发布观察对；hard false merge=0、
  unsupported merge=0。SAME_TASK 的 5 个 snapshot 正例对应 4 个唯一观察对。
- Revision Accuracy：Dev **5/5**，保留 **0/5**，原先声明的十题合计 **5/10**。
  Gold 修订 SUPPORTED 子集保留为 **0/2**。
- COMPUTABLE 日期/精度/边界/有符号差值：Dev **2/2**（+4、0），保留 **0/1**
  （H04 +2 未确认）。没有把 null 当 0，没有发布虚假差值。
- 保留 UNKNOWN 正确 **1/1**，CONFLICT **0/1**，REFUTED **0/3**。
  UNKNOWN 无据否定=0；H03 把冲突误作否定另如实记录，不能算安全状态正确。
- 无据因果确认、正式审核越权、过期/撤销发布均为 **0**。
- H04 过期阻断、撤销、依赖阻断及幂等成立；初始修订确认和历史 +2 序列失败。
  已有 P4 Dev 的 53/53 原子检查仍通过，不能替代此处 H04 的语义失败。

真实候选发现另计：B 的 12 个 Dev 查询、9 个相关目标，Recall@1/3/5 均为
**8/9**，不是关系判定 Recall。H 保留按独立 public/M1 端点和正例谓词提示
计候选 Recall **6/8**（S=5/5、R=1/2、I=0/1），固定候选判定仍只有 **5/8**。
这些都是离线有限语法结果，没有生产 Agent 或 Provider 成绩。

## 组合链路的实际调用

[C02 结构化演示](evidence/demo/C02.json)：M1 验证观察 → discover → UNCONFIRMED
候选 → 使用前重新核验 → RELATED_TO 两个实际证明方向 → 一个 SUPPORTED、
另一个 UNKNOWN；独立 SAME_TASK 为 UNKNOWN。只把已验证关系写入显式临时
SQLite，关闭 writer 后 P4 只读重开查询，current_supported=true、confirmed=1。
PRAGMA query_only=1，查询前后数据库字节不变。

同一实际关系撤销后 confirmed=0；另一个独立侧库中来源变化后旧候选重新核验
失败、旧关系为过期、不再发布。没有拿“候选存在”当关系支持，也不要求所有
候选持久化。[C10](evidence/demo/C10.json) 仍零候选，固定端点为 UNKNOWN；
外围“本任务”的回指未进入任务摘录，不能被解释为无关系。

[D04](evidence/demo/D04.json) 保留 P1 独立身份、P3 明确修订与 +4 计算；
[原五行 D07](evidence/demo/D07-original.json) 仍 UNKNOWN/BLOCKED/null，
不补项目或制造延期。[预算输出](evidence/demo/budget.json) 明确暴露截断，
所有演示仅说明给定快照内有证据的结果，不声称找全项目关系。
D08 项目别名、派生查询和撤销证据仍由 [P4 Dev 53/53](evidence/dev/p4-metrics.json)
及已有 P4 交付中的实际结构化结果支持。

## C 评测设施与验证

`evals/m3_task_impact` 的 16 个独立输入/Gold 样本静态核验通过：12 Dev、
4 保留，来源与项目隔离；M3 保留仅静态审阅，没有执行或评分。
[无 Adapter 运行](evidence/m3/unavailable-run.json) 为 12 NOT_RUN；
[评分](evidence/m3/unavailable-score.json) 为 12 NOT_EVALUATED、metrics=null。
固定 D01 的 [Scripted 结果](evidence/m3/scripted-dev-score.json) 只进入
SCRIPTED_WIRING_ONLY，另外 11 题仍未评测，不作为真实 Agent 业务成绩。

工程验证为 102 项候选/P2/M3 聚焦测试 + 269 项受影响 M1/P1–P4/来源/正式审核/
Decision Memory 回归 + 7 项验收工具测试 + 3 项补核工具测试，**381 个不同测试**。
命令和环境故障归因见 [verification.json](evidence/verification.json)。Windows
pyreadline 析构警告出现在通过后，最终测试进程 exit=0。未宣称 CI、部署或
真实 Provider 验证。pytest 数量不代表本轮 M2 语义验收通过。

## 独立审查与仍有边界

完整时序见 [POST_RUN_REVIEW.md](POST_RUN_REVIEW.md)：早期 PRE-RUN PASS
在首跑启动后被撤回；缺口为阻断理由和 ChangeRef 精确来源核验。冻结材料与
首跑均保留，随后只对同一预测补核，未修改规则、Gold、原 scorer 或首次输出。
[最终补核](evidence/holdout/post-exposure-audit-reviewed.json) 验证冻结文件和预测
hash 不变，ChangeRef 3/3 正确，业务状态/理由仍 FAIL。不能称为完全无缺口的
首次验收流程，更不能称外部盲测。

B/C 无未解决的集成阻塞；M2 仍有 H01/H02/H03/H04 的有限语法和冲突处理缺口，
C10 候选漏召回也保留。原 M2 scorer 的工程失败分母路径尚不适合未来通用验收；
本次四题无运行器工程失败，分母未缩水。未来修复须标为**已曝光回归**，新泛化
成绩需要新独立封存数据。没有为了开发集分数重写语义规则。

最终独立终审 APPROVE 此 FAIL 进度基线的提交与条件式安全快进推送；不批准
M2 语义通过或进入生产 M3。Git 路径与原提交限定核验见
[integration-git-check.json](evidence/integration-git-check.json)。

## 复现与 M3 最小接线建议

在新检出环境使用当前 checkout 的源码，输出选新的目录/label，不覆盖旧证据：

```powershell
$env:PYTHONPATH = "$PWD/src;$PWD"
python scripts/run_relation_checks.py --stage p4 replay-integration tests/integration/test_m2_candidate_contract.py tests/integration/test_m2_final_harness.py tests/unit/test_m2_postrun_audit.py
python scripts/demonstrate_m2_integration.py --output work/replay-composition
python scripts/run_m2_final.py predict --output work/replay-exposed-holdout
python scripts/score_m2_final.py --predictions work/replay-exposed-holdout --output work/replay-exposed-metrics.json
python scripts/audit_m2_final.py --predictions work/replay-exposed-holdout --metrics work/replay-exposed-metrics.json --output work/replay-exposed-audit.json
python -m evals.m3_task_impact run --mode UNAVAILABLE --output work/replay-m3-not-run.json
python -m evals.m3_task_impact score --predictions work/replay-m3-not-run.json --output work/replay-m3-not-evaluated.json
```

已有冻结文件不重新生成。上述 replay 是曝光后的复现，不是新的首次保留验收。
评分命令的 exit=0 只表示成功生成评分文件；验收结论必须读取 metrics 中的
overall_m2_acceptance，本次为 FAIL。不能用进程退出码宣称业务验收通过。
临时侧库和原始日志留在本集成 Worktree 的 work/，没有删除 A/B/C 或用户材料。
桌面工具最初创建的另一个未使用 managed Worktree 后来才完成，也保持原样；
实际集成始终只在项目 work/ 下的新独立 Worktree 进行。

下一轮 Adapter 只需实现现有 `adapter(request, tools)` / `analyze_task_impact`
边界：授权 Workspace 和有界当前来源 → M1 → 候选重验 → 完整竞争观察下的
P1/P2/P3 → P4 只读查询 → typed tasks/relations/revisions/拒答/证据与执行回执。
RELATED_TO 检查两个证明方向；SOURCE_CLAIM 不冒充正式审核；遗漏/截断输出
partial/unknown。先接离线 Dev，并将本次失败作为曝光回归处理；生产 Provider
和大规模评测等待下一轮明确授权。
