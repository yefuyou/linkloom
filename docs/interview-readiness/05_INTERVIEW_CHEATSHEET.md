# LinkLoom 面试速查卡

基线 Recovery `cd668f3`。A=本次离线验证，B=历史报告，C=推断/未来。详细证据：[架构](01_ARCHITECTURE.md) · [Loop](02_AGENT_LOOP_LAB.md) · [Memory/故障/指标](03_EVIDENCE_MEMORY_AND_FAILURES.md)。

## 90秒介绍要点

团队资料混着决定、提案和历史变更 → 来源切片与版本 → 候选经人审核进入Temporal Decision Memory → 当前/as-of查询可0模型 → 复杂关系由有界Agent逐跳Search/Read → 主张Grounding与Reviewer → 展示出处/未知。

真实参与：需求、方向、边界、验收、实验分析与Codex协作；具体实现作者不确定就不归功个人。不要说独立手写整个Runtime。

## 5分钟架构讲解路线

0–1分：Sources→Semantic Ingestion→Review→Materialization→Store。

1–2分：UnifiedAsk分流；正式查询确定性，来源调查Host编译TaskPlan。

2–4分：Engine→Adapter/Coordinator→RetrievalAgent→ModelLoop↔Model→ToolRuntime→Retriever/Read→Observation。Copper Meadow→Falcon Reach→Ada Vale→Owen Brook；本次Fake产品HTTP运行11轮、3Search/7Read。

4–5分：Final→Grounding/Plan检查→Reviewer→UI；伪造下一跳在 `_planned_search_query` / `verified_dependency_entities` 被拒；PASS不是业务审批。

## 8个术语

| 术语 | 一句话 |
|---|---|
| TaskPlan/v2 | 有类型、有依赖、待绑定的任务合同 |
| ModelTurnRequest | 本轮instruction、工具、Observation、受限证据/历史 |
| Tool Call | 模型的调用提议，参数不可信 |
| Observation | Runtime执行后的规范化ToolResult回灌 |
| Verified Read | 本run已观察ID的来源内容/quote哈希复核 |
| Grounding | 引用必须支持具体实体、关系方向、时间 |
| Temporal Memory | 经人审核物化、有有效时间与替代关系的业务事实 |
| Feedback Memory | 人治理的workspace术语映射，不训练模型、不创造业务事实 |

## 4个故障：用根因记忆

1. **ws/root混淆**：Tool History存在，Evidence Context被错过滤；分别校验业务workspace和Vault指纹。
2. **Source Identity缺字段**：模型遗漏document_count；Runtime严格拒绝，修提示合同而非放松权限。
3. **同文件多段丢失**：Retriever单段投影 + Assembler按文件去重；保留有界passages，按Evidence ID去重。
4. **非法alternate拖垮partial**：无计划备用却强行搜索；一次狭义纠错，已确认前缀可由模型提交null主答案，经原验证门接受。

## 5个取舍

确定性查询避免模型调用（本次未单独测量成本对照）；Host有界规划换可审计性；BM25便于离线复现但实体/措辞覆盖有限；逐跳Read增加成本换来源校验；反复全库读取保freshness却拖慢规模化，缓存尚未证明收益。

## 5个必须承认的限制

1. 只支持当前有界关系句式与路径，不支持任意多跳；产品真实Gemini自主规划未验证。
2. FakeModel证明合同，少量真实Gemini成功不能外推生产成功率。
3. 原首次冻结 **12/16** 保持；回归与新集分别报告，不能美化成14/16。
4. Reviewer是代码校验；actor/确认布尔不是企业认证、RBAC或人工批准。
5. 无自动审批/模型自进化；产品无跨进程来源调查恢复入口；500文档重复read仍是瓶颈。

最后闭卷：说清Falcon Reach如何进入轮3、Cedar为何不是当前供应商、ws/root Bug如何定位。**你的口头掌握情况仍未验收。**
