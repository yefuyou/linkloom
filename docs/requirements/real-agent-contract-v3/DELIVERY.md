# Real-Agent Reliability V3 交付

**唯一状态：EXPERIMENTAL_VALIDATED。暂不建议集成。**

本分支从指定 Adaptive commit `37e45b169f19c4edd29db59f637e690caab31ad6` 独立开发。V2只读参考；未整批移植框架，未创建PR、改PR #3、合并master或启用默认Adaptive Planning。

## 实际修复

- 严格 Provider Outcome Unknown 不再因异常的字符串 code 或嵌套 Timeout 被误分类为普通失败。保持终止、不自动重试。三条历史 Adaptive 失败具有该根因；新真实样本已正确返回 `PROVIDER_UNCERTAIN`，但未重放历史任务来宣称全部解决。
- 非法 TeamDecision Final 可在原预算内，通过既有 ModelLoop/RecoveryPolicy 获得最多一次新模型回合。持久化 `final_rejected`，修正阶段不开放工具；再次非法、无预算、伪造事实仍拒绝。独立审查发现的冷恢复剩余额度缺陷已修复，两个断点在仅剩一个请求时通过。
- 工具Schema声明Host已绑定的实际来源身份；没有注入下一跳事实，也未放松Grounding、Reviewer、Workspace、Memory或权限规则。

## 归因与尚存问题

逐项审计18条Adaptive失败及11次V2真实执行，记录首个可观察失败层、请求/模型状态、SHA与UNKNOWN。两分支并非同一种故障。四条Adaptive ToolInvalid来自Harness对已带时间戳来源再次包装，导致关系无法解析；不修改生产语法迎合它。六条V2 Contract失败首先是非法JSON，部分截断或反思文本；Grounding未执行时保持UNKNOWN。

别名缺跳、实体范围不一致、冲突引用不完整继续拒绝。typed claim-proposal暂未增加格式恢复，零工具场景模型仍可能返回非法工具调用。本轮未开展Query-only Stage B。

## 真实模型前后

Gemini 3.8 Flash 实际版本兼容性核验通过；新批次串行、SDK自动重试0。Canary完成模型规划和两跳检索，现有Grounding、Reviewer及保存引用审计均通过。

同冻结来源、Workspace、计划及参数的对照：正常两跳旧/新均成功（5→5请求），合法Partial均正确不足（4→4请求）。可比较结果2/2→2/2，没有可靠性提升证据；费用约$0.064964→$0.071224，增加约9.6%，不能推断稳定成本变化。冲突旧侧HTTP503、新侧严格不确定，不能作成功率因果比较；均未重试。

共35次请求，已知Usage费用估算$0.22179075，未知Usage预留$0.159744，总占用$0.38153475/$4；pending=0，严格不确定1次。已结束付费实验，不为获得好结果继续请求。

## 验证与限制

新增离线格式恢复、伪造事实、剩余额度和冷恢复检查通过；Provider/identity独立9项通过；Final修正HTTP和非法工具HTTP各1项通过。真实浏览器使用离线模型展示了中文答案、关系链、Reviewer和来源，未冒充真实Gemini演示。八类产品场景分别记录模型类型和证据。

同一249节点：原基线209通过/40失败，候选首轮208通过/41失败；唯一新增错误码回归修复后针对性8项通过，未伪称完整重跑。40个原失败含38个历史输入缺失和2个原断言失败。全仓collect另有4个历史缺失脚本错误，不宣称核心全绿。

新20项冻结集资格失败：Dev为2通过/5失败/3 N/A；Final仅执行一次，为0通过/8失败/2 N/A。包括计划与query不一致、不可观察guard等fixture/runner缺口，不能归成生产回归，也不能作为泛化成绩。保留Gold和原始失败，没有修改后重跑冲分。runner自测5通过/1失败同样保留。独立审查解除生产冷恢复与配对输入一致性阻断，整体验收仍不完整。

## 复现与后续

离线修复测试：`python -m pytest tests/integration/test_final_correction_v3.py tests/unit/test_real_agent_contract_v3.py tests/unit/test_contract_v3_provider.py`，设置`PYTHONPATH=src;.;tests/integration`。

Dev诊断：`python scripts/contract_v3_cases.py --partition dev --output unique`。Final有一次性claim，请勿重跑。真实入口 `scripts/contract_v3_live.py` 显式要求Provider许可，并绑定配对输入；本批次已结束。

机器记录为RESULTS.json、AUTOPSY.json、LIVE_EVIDENCE.json、DATA_EVIDENCE.json及REGRESSION.json。后续需要人工批准新的合格评测集与真实预算，再决定PR/集成或默认行为变更。本轮是执行契约可靠性修复与受控验证，没有权重学习，也没有自动接纳生产策略。
