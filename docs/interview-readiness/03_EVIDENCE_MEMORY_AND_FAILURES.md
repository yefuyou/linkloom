# 证据、记忆与故障：回答追问的依据

基线 `cd668f3`；A/B/C见 [00](00_START_HERE.md)。**A** 下的关键输入输出和接力函数见本页与 [02实验](02_AGENT_LOOP_LAB.md)；**B** 都链接已提交的历史材料，本次真实Provider请求0。工程设计理由/后续方案标C。

## Retrieval → Evidence → Grounding

**A**：[runtime_adapter.py:165 · RuntimeAgentAdapter._search_notes](../../src/linkloom/agents/runtime_adapter.py#L165) 接query/source_context/limit，调用Backend得到候选，再交ContextAssembler选取，返回证据记录列表`list[dict]`；[runtime.py:423 · ToolRuntime.execute](../../src/linkloom/tools/runtime.py#L423) 校验后将它包装为一个`ToolResult(value=[...])`，下一步必须用返回的Evidence ID调用Read。[runtime_backend.py:116 · RuntimeRetrievalBackend.search](../../src/linkloom/retrieval_v2/runtime_backend.py#L116) 把文档排序结果重新投影成有行号和hash的passage，不是全文自动可信。

| 概念/实际函数 | 输入→输出；边界与验证 |
|---|---|
| [retrievers.py:80 · BM25Retriever.retrieve](../../src/linkloom/retrieval_v2/retrievers.py#L80) | query→词法排名；本次产品演示显式BM25。[runtime_adapter.py:86 · RuntimeAgentAdapter.__init__](../../src/linkloom/agents/runtime_adapter.py#L86) 在未配置时默认hybrid，别把本次设置说成所有环境默认 |
| [retrievers.py:98 · DenseRetriever.retrieve](../../src/linkloom/retrieval_v2/retrievers.py#L98) | query embedding→向量相似结果；可选本地模型，不代表此次加载过 |
| [retrievers.py:122 · HybridRetriever.retrieve](../../src/linkloom/retrieval_v2/retrievers.py#L122) | 两通道各top20→RRF融合：Σ1/(60+rank)，避免直接混不同分数尺度；不保证事实正确 |
| [assembler.py:129 · ContextAssembler.assemble](../../src/linkloom/context/assembler.py#L129) | scoped候选→selected/dropped、预算审计；默认max_evidence=5，按Evidence ID去重，证据优先于Memory/experience |
| [retrieval.py:35 · retrieve_evidence](../../src/linkloom/retrieval.py#L35) | 来源内容/行与workspace→Evidence ID、quote SHA、content SHA、line range；ID绑定片段身份而非一个随意序号 |
| [runtime_adapter.py:521 · RuntimeAgentAdapter._read_verified_note_for_tool](../../src/linkloom/agents/runtime_adapter.py#L521) | 本run Search见过的ID→`_read_verified_note`重新读来源，检查content hash/路径/原文quote及quote hash→片段；未知ID/路径不能绕过 |
| [team_decision.py:656 · TeamDecisionResult.from_grounded_final](../../src/linkloom/agents/team_decision.py#L656) | 模型Final+visible evidence+Read refs→结构化结果或拒绝；[claim_grounding.py:715 · assess_claim_text](../../src/linkloom/agents/claim_grounding.py#L715) 核对主张实体、方向、日期与所引原文 |
| [reviewer_agent.py:15 · ReviewerAgent.execute](../../src/linkloom/agents/reviewer_agent.py#L15) | result及refs→逐引用验证与subject合同→ReviewDecision；不能授予业务批准 |

Search结果即使带`status=verified`，也不等于本次已执行Read。ID存在、来源仍新鲜、主张被那段原文支持，是不同检查。Product UI再把Runtime临时路径映回source_id/source_version，并核对当前版本；版本变更会使旧Final失效。证据：[02主Trace](evidence/three-hop-summary.json)、`test_three_hop_conflict_preserves_alias_and_owner_and_source_change_invalidates_final`。

**B：历史Retrieval V2评测**用30例、五种检索模式、离线MiniLM缓存；指标不是当前三跳成功率，见 [AG-2报告](../evaluation/retrieval_v2_ag2_report.md) 和 [下游对照](../evaluation/retrieval_v2_downstream_comparison.md)。C：一次检索通常便宜，若首问不含下一跳名字，逐跳发现实体才可能值得多轮代价。历史两例简单RAG只答部分、Agent完整，不能推广所有问题均需Agent。

## Temporal Decision Memory：正式业务状态

**A**：来源→[product.py:1248 · ProductApplication._ingest](../../src/linkloom/ui/product.py#L1248)：RawArtifact分段成精确EvidenceSpan，Extractor给Candidate；候选有来源、类型、时间与置信度，但还不是正式事实。[product.py:1467 · ProductApplication._approve_candidate](../../src/linkloom/ui/product.py#L1467)接人类reviewer/reason及必要resolution→[materialization.py:752 · SemanticDecisionMaterializer.approve](../../src/linkloom/semantic_ingestion/materialization.py#L752)绑定workspace、candidate fingerprint和审批事件→[materialization.py:919 · SemanticDecisionMaterializer.materialize](../../src/linkloom/semantic_ingestion/materialization.py#L919)重新核来源/授权/策略，才写Temporal record及receipt。

`valid_from`表示业务生效时间，不是模型抽取时间或审核时间；新值可以supersede旧值，保留历史链。[product.py:997 · ProductApplication.recover_decision](../../src/linkloom/ui/product.py#L997) 接subject/relation/as_of→Store选择该时点有效记录→来源核验→当前/历史视图。缺生效信息需人解决，不能让LLM默认补成“现在”。出处过期会呈现 `STALE/NEEDS_REVALIDATION`，不是无声改写为新事实。

Birchline/Wrenwell/Cedar应画成**两条正式版本加一个并列提案**：

```text
Birchline（已审，后来SUPERSEDED） → Wrenwell（已审，当前ACTIVE）
Cedar（PROPOSAL，未批准） ── 保留为supporting proposal，不进入当前正式链
```

本次复验的transport合成案例中Birchline为10月1日决定，Wrenwell为10月7日替代决定，Cedar为10月9日未批准提案。Wrenwell来源支持“更换了谁”，没有支持更换原因，因此current=Wrenwell但replacement_reason仍未知。**有更换事实，不能推出更便宜/更稳定等原因。** 测试：[test_product_transport_http.py:169 · test_formal_http_runtime_payload_and_ledger_are_unchanged](../../tests/integration/test_product_transport_http.py#L169)。另一个segment测试的日期设置不同，专门验证生效与审核分离，不能混搬日期：[test_product_segment_recovery.py:104 · test_targeted_recovery_receipts_and_complete_flow](../../tests/integration/test_product_segment_recovery.py#L104)。

## Feedback Memory与运行记忆

**A**：[feedback_memory.py:74 · FeedbackMemory.action](../../src/linkloom/ui/feedback_memory.py#L74)执行Propose→Pending→Approve→Active→Revoke（另有reject/expire）。只接受`decision-term/v1`，单个2–40字符术语映射到本workspace已有已审relation；不能覆盖已知实体/术语、不能存任意指令。approve等生命周期动作需`human_confirmed=true`。[feedback_memory.py:118 · FeedbackMemory.snapshot](../../src/linkloom/ui/feedback_memory.py#L118) 每次重载active状态，按PROJECT+workspace+key过滤，冲突fail closed，再把映射给scope discovery。撤销后不再路由，反馈ID不是业务引用，不进模型prompt。测试：`test_feedback_memory_flow.py`本次12项通过。

| 层 | 数据来源/更新 | 审核与作用域 | 能否影响正式答案/撤销 |
|---|---|---|---|
| Temporal Decision Memory | 来源证据候选→授权后物化 | 当前产品Human Review；slot/workspace/时间/版本 | 是，正式事实来源；supersession/来源失效保历史与审计 |
| 运行证据/历史 | 本run Search/Read/ToolResult→Ledger及受限投影 | 不逐条人审；run/task/agent/workspace/source过滤 | 支撑一次有据回答，不能自己物化；新鲜度失效/结束/上下文裁剪 |
| Human-Governed Feedback | 人提出术语→pending→approve | 显式人确认；workspace/已有relation/限定term | 只影响找哪一个已审slot，不新增事实；revoke/expire即时生效 |
| AgentMemoryCandidate | 已grounded且Reviewer接受的运行结果→capture | `NON_AUTHORITATIVE / NOT_AUTHORIZED / CANDIDATE`；仍需独立授权和物化 | 捕获本身不能变正式答案依据；候选reject/stale，未授权不得materialize |

[runtime_adapter.py:656 · RuntimeAgentAdapter._capture_agent_memory_candidate](../../src/linkloom/agents/runtime_adapter.py#L656)只把符合条件的完成结果交共享生命周期capture，返回review_required。[memory_candidate.py:428 · AgentMemoryCandidate](../../src/linkloom/agents/memory_candidate.py#L428)不持有物化权；[materialization.py:919 · SemanticDecisionMaterializer.materialize](../../src/linkloom/semantic_ingestion/materialization.py#L919)没有durable authorization就拒绝，审批后来源变化也拒绝。共享框架另有版本化policy AUTO_ALLOW授权入口，但不等于Agent自主审批；当前产品演示走人审。来源多跳关闭整个Decision Memory工具/capture，见02的隔离测试。

## 四个适合讲的工程故障

### 1. 工具读到了，Evidence Context却空了

**Situation/Investigation（B）**：真实两跳、三跳工具成功，后续请求的evidence_context全空；沿request artifact与Ledger对比发现。历史：[Reliability交付E节](../requirements/real-agent-reliability/DELIVERY.md)、[安全审计](../requirements/real-agent-reliability/SECURITY_AUDIT.md)。

**Root cause/Fix（A+B）**：Product业务域用`ws_*`，Vault位置指纹用`root_*`，旧调用把root当workspace传给过滤器。当前 [model_loop.py:275 · SingleAgentModelLoop._verified_evidence_context](../../src/linkloom/runtime/model_loop.py#L275)分别接workspace_id/source_fingerprint；调用处分别传Product workspace与Runtime source。**Tool History是另一份带独立预算的投影，存在不代表Evidence Context完整。**

**Validation（A）**：`test_product_evidence_context_scope.py`三项检查Product证据保留、foreign workspace/错误指纹拒绝、旧历史裁剪时Read优先；本次通过。**B**：历史修复后两跳context计数`[0,1,1,2,2]`、三跳`[0,1,1,2,2,3,3]`；真实回归成功。

**Tradeoff（C/B）**：保留证据增加输入token，不能宣称降本。Fake控制器可在自身reads缓存、最新Observation或tool history找事实，可能掩盖空Evidence Context；所以“Fake答对”不足以证明模型上下文正确。未证实跨workspace泄露，别把丢失Bug说成已发生数据泄露。

### 2. Source Identity只检查“传了的字段”

**Situation/Investigation（B）**：旧审计构造只含scope_path的source_context，executor仍执行；后来真实Canary又遗漏document_count而被严格新门拒绝。这是两个阶段，不能混写成Runtime缺字段却必须放行。

**Root cause/Fix（A+B）**：旧逻辑比较调用方提供项，缺项不比；当前 [runtime.py:713 · create_retrieval_tool_runtime](../../src/linkloom/tools/runtime.py#L713) 内`validate_search_arguments`遍历全部expected项，核键存在、JSON类型和值；只有expected=null的fixture元数据可省略。模型提示在 [retrieval_agent.py:184 · RetrievalAgent._retrieval_instruction](../../src/linkloom/agents/retrieval_agent.py#L184) 明确复制document_count等字段，不放松校验。

**Validation（A）**：`test_source_identity_contract.py`逐项删字段/类型混淆均拒绝且不调callback；完整身份才执行。**B**：[Recovery交付C节](../requirements/agent-recovery-optimization/DELIVERY.md)保留missing Canary首试因缺document_count失败、提示修复后成功。

**Tradeoff（C）**：更严格合同会暴露旧Fake夹具，增加失败但保护执行域；本次发现5个旧Runtime capture集成例仍只传intent而失败，见末尾。历史审计未证明已实际跨域泄露，不扩大结论。

### 3. 同文件两个关系只剩一个passage

**Situation/Investigation（B）**：冻结O同文件owner+leader，只得到owner而安全partial；修第一层后Canary仍漏读，沿selected/dropped发现第二层。来源：[Recovery COVERAGE](../requirements/agent-recovery-optimization/COVERAGE.md)。

**Root cause/Fix（A+B）**：Retriever每文档只投影一条正文；ContextAssembler又按source_ref文件去重。当前 [runtime_backend.py:116 · RuntimeRetrievalBackend.search](../../src/linkloom/retrieval_v2/runtime_backend.py#L116)保留匹配正文passages、BM25按片段相关度重排后top_k；[assembler.py:129 · ContextAssembler.assemble](../../src/linkloom/context/assembler.py#L129)按Evidence ID去重，同文件不同span能共存。

**Validation（A）**：`test_recovery_passage_coverage.py`验证两片段不同ID、保留hash/行号、top_k与内容更新失效；`test_distinct_passages_from_one_source_survive_with_the_same_caps`验证第二层。**B**：same-file Canary 6请求、2Search/3Read、3个distinct passages，Maya Qin被接受；正式同题after未发送，因果A/B为N/A。

**Tradeoff（C）**：更多片段会花更多Read/token，仍有每文档候选上限和context预算。整篇文档可能含多个不相关/冲突/指令片段，不能仅凭文件命中把全文授为某主张的可信Evidence。

### 4. 下游缺失，非法alternate后丢了已知前缀

**Situation/Investigation（B）**：旧LIVE-MISSING 6轮，非法alternate被正确拒绝后模型收到Observation，第6轮给null Final但rationale空，未通过运行/Reviewer；正式另一次baseline7请求也失败。[RECOVERY说明](../requirements/agent-recovery-optimization/RECOVERY.md)保留这一区别。

**Root cause/Fix（A+B）**：恢复只要求纠错，没识别“已搜索、候选已Read、无计划alternate”的停止点；结果层把一次已放弃查询错误永久当失败。[progress.py:42 · assess_progress](../../src/linkloom/runtime/progress.py#L42)计算可达缺失/冲突/耗尽；[recovery_policy.py:30 · decide_recovery](../../src/linkloom/runtime/recovery_policy.py#L30)建议FINAL_PARTIAL，模型仍须提交原协议null主答案+独立有据rationale；[retrieval_agent.py:589 · RetrievalAgent._project_result](../../src/linkloom/agents/retrieval_agent.py#L589)只允许特定单次计划查询错误被该Final放弃。

**Validation（A）**：`test_graceful_partial_recovery.py`6项本次通过：合法partial、身份/缺身份不可恢复、第二错停止、预算耗尽非partial、伪造rationale拒绝。**B**：missing Canary after-02 5请求保留Lena Frost；正式after首请求Provider unknown，因此正式改善对照N/A。

**Tradeoff（C）**：Partial保留可信前缀，不保证每次完成；身份、版本、权限错误不能借partial洗白，预算到顶不能把未验草稿改成成功。

## Runtime停止与恢复：先分类再行动

**A**：[models.py:671 · TerminationState](../../src/linkloom/runtime/models.py#L671)记录终止；`RetrievalAgent.execute`把task max_steps、run max_steps和剩余provider预算取最小值，ToolPolicy再限工具数。[model_loop.py:834 · SingleAgentModelLoop._run_durable](../../src/linkloom/runtime/model_loop.py#L834)持久化请求/响应/观察；Progress只提建议，不拥有执行权。

| 状态 | 正确处理与证据位置 |
|---|---|
| 业务事实未知 | 可在预算内提交null主答案+有据前缀；不是执行异常；Recovery六例 |
| 证据冲突 | 保留双方引用、争议主答案为空；不得多数投票强选；产品HTTP冲突例 |
| 模型/工具已知执行失败 | 回传明确错误；按限定策略一次纠错或停止，身份/权限不可放松 |
| max_steps/provider预算耗尽 | 没有在界内接受Final就FAILED；不可事后包装Successful Partial；预算测试 |
| Tool Budget到顶 | STOP_TOOL_USE只能用已有证据尝试Final，仍受剩余模型预算和全部验证门，不保证成功 |
| Provider UNCERTAIN | 网络结果未知不等于业务未知；没有可验证Final，不展示草稿，也不自动重发 |

[recovery.py:115 · decide_model_resume](../../src/linkloom/runtime/recovery.py#L115)区分durable request/response与需核验的未知结果；`ModelProviderError.retryable`只是分类，不授予无限重试。请求可能已计费/执行，重发需保留原身份与累计预算；产品来源调查当前没有跨进程resume API。

## 两组历史Gemini：B，不是本次效果

历史模型`gemini-3.8-flash`、SDK `2.22.0`，合成workspace、串行、SDK自动重试0。费用使用**当时报告价格**估算，非当前报价、非账单；本次不补造费用数据。

| 批次与来源 | 已记录总体 |
|---|---|
| [Reliability LIVE_METRICS](../requirements/real-agent-reliability/LIVE_METRICS.json) / [交付B、D节](../requirements/real-agent-reliability/DELIVERY.md) | 50请求（47有Usage、3未知）；input241,568/output8,003/thinking23,752；已知$0.30025725、含未知预留上界$0.68732925；约48分钟 |
| [Recovery LIVE_METRICS](../requirements/agent-recovery-optimization/LIVE_METRICS.json) / [交付](../requirements/agent-recovery-optimization/DELIVERY.md) | 13 logical runs/63请求（61有Usage、2未知）；input379,399/output9,506/thinking45,328；已知$0.49017675、含预留上界$0.74822475；phase wall3997.23s，case elapsed合计617.04s，两者分母不同 |

| 样本 | 模型轮次；S/R | input / output / thinking | 总耗时；Provider成功等待 | 已知估价 |
|---|---|---|---|---|
| Reliability修复后两跳 | 5；2/2 | 25,511 / 954 / 3,486 | 41.17s；约40.05s | 约$0.035783 |
| Reliability修复后三跳 | 7；3/3 | 47,400 / 1,372 / 3,290 | 42.39s；约40.76s | 约$0.053033 |
| Recovery两跳after | 5；2/2 | 26,554 / 937 / 3,508 | 54.99s；不借前批等待数代填 | $0.03658425 |
| Recovery三跳after | 7；3/3 | 47,541 / 1,300 / 4,154 | 67.16s；同上 | $0.05610825 |

Recovery分类为5 full、1 partial、4 runtime failure、1 admission failure、2 Provider unknown；第二个strict unknown后停批。只有正常两跳/三跳同题baseline/after可比，成本/耗时略升；缺失/同文件/冲突的正式after缺数据。Canary成功证明路径能工作，不证明生产成功率提高。真实TaskPlan全部Host编译；model planning窄路径虽在代码中，未在这些UnifiedAsk真实Gemini运行获验证。

**B：500文档瓶颈**：[PERFORMANCE](../requirements/agent-recovery-optimization/PERFORMANCE.md)、[SCALE_METRICS](../requirements/agent-recovery-optimization/SCALE_METRICS.json)。最终每规模3次成功的500份中位E2E104,234.32ms，read_notes90,085.21ms；每成功两跳12次全库read_notes。[runtime_adapter.py:150 · RuntimeAgentAdapter._read_documents](../../src/linkloom/agents/runtime_adapter.py#L150)等边界反复全库读/哈希，Verified Read独占校验仅毫秒级。旧桶retrieval含嵌套read，不可相加；不同批次硬件负载/observer不同，不构成提速证明。**C**：任务内不可变snapshot+边界freshness缓存是后续方案，尚未实现/测得收益，mtime不能独自代表版本。

## 本次验证、未验证与面试禁语

主线程14项（workspace/passage/plan）通过，Recovery6项通过，产品source HTTP10项通过。**除下述5个Runtime Memory capture端到端失败外**，独立调查中的Temporal/Memory候选与权限、identity/context聚焦检查通过，另有Feedback HTTP12项、正式transport1项、segment恢复1项通过；不把重叠命令相加成新总成绩。准确命令、环境和独立复查见 [验证索引](evidence/verification.md)。没有全仓绿测或CI声明。

当前已知缺口：`test_runtime_agent_memory_capture.py`本次5例失败，旧Fake仅传`source_context.intent`，触发严格身份门，未到candidate capture。候选/共享授权单测通过，不能据此宣称Runtime capture端到端全绿；本次按范围只记录。初次主线程6个temp权限失败改用work临时目录后通过；独立调查一次漏设BM25导致HuggingFace元数据访问尝试被沙箱阻断，设BM25后离线完成，无新真实LLM请求。

**保留成绩**：历史首次冻结12/16原样；Recovery新冻结首次11/12、Canary回归与已暴露回归另计，不能改成14/16。见两份DELIVERY。适合面试讲的是证据身份拆分、双层passage定位、有界partial恢复、人治理Memory和可复查Trace；暂不说任意多跳、自动审批、模型自进化、企业RBAC、稳定生产成功率、已证明降本提速或个人独立手写全栈。
