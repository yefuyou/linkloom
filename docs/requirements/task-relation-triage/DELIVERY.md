# Task Identity / Relation 最小产品改进

2026-10-21。独立实验基线：PR #6 精确 Head
`13adb03c61df8c882a32ce71004b006f7a94fd9b`。
分支：`codex/task-relation-evidence-triage`。**离线、只读、有限范围通过；
M2 H01–H04 仍为 0/4 FAIL。** 不宣称语义泛化或真实模型验收通过。

## 原始失败的定位

原始 M2 材料来自 `dd76a25bd9e70544c8e0534538b694015928fea1` 的
`docs/requirements/m2-integration-acceptance/`，仅通过 Git 对象只读提取。
[原始预测摘要与字节指纹](evidence/original-m2-diagnosis.json) 可核对出处。

| 检查问题 | 原始证据与判断 |
| --- | --- |
| 候选发现是否漏掉？ | 确有次要缺口：原保留候选 Recall 6/8；但给定固定端点后关系仍只有 5/8。补候选不能单独解释全部失败。C10 的外围回指丢失也仍保留。 |
| 同一任务身份能否确认？ | H01、H03、H04 的 SAME_TASK 均为 SUPPORTED；原保留身份原子为 5/5，不是这些修订失败的主因。 |
| 有方向的修订证据是否可靠提取？ | 这是主因。H01 的“替代其原计划，改为…”、H03 的“本条是执行计划，替代A.md的14日计划”、H04 的修订与纠错绑定超出有限规则。H03 漏掉正证，只识别反证，误作 REFUTED 而非冲突。 |
| 模型能力不足？ | 原 M2 是离线有限语法结果，不能据此评价模型。本轮也未使用真实 Provider。 |
| 查询/工具是否用错已有证据？ | 原修订证明器未把已有原文识别成所需证明；另发现可独立修复的产品缺口：`inspect_task_context.discovery.candidates` 已有未确认记录对，#6 页面却不展示，用户只能从分散提案/步骤自行拼接。 |

选择修复最后一项的**产品呈现和导航缺口**：复用已返回线索，让用户对照两条
来源、看懂关系分级和阻断原因。没有为了曝光样本修改证明正则、Gold 或评分。

## 实际功能与集成差异

正常 Ask HTTP 的 `task_review.relation_triage` 和现有页面新增逐记录对检查：

| 状态 | 依据 |
| --- | --- |
| 已证实关联 / ESTABLISHED | 完整不可变端点匹配原有当前 P4 步骤，且步骤确实 SUPPORTED；仍仅为有界来源关系，不是业务批准、当前负责人或生效计划认定。 |
| 可能相关 / POSSIBLE | 现有未确认 Discovery 线索，或证据校验通过的正向模型提案；两者均不能建立可信关系。 |
| 无法建立关联 / NOT_ESTABLISHED | 无正向线索、返回的否定/冲突或失效记录、范围不支持、前置条件未满足等；不是“已证明无关”。 |

每项显示两条原文、来源名称/版本/行号、来源入口、支持/反对/候选证据及其不同
来源。修订保留 A→B 方向，候选顺序不会描述成已执行检查。完整 Workspace、
来源版本、evidence 和 payload 参与匹配，不按名字绑定。负责人、日期和日期
角色分别保留，不从“可能同任务”继承字段。没有人工确认按钮或新授权接口。

生产改动仅为新增 UI 投影模块、现有 `task_result.py` 接线和 `product.js`
展示；没有新表、数据库路径入口、Runtime、Planner、工具或模型调用。
原始候选排序/有限证明器也不修改。新增脚本与回归加入现有离线 CI 作业。
这是叠加于 #6 的小补丁，不重复移植 #6 的 143 个文件。

## 真实产品演示与失败保留

相同公开合成 fixture 分别运行精确父提交和本分支的真实 localhost HTTP。
父提交实际返回 1 个 RELATED_TO 候选（D04 为 3 个候选），页面投影没有
`relation_triage`；本分支无需增加 F/G/语义调用即可显示这些记录对。

| 场景 | 实际结果 |
| --- | --- |
| 原先隐藏的候选 | RELATED_TO 显示 POSSIBLE；G 没有为它创建步骤，亦无模型提案。两个原文和线索可查看。 |
| 脚本模型报 SUPPORTED | SAME_TASK 仍为 POSSIBLE；身份未确证时修订保持 NOT_ESTABLISHED，日期不继承。 |
| 模型 UNKNOWN / 冲突 | SAME_TASK 不建立关联；冲突例保留正反两组引文，不按新日期取胜。REFUTED 另有真实 HTTP 回归。 |
| 原 P4 正例 | 仅 fixture 准备阶段调用原证明器写入合成侧库；正常产品读取使精确身份与有向修订 ESTABLISHED，原日期差为 4 天，plan_currentness 仍 NOT_ESTABLISHED。 |
| 反向修订 | 正向候选可见，反向计划步骤 BLOCKED；没有任何反向 ESTABLISHED 或日期计算放行。 |
| 无候选 | 产品 UNKNOWN，检查列表为空；不编造记录对或断言任务不存在。 |
| 来源证据撤权 | 已有结果热读、冷读均 BLOCKED，清空证据/轨迹/任务视图；跨 Workspace 返回 409 或冷恢复 404，无原文返回。 |
| 关系撤销后的新调查 | 不返回历史的既有查询可能显示新的 POSSIBLE 线索；明确披露未检查历史，可信步骤全部 BLOCKED，不恢复旧证明。 |

七组演示均实际调用现有 Product HTTP → F/G → Runtime/ToolRuntime/Ledger →
Grounding/Reviewer → UI 投影，并通过热读与新服务冷读。脚本适配器只供给测试
提案，不能设定通过验证后的业务结果。侧库产品读取前后字节一致；没有额外
Provider 请求。基线/候选完整响应、输入与执行证据见
[证据压缩包](evidence/offline-product-evidence.zip)；每项字节指纹在
[MANIFEST](evidence/MANIFEST.json)，可直接浏览[演示摘要](evidence/summary.json)。

## 验证与限制

本地执行现有 workflow 的对应命令：Core **270 passed / 2 deselected**；
最终任务批次 **540 passed**（含新增 9 个真实 HTTP 测试）；原 Legacy Gate
**4 passed / 20 deselected**；Frozen Ledger **5 passed**。不是全仓测试结论。
真实 HTTP 响应驱动的前端 DOM 检查（可能/P4/冲突/撤销/撤权）、Node 语法、
`git diff --check` 及原 `--offline-task-demo` HTTP/冷读入口通过。
相关 JUnit/输出保留在压缩包。没有远端 CI 运行或部署结论。

[独立 Reviewer](REVIEW.md)：有范围的 PASS，无未解决 P1/P2。
审查发现最初对撤销诊断的表述过宽，已收窄并在 UI/SPEC 留下明确说明；
这不是历史关系诊断修复。既有权限终止与撤权阻断规则未放宽。

仍然未知/未完成：

* M2 的语义证明缺口、H03 的原规则误判、C10 漏召回均未修复；没有新泛化成绩。
* 只呈现已返回记录对；Discovery/P4 截断会提示，遗漏不能证明无关系。
* 默认 P4 读取不含历史，新调查不能完整诊断旧证明的撤销原因；新线索不会使
  历史证明复活。返回的失效/撤销记录会阻止 ESTABLISHED。
* 真实模型语义能力、具备认证权限审计的人工关系确认均未接通。
* 本地浏览器插件不可用，云浏览器无法连接执行环境 localhost；实际响应已
  渲染进前端 DOM 验证，但**未完成真实浏览器视觉验收**。

## 复现

安装项目 `.[dev]` 后，在新目录运行（脚本拒绝覆盖旧输出）：

```bash
python scripts/demonstrate_task_relation_triage.py --output work/new-triage-demo
node tests/ui/unified_ask_dom_test.cjs work/new-triage-demo/possible/result.json work/new-triage-demo/p4/result.json work/new-triage-demo/conflict/result.json work/new-triage-demo/retracted/result.json work/new-triage-demo/possible/revoked-hot.json
```

完整新 HTTP 套件：`python -m pytest -q tests/integration/test_task_relation_triage.py`。
交互入口仍是 `python -m linkloom.ui --offline-task-demo --database work/new-demo.sqlite`。
全部使用公开合成来源，无真实 API Key，无真实用户笔记内容。

## Git 与集成风险

与基线对比，Runtime、Agents、Semantic Ingestion/P1–P4、Tools、Decision
Memory、evals、旧 tests/eval 及 `.gitattributes` 字节无变化。旧 Gold/Seal/
历史成绩不修改；#6 原分支和 master 不修改、不合并，本分支不创建 PR。

集成需要 #6 的现有 `task_review` 与来源重验调用链；后续改造该调用链时，应
保留“先校验、后投影”的顺序。新增字段为只读展示信息，不能作为授权、工具
前置条件或持久关系写入依据。最主要的剩余风险是用户把 POSSIBLE 误读成
可信关系，或误以为新调查查完了历史；页面已针对两者给出明确说明。
