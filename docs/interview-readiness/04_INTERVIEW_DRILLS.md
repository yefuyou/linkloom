# 25道递进练习：先闭卷讲，再看要点

难度：1=基础，2=跨模块，3=工程追问。每题先用60–90秒作答；“要点”是核对项，不是背诵稿。统一验收按00的0/1/2分；目前所有个人回答**未验收**。源码引用固定在Recovery基线，实验见 [02](02_AGENT_LOOP_LAB.md)，指标与失败口径见 [03](03_EVIDENCE_MEMORY_AND_FAILURES.md)。

## 基础理解

### 1. “这个项目解决了什么真实问题？”〔1〕

要点：团队资料里决定/提案/历史变更混杂；恢复当前与as-of事实；显示出处与不确定性；Obsidian是来源基础。源码：[product.py:997 · ProductApplication.recover_decision](../../src/linkloom/ui/product.py#L997)。追问：如果只做聊天框加向量库，哪一个用户问题仍答不好？

### 2. “当前供应商为什么可以不调用LLM？”〔1〕

要点：先定位已审slot/日期；确定性Store查询与freshness；来源抽取曾用模型，不代表每次读取用模型。源码：[unified_ask.py:149 · UnifiedAskService.ask](../../src/linkloom/ui/unified_ask.py#L149)。追问：查“为什么换供应商”为什么不能直接复用同一结论？

### 3. “Model、Runtime、Tool、Retriever、Reviewer各是谁？”〔1〕

要点：提议、执行与持久状态、受限函数入口、资料排序、确定性输出核验；Reviewer不是人工审批。源码：[runtime.py:423 · ToolRuntime.execute](../../src/linkloom/tools/runtime.py#L423)；[reviewer_agent.py:15 · ReviewerAgent.execute](../../src/linkloom/agents/reviewer_agent.py#L15)。追问：工具handler拿到任意路径就能读吗？

### 4. “哪些代码是你写的？”〔1〕

要点：按真实经历讲需求、方向、边界、验收、实验与AI协作；不推断具体函数作者；能解释不等于独立手写。源码练习：任选 [runtime_adapter.py:751 · RuntimeAgentAdapter.run](../../src/linkloom/agents/runtime_adapter.py#L751)，解释自己的理解与可核验证据。追问：不用Codex时，你能定位第3轮失败在哪一层吗？

## Agent Loop

### 5. “画一次真实三跳，不要只画抽象RAG。”〔2〕

要点：Copper Meadow→Falcon Reach→Ada Vale→Owen Brook；11轮、3Search/7Read是本次Fake产品轨迹；包含重复/无关Read；Final只引三项。源码：[unified_investigation_fixture.py:48 · UnifiedInvestigationFixture.configure_agent](../../tests/integration/unified_investigation_fixture.py#L48)。追问：为什么三跳不是三轮？

### 6. “第一次结果里的Falcon Reach，如何进入下一次请求？”〔2〕

要点：第2轮Read→ToolResult→持久Observation→轮3ModelTurnRequest；evidence_context另保留；模型提出alias依赖；Host重新查Ledger验证再绑定查询。源码：[model_loop.py:834 · SingleAgentModelLoop._run_durable](../../src/linkloom/runtime/model_loop.py#L834)。追问：`observation_ref`和`evidence_context`为什么同时需要？

### 7. “模型发出function call以后，谁执行Python？”〔1〕

要点：Provider Adapter解析neutral proposal；Loop绑定调用身份；ToolRuntime做schema/policy/source检查并调handler；模型只输出数据。本实验直接Fake action，没经过Gemini解析。源码：[gemini_api.py:1551 · GeminiProviderAdapter.complete](../../src/linkloom/agents/providers/gemini_api.py#L1551)；[runtime.py:423 · ToolRuntime.execute](../../src/linkloom/tools/runtime.py#L423)。追问：为什么call_id不能直接相信Provider？

### 8. “模型把下一跳改成Invented Meridian会怎样？”〔3〕

要点：已实际负例；parent Read必须先完成；object匹配失败，`$.source_context.dependency_entities`→TOOL_INVALID_ARGUMENTS；本控制器停止后FAILED/无结论。源码：[runtime_adapter.py:220 · RuntimeAgentAdapter._planned_search_query](../../src/linkloom/agents/runtime_adapter.py#L220)；[task_plan.py:676 · verified_dependency_entities](../../src/linkloom/runtime/task_plan.py#L676)。追问：另一个出现在原文中的名字就一定合法吗？

### 9. “你实现的是自主规划，还是规则Workflow？”〔2〕

要点：UnifiedAsk走host正则编译；TaskPlan/v2是带relation_path依赖合同；模型选择Search/Read；另有model planning/一次repair窄路径，未由当前产品真实Gemini样本证明。源码：[team_decision_planner.py:187 · plan_relation_investigation](../../src/linkloom/runtime/team_decision_planner.py#L187)；[graph.py:444 · RuntimeEngine.start_multi_agent](../../src/linkloom/runtime/graph.py#L444)。追问：遇长中文别名问题为何宁可unsupported？

## Retrieval / Evidence

### 10. “为什么Search Hit不能直接当Final证据？”〔2〕

要点：Hit排序与出处形状不等于已执行Read；本次Search甚至标status=verified，仍不能跳Read；需已观察ID、新鲜内容/quote hash、具体主张匹配。源码：[runtime_adapter.py:521 · RuntimeAgentAdapter._read_verified_note_for_tool](../../src/linkloom/agents/runtime_adapter.py#L521)；[team_decision.py:656 · TeamDecisionResult.from_grounded_final](../../src/linkloom/agents/team_decision.py#L656)。追问：正确ID能证明错误汇报方向吗？

### 11. “BM25、Dense、RRF分别做什么？Demo实际用了哪个？”〔2〕

要点：词法、向量相似度、按排名倒数融合；Hybrid每通道top20、k=60；本次BM25，不把可选模式当当前运行。源码：[retrievers.py:75 · BM25Retriever](../../src/linkloom/retrieval_v2/retrievers.py#L75)、[retrievers.py:93 · DenseRetriever](../../src/linkloom/retrieval_v2/retrievers.py#L93)、[retrievers.py:122 · HybridRetriever.retrieve](../../src/linkloom/retrieval_v2/retrievers.py#L122)。追问：向量检索能直接保证关系方向和有效时间吗？

### 12. “Evidence ID、Quote Hash、Source Version是什么关系？”〔2〕

要点：ID定位片段身份；hash校验精确片段/内容；Product source_version锁定来源版本；Workspace限定域，root fingerprint另指Vault；不能互代。源码：[retrieval.py:35 · retrieve_evidence](../../src/linkloom/retrieval.py#L35)；[investigation.py:306 · ProductInvestigationService._source_relations](../../src/linkloom/ui/investigation.py#L306)。追问：整文只改了无关一行，旧ID还能直接复用吗？

### 13. “Grounding和Reviewer是不是重复？”〔2〕

要点：Grounding检验原子主张及引用Read，Plan检验依赖目标；Reviewer用工具检验引用和subject合同；两者均不能代替业务人工审批。源码：[claim_grounding.py:715 · assess_claim_text](../../src/linkloom/agents/claim_grounding.py#L715)；[reviewer_agent.py:15 · ReviewerAgent.execute](../../src/linkloom/agents/reviewer_agent.py#L15)。追问：Reviewer接受partial，为什么仍显示不足？

## Memory / Temporal

### 14. “会议一句话怎样变成正式业务事实？”〔2〕

要点：RawArtifact→span→模型候选→验证→Human Review→Materialization receipt→Temporal record；Extraction confidence不能代替审核。源码：[product.py:1248 · ProductApplication._ingest](../../src/linkloom/ui/product.py#L1248)；[product.py:1467 · ProductApplication._approve_candidate](../../src/linkloom/ui/product.py#L1467)。追问：原文没有明确生效时间怎么办？

### 15. “Wrenwell可信，Cedar为什么不能成为当前供应商？”〔1〕

要点：Birchline与Wrenwell有已审决定及有效时间/替代；Cedar只是未批准proposal；current/as-of不同；可信更换事实不能推出原因。源码：[product.py:997 · ProductApplication.recover_decision](../../src/linkloom/ui/product.py#L997)；[test_product_application_flow.py:444 · test_recovery_keeps_later_unapproved_proposal_out_of_current_answer](../../tests/integration/test_product_application_flow.py#L444)。追问：10月6日查到谁？你查的是事件时间还是审核时间？

### 16. “Feedback Memory学习了什么？”〔2〕

要点：pending到approve/active再revoke的受审术语映射；只影响workspace中已有reviewed relation的scope discovery；无模型权重更新、无新业务决定。源码：[feedback_memory.py:74 · FeedbackMemory.action](../../src/linkloom/ui/feedback_memory.py#L74)；[feedback_memory.py:118 · FeedbackMemory.snapshot](../../src/linkloom/ui/feedback_memory.py#L118)。追问：为什么不能让模型自行写一段反馈指令？

### 17. “AgentMemoryCandidate可以自动变成Decision吗？”〔3〕

要点：候选不是权威；需审核/物化路径；不能借运行证据或Feedback绕过正式审批；来源关系调查关闭Decision Memory工具与capture。源码：[runtime_adapter.py:656 · RuntimeAgentAdapter._capture_agent_memory_candidate](../../src/linkloom/agents/runtime_adapter.py#L656)；[investigation.py:75 · ProductInvestigationService.start](../../src/linkloom/ui/investigation.py#L75)。追问：现有正式决策和来源调查处于同一workspace时，怎么证明不被修改？

## Runtime / Security

### 18. “Agent为什么会停？请分业务与执行两类。”〔2〕

要点：业务不足/冲突可在预算内提交有据null Final；执行失败、预算耗尽、Provider未知不同；max_steps/provider/tool/deadline各有边界。源码：[progress.py:42 · assess_progress](../../src/linkloom/runtime/progress.py#L42)；[recovery_policy.py:30 · decide_recovery](../../src/linkloom/runtime/recovery_policy.py#L30)。追问：为何预算耗尽不能事后改称成功partial？

### 19. “网络超时为什么不直接重试？”〔3〕

要点：区分known failure和unknown_provider_outcome；后者可能已执行/计费，保持UNKNOWN并需核验；durable身份与请求预算，不能重试到绿。源码：[recovery.py:115 · decide_model_resume](../../src/linkloom/runtime/recovery.py#L115)；[model_adapter.py:441 · ModelProviderError](../../src/linkloom/agents/model_adapter.py#L441)。追问：Provider返回retryable是否就意味着产品会无界自动重发？

### 20. “上下文不够时，你牺牲什么？”〔2〕

要点：旧tool turns/history先裁；最新Observation和已选Evidence优先；Evidence自身8结果/24KiB，请求96KiB；不足时拒绝而非伪完整。源码：[model_loop.py:60 · _bounded_model_request](../../src/linkloom/runtime/model_loop.py#L60)；[model_loop.py:275 · SingleAgentModelLoop._verified_evidence_context](../../src/linkloom/runtime/model_loop.py#L275)。追问：工具历史保存完整，是否证明模型仍看到完整证据？

### 21. “你们具备企业多租户安全吗？”〔3〕

要点：当前是本机服务、workspace/source权限边界；外部Provider需显式允许；来源注入不能扩大工具权限；local actor/human_confirmed不是身份认证或RBAC。源码：[unified_ask.py:220 · UnifiedAskService._source_request](../../src/linkloom/ui/unified_ask.py#L220)；[feedback_memory.py:74 · FeedbackMemory.action](../../src/linkloom/ui/feedback_memory.py#L74)。追问：未来多用户部署还缺哪一层？把它标为未来设计，不说已实现。

## 真实工程故障与取舍

### 22. “讲一个Trace里工具成功，模型上下文却丢证据的Bug。”〔3〕

要点：Product ws_*与Vault root_*混淆；过滤层错；保留两身份分别校验，不能删scope检查；Fake控制器可依赖自身缓存/Observation掩盖。源码：[model_loop.py:275 · SingleAgentModelLoop._verified_evidence_context](../../src/linkloom/runtime/model_loop.py#L275)；[test_product_evidence_context_scope.py:323 · test_context_rejects_foreign_workspace_and_wrong_vault_fingerprint](../../tests/unit/test_product_evidence_context_scope.py#L323)。追问：怎样证明修复没引入跨workspace污染？

### 23. “同文件第二段为什么检索不到？修一层够吗？”〔3〕

要点：Retriever单文件仅投影一个passage；Assembler再按source_ref去重；现按Evidence ID独立保留且有上限；每段仍需Read/Grounding。源码：[runtime_backend.py:116 · RuntimeRetrievalBackend.search](../../src/linkloom/retrieval_v2/runtime_backend.py#L116)；[assembler.py:129 · ContextAssembler.assemble](../../src/linkloom/context/assembler.py#L129)。追问：为什么不把全文直接塞给模型？

### 24. “缺document_count和非法alternate，为何要不同处理？”〔3〕

要点：前者source identity不可恢复；后者在受验证计划内可一次纠错/预算内Partial，不能发明新备用查询；错误恢复不放松证据门。源码：[runtime.py:713 · create_retrieval_tool_runtime](../../src/linkloom/tools/runtime.py#L713)；[recovery_policy.py:30 · decide_recovery](../../src/linkloom/runtime/recovery_policy.py#L30)；[test_graceful_partial_recovery.py:82 · test_identity_errors_cannot_be_recovered_as_partial](../../tests/unit/test_graceful_partial_recovery.py#L82)。追问：若有owner但无leader，你会如何保留价值？

### 25. “你如何证明Agent值得这个延迟和成本？”〔3〕

要点：正式查询0模型；两份历史真实多跳中一次检索只答部分，Agent完成但样本极少；区分Provider等待与500文档全库read瓶颈；原冻结12/16不改14/16；不能宣称稳定成功率或未完成前后对照提升。源码：[runtime_adapter.py:150 · RuntimeAgentAdapter._read_documents](../../src/linkloom/agents/runtime_adapter.py#L150)；历史数据：[Reliability LIVE_METRICS](../requirements/real-agent-reliability/LIVE_METRICS.json)、[Recovery LIVE_METRICS](../requirements/agent-recovery-optimization/LIVE_METRICS.json)。追问：你下一个实验怎样设计才可以支持因果性能结论？〔C：待设计，未执行〕

两轮模拟面试建议：第一轮随机抽1/3/5/6/8/10/13；第二轮抽15/16/18/19/22/24/25。先录音再查源码，特别记录“把Host说成模型”“把证据说成审批”“把单次成功说成成功率”三类错误。
