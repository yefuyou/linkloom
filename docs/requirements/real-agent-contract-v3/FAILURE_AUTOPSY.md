# V3 跨分支故障归因

归因在生产改动前完成；29项历史运行全部只读，不重放付费请求。完整逐项分类、原始请求/响应与SHA见 RESULTS.json 的 autopsy。缺失证据保留 UNKNOWN。

## Adaptive：18项

|案例|错误|已核验子原因|
|---|---|---|
|AP-D06|TASK_PLAN_FINAL_SCOPE_MISMATCH|Plan structure was accepted, but final binding used 星河桥 while the step_1 question binds 星河桥项目; final scope validation rejected the claim.|
|AP-D08|TOOL_INVALID_ARGUMENTS|Nested timestamp wrapper left claim-grounding facts empty; verified_dependency_entities fail-closed.|
|AP-D09|MODEL_RESPONSE_UNSUPPORTED|Provider details.reason=undeclared_tool; SDK_PARSE rejected the response shape.|
|AP-D16|MODEL_UNKNOWN_FAILURE|StrictProviderOutcomeUnknown/PROVIDER_OUTCOME_UNCERTAIN after RemoteProtocolError; response_accepted=false.|
|AP-D21|TOOL_INVALID_ARGUMENTS|Nested timestamp wrapper left claim-grounding facts empty; verified_dependency_entities fail-closed.|
|AP-D23|MODEL_RESPONSE_UNSUPPORTED|Provider details.reason=undeclared_tool; SDK_PARSE rejected the response shape.|
|AP-H02|TEAM_DECISION_CONTRACT_ERROR|Plain prose Final reached TeamDecisionResult.from_grounded_final.|
|AP-H04|TASK_PLAN_FINAL_SCOPE_MISMATCH|TaskPlan structural validation passed but semantic alias hop was omitted.|
|AP-H05|TOOL_INVALID_ARGUMENTS|Nested timestamp wrapper left claim-grounding facts empty; verified_dependency_entities fail-closed.|
|AP-H07|MODEL_RESPONSE_UNSUPPORTED|Provider details.reason=undeclared_tool; SDK_PARSE rejected the response shape.|
|AP-H09|MODEL_RESPONSE_UNSUPPORTED|Provider details.reason=undeclared_tool; SDK_PARSE rejected the response shape.|
|AP-H11|TEAM_DECISION_CONTRACT_ERROR|Code-fenced JSON stopped at finish_reason=max_tokens.|
|AP-H13|MODEL_UNKNOWN_FAILURE|StrictProviderOutcomeUnknown/PROVIDER_OUTCOME_UNCERTAIN after RemoteProtocolError; response_accepted=false.|
|AP-V01|TASK_PLAN_FINAL_SCOPE_MISMATCH|Conflict refs were top-level only; uncertainty.evidence_refs was empty.|
|AP-V02|MODEL_UNKNOWN_FAILURE|StrictProviderOutcomeUnknown/PROVIDER_OUTCOME_UNCERTAIN after RemoteProtocolError; response_accepted=false.|
|AP-V14|MODEL_RESPONSE_UNSUPPORTED|Provider details.reason=undeclared_tool; SDK_PARSE rejected the response shape.|
|AP-V16|MODEL_RESPONSE_UNSUPPORTED|Provider details.reason=undeclared_tool; SDK_PARSE rejected the response shape.|
|AP-V17|TOOL_INVALID_ARGUMENTS|Nested timestamp wrapper left claim-grounding facts empty; verified_dependency_entities fail-closed.|

## V2：11项

六个 Contract Error 均复调原validator重现 JSON 解析失败：四份截断 JSON、两份非 JSON 反思，均以 max_tokens 结束。不是六次 Grounding 拒绝。D11为空输出；D21-v1缺失响应与异常，根因UNKNOWN；D24-v1为零工具声明时返回undeclared_tool。两项正确结果包括一个合法Partial。

## 共同与不同边界

共享核心代码一致，但不能合并为单一根因。两专项都有输出耗尽与零工具时非法调用；Adaptive另有计划实体后缀、冲突引用和Harness二次时间戳包装。四个ToolInvalid均为dependency_entities语义校验，不是遗漏document_count。保留拒绝，避免为修复Harness而放宽关系或来源验证。

三个Adaptive严格未知被Adapter将字符串code传给HTTP状态判断，误标known_failure；V3修复typed未知优先级并禁止嵌套超时覆盖。

新Final修正仅允许一个新模型回合，原输出不补写为成功。是否增加成功率仍需新实验；预算耗尽和无效修正继续失败。
