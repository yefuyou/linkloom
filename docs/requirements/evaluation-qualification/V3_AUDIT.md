# V3 资格失败归因

旧 dev/final、Gold、分数及 Final 已曝光身份全部保留。这里只读解释证据，不重跑旧 Final。

分类：1=合法输入且证明生产失败；2=准入不合法；3=Runner未执行目标；4=观测不足；5=Gold与目标不一致；6=缺目标执行证据。分类可重叠，空集表示历史通过保留。

| Case | 分类 | 原因 |
|---|---|---|
|RAC3-D01|3,5|Repeated malformed Final was scripted, not a successful correction. Gold business_status=complete despite rejected Final; only owner passage was read.|
|RAC3-D02|历史通过|Observed valid completion, two facts and Reviewer. Historical pass retained.|
|RAC3-D03|3|UNVERIFIED_ABSTENTION identifies unsearched leader subgoal. Controller finalized after owner read; absence was not investigated.|
|RAC3-D04|2,3|Single-owner question has no admitted multi-hop plan. Partial/conflict without validated subgoal coverage was correctly rejected (UNVERIFIED_ABSTENTION).|
|RAC3-D05|3|One paragraph was read; Final claimed only owner. Gold requires two relations across separate passages. Runtime accepted only the grounded fact.|
|RAC3-D06|2,3,6|TaskPlan.goal differs from query. Runtime tool boundary was never reached; identity/dependency/executor guards were not executed.|
|RAC3-D07|3|Controller issued one read for a multi-passage file then Final. Second relation never became verified context.|
|RAC3-D08|4,5|Context byte limit was not wired to Runtime; count limit is not byte accounting. Dropped-noise guard uses read-path absence, not ContextAssembler audit.|
|RAC3-D09|历史通过|Final qualified evidence records actual MODEL_MAX_STEPS_EXCEEDED, Search+Read and no Final. Earlier provisional concerns are superseded by this saved trace.|
|RAC3-D10|4|Runtime unknown-outcome and one model invocation observed. Fake adapter has no paid Provider reservation ledger; append-only/cost guard is unobserved.|
|RAC3-F01|3,4,6|post_fix_provider_input_only has no scripted tools or foreign-workspace action. Empty malformed Finals cannot prove workspace isolation.|
|RAC3-F02|3,4,6|No source update or stale read was executed; repeated malformed Final is not freshness evidence.|
|RAC3-F03|3,4,6|No Search/Read or scoped Final; no observations for scope guards.|
|RAC3-F04|3,4,6|Unscripted mode generated empty Final, not the specified unknown-field/schema-boundary stimulus.|
|RAC3-F05|3,4,6|No owner read or missing-hop search. Partial target never reached.|
|RAC3-F06|2,3,4,5,6|No opposing reads. The registry-names-X-as-owner phrasing yields no supported typed facts in the existing claim grammar. Gold also mandates a context-only evidence path without exact fact spans. No authority inference was tested.|
|RAC3-F07|2,3,6|TaskPlan.goal differs from query; alternate/dependency/source-context boundary not reached.|
|RAC3-F08|3,4,6|No distinct reads or real context-byte selection occurred; context target unobserved.|
|RAC3-F09|3,6|Unscripted empty Final fails format before step exhaustion; MODEL_SCHEMA_CORRECTION_EXHAUSTED is not a budget-boundary witness.|
|RAC3-F10|2,3,6|TaskPlan.goal differs from query; provider_outcome=not_scripted never injects uncertain response. No unknown-outcome target evidence.|

没有一项凭现有记录足以归为类别1；这不意味着生产系统没有缺陷，也不把未观察的安全行为算通过。D09的最终保存记录确实达到MODEL_MAX_STEPS_EXCEEDED，不能沿用早期“控制器提前结束”的猜测。

D06、F07之外，F10也有goal/query不一致。F01-F09中八项只有未脚本化的空Final，不能证明Workspace、Freshness、Conflict或Budget行为。D03/D04的UNVERIFIED_ABSTENTION是缺少合法覆盖；D05/D07只读一个段落；D08未落实字节上下文限制；D10没有Provider预留账本。

新Preflight对旧20项输出具体输入、计划、观测和Gold诊断，见V3_PREFLIGHT.json。旧pseudo身份在旧runner中会转换为Scanner身份，静态报告UNMATERIALIZED_SOURCE_IDENTITY说明旧记录不可直接执行，并不指控旧runner把这些伪SHA当真实身份。新用例直接使用Scanner材料化，取消此歧义。

逐项错误码、实际结果、未观察guard和旧文件SHA见V3_AUDIT.json。新测试版本另存，不回写历史成绩。
