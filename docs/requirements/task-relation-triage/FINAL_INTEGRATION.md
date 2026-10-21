# 最终集成准备

结论：**READY_FOR_FOLLOWUP_PR**，仅指 #6 合并并复核实际依赖后，可准备任务关系
可视化的后续 PR。当前没有创建 PR，没有合并、修改 #6 或 master。
M2 H01–H04 仍为 **0/4 FAIL**，本功能不构成语义证明问题的解决。

## 1. 精确依赖与增量范围

核验对象 `a9b1fc2d0a64f24c769996a34b3a8e5a4c4d707b` 的唯一父提交是 #6 精确
Head `13adb03c61df8c882a32ce71004b006f7a94fd9b`，不是在当前 master 上重新实现。
本次清理不改变生产代码、测试、复现脚本或 workflow，生产文件字节与 a9 相同。

既有生产增量只有 `ui/task_relation_triage.py`、`ui/task_result.py` 与
`ui/static/product.js`：依赖 #6 的 task_review、完整 ObservationRef、Discovery
候选、语义提案、P4 investigation_steps，以及先于展示执行的来源/收据重验。
没有新 Runtime、关系存储、工具、模型适配器或批准入口。

查询时 #6 仍为 Draft/open，Head 仍为 13adb03；master 已推进到
`33e9ed0e4d2156e202679040568c998c48f46e90`。未声称该净增量可直接应用到当前
master；#6 后续解决冲突或改动 Head 时，必须按实际合入版本重验上述契约。

## 2. ESTABLISHED 的来源与边界

| 输入/情况 | 核对结论 |
| --- | --- |
| 模型报 SUPPORTED / EVIDENCE_VALID，或只有发现候选 | 不足以建立 ESTABLISHED；原有测试保留 POSSIBLE，依赖身份的修订仍阻断。模型的证据校验通过不等于语义证明通过。 |
| 精确 P4 步骤 | 必须存在匹配的关系步骤，所有匹配步骤均 SUPPORTED 且 current_supported=true；返回的失效记录或否定/冲突记录阻断展示升级。 |
| 历史已撤销，且本次包含该记录 | 生命周期重验及失效标记不能放行 ESTABLISHED。旧保存结果的关系变化会整体阻断。 |
| 新调查省略历史 | 不能判断旧证明是否撤销；新线索可能仍为 POSSIBLE。UI 明示该能力限制，不恢复旧关系。 |
| 缺身份前提、缺修订证明、反向端点、失效来源或不支持的范围 | 原步骤保持 BLOCKED/非 SUPPORTED；不能靠 current_supported 单一标记或模型提案放行。 |
| 有效证明已回读，但引文详情因输出预算未返回 | 详情截断与证明缺失不同：既有步骤由完整 P4 读取计算，再做有界输出。保存结果仍须与真实 Tool Ledger 一致并重新执行查询。UI 披露截断；不得把摘要当成一个可独立授权的新证明。 |

完整端点匹配包含 workspace、来源版本、evidence 和 payload；修订不交换方向。
`review_task_relation_result` 先验证本次运行的真实工具收据，并重新读取来源与
生命周期；模型不能构造可信步骤。新投影是内部展示函数，未来不得作为接收
调用方自报步骤/状态的公开接口，也不能让其状态成为业务授权凭据。

这里的 ESTABLISHED 仅表示**既有 P4 有限来源证明当前通过**，不是通用语义正确、
人工批准或当前生效计划。原证明器的 M2 泛化缺口仍然存在。

## 3. 撤权与 Workspace 隔离

本轮 36 项现有聚焦回归：36 passed，0 failed/error/skipped。覆盖新关系视图、
#6 产品回读、精确 P4 步骤、执行失败及权限终止；没有增加刷数量的测试。

额外真实 HTTP 检查扫描完整序列化响应：跨 Workspace 热读/冷读均返回
WORKSPACE_MISMATCH（409）；来源 Evidence Catalog 撤权后热读/冷读均 BLOCKED。
四种失败响应均没有旧任务原文；撤权结果没有 evidence、trace 或 task_review。
Source/Workspace 校验先于新投影，失败对象不携带旧 preparation 引文。

## 4. ZIP 清理与可复现验收

ZIP 为 1,123,120 bytes，包含 186 项、7,881,392 bytes 未压缩的一次性材料，
主要是重复 HTTP 响应与执行记录。只有文档引用，没有运行、测试或 CI 读取依赖。
本次从待集成树移除 ZIP 与其 939 行 MANIFEST；原 a9 提交仍保留完整历史副本和
指纹，未重写历史，也不声称旧 Git 对象体积被回收。本地原文件另行保留。

保留复现脚本、9 项 HTTP 回归、workflow、摘要与原始 M2 只读诊断。公共 P4
输入来自既有 `tests/fixtures/task_revisions/inputs/dev.json`，不是 Gold。
从 a9 干净导出、排除 ZIP/清单后，七组真实 HTTP/SQLite 演示及五份实际响应
驱动的前端 DOM 检查全部通过，侧库字节均未改变，真实 Provider 调用为零。

```bash
python scripts/demonstrate_task_relation_triage.py --output work/new-triage-demo
node tests/ui/unified_ask_dom_test.cjs work/new-triage-demo/possible/result.json work/new-triage-demo/p4/result.json work/new-triage-demo/conflict/result.json work/new-triage-demo/retracted/result.json work/new-triage-demo/possible/revoked-hot.json
python -m pytest -q tests/integration/test_task_relation_triage.py tests/integration/test_m3p_product_review.py tests/unit/test_task_investigation_p4_steps.py tests/integration/test_m3p_execution_failures.py
```

脚本要求新输出目录，拒绝覆盖旧记录。现有 CI 作业已配置为留存生成结果；
本轮未触发远端 CI，无需将新生成的原始日志重新提交 Git。
本轮[检查收据](evidence/followup-readiness.json)记录生产指纹、状态与隔离检查。
原 a9 的独立生产审查仍适用于相同代码；本轮追加独立复核因 Agent 额度不可用
未完成，不声称获得新的独立 PASS。浏览器视觉验收没有在本轮补做。

## 5. #6 合并后的最小接入步骤（本轮未执行）

1. 确认 #6 实际 merged，记录合入 SHA 及最终 Head，检查上述来源/收据重验和
   task_review 契约仍在。不能只凭 PR 标题或原先的 Head 判断依赖已满足。
2. 从合入后的远端 master 新建工作树和 follow-up 分支；导出本实验分支相对
   13adb03 的**净差异**，先检查可应用性。净差异不包含 ZIP/MANIFEST，也不应
   重带 #6 的 143 个文件或其原始基线历史。兼容不了时停在具体冲突处。
3. 只应用该净增量，重跑上述聚焦回归、离线演示/DOM 和受影响 workflow 门禁。
   对最终实际组合版本补做浏览器操作，不把本轮旧基线结果冒充组合验收。
4. 核对 diff 只有关系展示、接线、复现与必要文档，再创建后续 Draft PR。
   保留 M2 FAIL 和历史读取限制；不自动 Ready、合并或改 Provider 默认授权。

## 6. M2 根因与下一阶段切口（独立于本可视化增量）

| 原失败 | 根因 | 下阶段具体切口 |
| --- | --- | --- |
| H01 | 身份已确认，替代/修订表述未识别；缺年份正确保留，但修订未建立 | 把新旧计划、原始修订声明、引用目标和日期角色组成来源绑定的候选证据包；缺年不得补年。 |
| H02 | 简写变更桥、任务特定的否定句无法可靠绑定；候选也有漏召回 | 分离变更实体发现与方向/对象绑定，保留否定和无法唯一绑定的状态；不能因供应商名相近确认影响。 |
| H03 | 正证漏识别，只识别反证，因此把冲突误作否定 | 确保同一记录对的支持与反对声明都进入可审查的证据集合；冲突不按日期、模型置信度或单侧匹配消解。 |
| H04 | 初始修订未确认，后续纠错声明绑定也不足，故无法验收历史 +2 全序列 | 先打通原计划→修订声明→纠错/撤回的精确来源链，再独立检查版本变化和生命周期；不得改写旧记录补造历史成功。 |

原保留候选召回为 6/8，但固定端点判定仍只有 5/8；仅补召回不能解决全部失败。
SAME_TASK 原保留身份原子为 5/5；主要缺口是修订/影响的语义与来源绑定，而非
全面身份失效。原结果不是一次真实模型评测，不能归因于真实模型能力不足。

建议下一阶段先做**来源完整、方向明确、正反证并列的修订候选证据包**，复用
现有来源/候选/验证链。模型输出仍只作候选；可信晋升需要另行明确可审查的
验证或真实认证审核合同，不能直接放宽 P4、Grounding 或 Reviewer。
四个已曝光案例只作回归，同时设计不同措辞、否定/引用范围、反方向、日期角色、
来源版本变化和无证据样本；新的泛化结论必须来自另行封存的新数据及明确授权。
禁止只为 H01–H04 追加正则、重标 Gold、重算 Seal 或覆盖历史成绩。本轮没有
实现此阶段，也没有调用真实模型或启动新评测。
