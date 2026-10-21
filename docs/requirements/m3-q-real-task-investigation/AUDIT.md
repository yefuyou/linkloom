# M3-Q 初始审计

正常入口：`ui.__main__` -> `ProductApplication` / `ProductInvestigationService` ->
`UnifiedAskService._task_request` -> `prepare_task_investigation` -> F -> G ->
RuntimeEngine / SingleAgentModelLoop -> task tools / SemanticAnalysis -> Grounding / Reviewer ->
`task_result` -> Ask HTTP/UI；完成结果经 `read_saved_task` 重建只读上下文再核验。

P0 缺口（P1）：TaskInvestigationModels、SemanticCallPolicy、Host 与 Unified Ask
均硬性拒绝外部任务阶段；CLI 仅接通通用提取/Temporal。现有 Fake 演示不是普通 Provider 路径。
缺少覆盖全部阶段的单次来源授权、总预算及真实调用回执合同。

F 使用模型提取后进行 M1 精确字段与 non_asserted veto；G 用结构化模型规划后确定性验证
端点、Workspace、范围、依赖和预算。P1 SAME_TASK、P2 direct relation、P3 revision 的可信
证明器及候选发现仍使用有限中文语法。规则未匹配返回 UNKNOWN，不等价于 REFUTED。
M3 另有 LLM SemanticProposer，其引文有效只表示 EVIDENCE_VALID、非语义成立。

P1 发现：Host 只向语义模型发送 ready relation，导致修订候选在身份前提未完成时被遗漏。
修复为完整计划的未确认关系候选；P4 执行就绪合同不变。正反证字段可与 verdict 不一致，
将增加结构校验，拒绝正反证都有却自称 REFUTED/SUPPORTED 的结果。

H01：P3 不识别“替代其原计划，改为”；H02：P2 缺简写变更/具名否定绑定；
H03：P3 漏正证而仅识别反证，错误 REFUTED；H04：P3 修订/纠错/历史绑定失败。
原始 M2 DELIVERY 的冻结执行为 0/4，属有限证明语法缺口。当前没有理由声称 LLM 已修复。
可信关系引擎重写超出本轮；不通过新增关键词修补或将模型提案变成可信证明。

三跳是 G 显式预算，超限报错；发现/语义截断保留缺口或阻断，不能据此证明无关系。
现有 Reviewer/P4 逐前提重验；没有模型批准业务关系的接口。

最小变更集中于 CLI、Host 任务接线/授权/预算 Adapter、SemanticAnalysis 的显式 LIVE 合同、
Unified Ask、UI 能力/预算提示及语义校验；保留原 Runtime/Planner/数据库与冻结输入。

模型/SDK：本机 google-genai 2.22.0，仓库已适配 gemini-3.8-flash；官方模型页确认 function calling。
兼容性须用实际 SDK 构造/本地传输验证，模型名称本身不是调用成功证据。
参考：https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash
SDK timeout 单位毫秒；HttpRetryOptions attempts=1 禁止 SDK 重试：
https://googleapis.github.io/python-genai/genai.html

阶段结论：在用户已授权范围内可继续 P0；真实付费请求仍禁止。
