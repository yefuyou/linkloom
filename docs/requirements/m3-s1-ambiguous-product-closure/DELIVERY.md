# M3-S1：PASS，原歧义 Product 闭环完成

本任务的代码与原歧义 Product 验收通过；最终业务结果为 **PARTIAL / UNKNOWN**，
表示负责人、截止日仍未定案。没有把未知当作执行失败，也没有用新的明确场景替代。
M3-S 历史 PARTIAL 报告、Gold、M2 0/4、P4 和 CI 门禁保持不变。

## 根因与修复

原失败来自 G 的输入构造，不是 F 错把候选人或讨论日期认定为业务事实。
原请求哈希 `f5f46a687bd0ffa13a7b3f2c79957b3e5a008b2b058df84006052ac7405e685b`
已从保存的真实请求、完整旧计划和 Host UNKNOWN 回执精确离线重建。

| 同一原始失败场景 | 原实现 | 最终投影 |
| --- | ---: | ---: |
| 初次规划预留（原始版本） | 14,879 bytes | — |
| 重规划请求含1024 framing预留 | 31,756 bytes | 21,897 bytes |
| previous_plan | 15,394 bytes | 5,095 bytes |
| 完整来源表 | 2,202 bytes | 2,202 bytes |
| 端点数 | 2 | 2 |

实际检查同时约束 `max_input_tokens=24576` 和更小的 `max_input_bytes=24000`。
旧请求超限7,756字节，在第二次 Provider 调用前就被阻断；不是模型调用后的 Token 超支。
旧计划7个步骤反复包含8份端点和10份来源绑定，而实际各只有2个唯一对象。
本例旧回执数组为空，主因是步骤内重复引用；后续重规划还可能重复累计回执。

修改仅涉及三个生产文件：

- `semantic_ingestion/task_relation_question_planner.py`：沿用 `SemanticContext.model_input()` 的可信端点/来源表；旧步骤按 handle 引用，显式保存原 `source_binding_handles`，累计回执只在外层保留一份。投影前确认旧端点、完整来源都仍在当前授权上下文；Host完整计划、hash、依赖和来源引用不改、不截断，不做LLM摘要或额外模型调用。
- `ui/task_investigation.py`：保持最多一次 replan；bounded discover 没有新信息就记录 UNKNOWN，重规划时从 schema 和 decode 允许集合同时移除 discover。模型不能制造发现完成或反复搜索。
- `semantic_ingestion/task_relation_question_models.py`：与现有 P4读取条件一致，UNKNOWN/CONFLICT/REFUTED 不满足任何步骤的积极前提；relation仍必须SUPPORTED。独立合法读取可继续，依赖缺失信息的步骤保持阻断。

首次真实复验进一步暴露保留步骤 ID 冲突：模型把已保留的 discover `step_5` 重用于 clarify。
Host正确拒绝，没有覆盖旧历史。现将保留ID显式列入输入、schema字段说明和prompt，继续保留硬去重。
没有放宽验证、提高Token/byte上限、新造Planner/Runtime或新增中文业务语义正则；F未修改。
诊断见 [payload-diagnosis.json](evidence/payload-diagnosis.json)。

## 离线轨迹

使用原 M3-S F 观察和初始 G 输出重放，先复现 `INPUT_BUDGET_EXCEEDED`，再验证投影后
在原预算内成功。来源、Workspace、版本/hash、完整端点和完整Host计划一致；被删来源、
端点或换Workspace都在模型调用前拒绝。UNKNOWN discover不能解锁dependent inspect；
independent inspect仍可执行。重复discover会被schema限制和Host decode拒绝，不进入循环。

正常Product HTTP分别覆盖F返回两条观察、仅返回一条观察；两者都完成Runtime/Reviewer，
保留owner/deadline未知、来源证据及缺口，冷读不调用模型。这些是离线合同，不代替真实验收。
最终受影响Task/Frozen回归 **625 passed**；专项99、Context安全55及前置123均通过，批次重叠不相加。
JS语法和Ask DOM通过；CI配置未修改，远端CI未运行。XML见 evidence/。
曾出现的Windows子进程GBK/UTF-8解码失败通过设置运行环境 `PYTHONIOENCODING=utf-8` 解决，没有改历史断言。

## 真实原歧义 Product

原来源SHA：`aaa627070219888e9214db52cfbba1b3ddbac73d9a22f4556b1268c588f79113`。
问题保持：“页面巡检现在由谁执行，交付日期定了吗？两次会议的安排有什么变化？”
两个会议片段、固定预期和原始记录均未修改。[LIVE_CASE](LIVE_CASE.json) 与修复复验前固定的
[LIVE_RECHECK](LIVE_RECHECK.json) 可核对同一来源、问题及预期。

| 实际尝试 | 请求/预留 | 结果 |
| --- | --- | --- |
| 首次，F1/G2 | 3 / $0.75 | F正确UNKNOWN；真实重规划21,194字节，成功发出并返回；因保留ID冲突而OUTPUT_INVALID，Product BLOCKED。原产物保留。 |
| 修复后，F1/G1/Runtime4/Semantic1 | 7 / $1.75 | run `ui-872e36156520`，Product PARTIAL、error=null，Reviewer接受。 |

正常Workspace创建、Sources导入、Ask选择Tasks、单次授权后才发送内容；未授权请求0次。
实际Runtime工具为 `inspect_task_context`、`query_task_relations`、`analyze_task_semantics`。
四个负责人/截止日inspect步骤完成，两条页面巡检观察的owner、deadline_text均null，日期角色UNKNOWN。
会议日期和两个候选交付日期都没有变成已确认截止日，也没有把报进度者当执行人。

SemanticAnalysis提出SAME_TASK=UNKNOWN、REVISES_PLAN=REFUTED，两者引用EVIDENCE_VALID，
仍为未发布模型解释。Reviewer `evidence_sufficient` 只证明实际来源/执行/回执充分，
包含 `SEMANTIC_NOT_EVALUATED`，没有P4提升或可信日期差值。UI展示UNKNOWN、精确原文、
关系缺口及clarify问题。记录版本/offset/hash与字段来源均能回读。

最终真实G直接选择inspect+relation+clarify，没有再次请求discover。这满足有界规划目标；
不能声称修复后的真实复验又命中了discover→UNKNOWN→replan。该分支有原失败精确重放、
首次真实重规划成功发送/非法输出拒绝、最终专项回归覆盖，保留这个验证边界。

## 浏览器与冷读安全

真实Chromium `155.0.8059.85`，页面错误=[]。字段证据可展开、原始来源正文精确打开、
刷新后重新核验成功；新增Provider请求0。新进程未配置Provider仍可读取已接受的原任务结果。

对自己的公开合成数据执行撤权、版本更新和切换Workspace验收：撤权和更新后的热/冷读均
BLOCKED，旧evidence/trace/task_review不再返回；跨Workspace返回409。Provider journal前后
SHA一致，没有额外请求。来源更新创建了自己的新版本，所以本地该旧结果现在按合同失效。
Sources仅本地保存，通用入库Provider未配置；来源截图中的unavailable不是Tasks真实调用失败。

证据：[字段截图](evidence/cold-fields.png)、[原文截图](evidence/source-open.png)、
[完整Product/安全收据](evidence/product-and-safety.json)。完整本地产物保留于
`work/m3s1-live-20261022/` 和 `work/m3s1-live-recheck-20261022/`，未提交数据库或密钥。

## Provider 与预算

模型 `gemini-3.8-flash`、SDK `google-genai 2.22.0`、low，串行、SDK attempts=1、单次60秒，
请求前持久预留。新任务明确授权一次新的验收，没有自动重试 M3-S 的未知请求；旧UNCERTAIN预留未释放。

- 本轮10次：F2/G3/Runtime4/Semantic1；10 SUCCEEDED，0 Provider FAILED、0超时、0 UNCERTAIN。
- 有1次模型计划格式被Host拒绝，不能把10次API返回成功说成两次Product尝试均通过。
- 输入71,909、候选输出5,076、SDK total78,273 tokens；total包含额外计数，不能直接等同前两项相加。
- 本轮预留$2.50，低于固定$3费用上限；首次自定请求上限10，修复后复验前固定为12，实际仍仅10次。
- 此前69请求/$19.43保守预留，项目累计79请求/$21.93，$50预留余额$28.07。核对了既有4份全局journal，未发现其他新增M3预留。
- 预留不是已扣费；实际Google账单、促销赠金扣减和计费项目无法独立核验，保持未知，不描述成$0。

原始预算、授权、usage与统计见 [live-summary.json](evidence/live-summary.json)；文件hash见
[manifest.json](evidence/manifest.json)。新请求、默认配置、评测均未使用Gemini2.5。

## 工程状态与剩余边界

独立只读Reviewer结论PASS、P1/P2=0，见 [REVIEW.md](REVIEW.md)。分支
`codex/m3s-ambiguous-product-closure`，基线bde9d9e2；最终SHA与远端回读见本轮交付消息。
master、PR#6、M3-R、M3-S与CI分支未改，不创建PR；PR#6保持Draft。

仍需正式资料才能确认负责人/截止日；本轮正确答案就是未知。更广泛真实重规划的保留ID协议
稳定性尚需未来新案例验证；当前仅承诺本任务原歧义闭环和已覆盖合同，不宣称通用Agent成功率。
