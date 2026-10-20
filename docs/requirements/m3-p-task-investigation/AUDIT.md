# Current call graph and authority audit

`UnifiedAskService.ask/_task_request` -> ProductInvestigationService.start ->
ProductTaskSources (repository source IDs, RawArtifact versions, registry) ->
F TaskObservationDiscoveryService (semantic extraction, deterministic evidence
validation) -> G InvestigationPlanner (model-selected typed steps; Host verifies
schema/scopes/dependencies) -> TaskRelationTools composition -> existing
RuntimeRunBackend/RuntimeEngine -> RetrievalAgent ModelLoop -> ToolRuntime and
durable Tool Ledger -> inspect_task_context/query_task_relations/optional
analyze_task_semantics -> fixed receipt Final -> existing Grounding/Reviewer ->
task_result product projection -> Unified Ask -> existing product.js.

Semantic judgment: F/G and SemanticProposer supply candidates only. Host owns
actual admission, source identities, scope and budgets; Runtime owns calls.
P1/P2/P3 finite proof contracts, replayed by P4 RelationQuery, own the existing
supporting relation verdict. P4 verifies original inputs, full ObservationRefs,
source registry/version, lifecycle and premise chains on every read. Runtime
Reviewer reexecutes query/inspect and verifies semantic evidence; it never
confirms the model's business meaning. G ready_steps require SUPPORTED for
relation dependencies. Missing evidence, unknown/conflict/refutation, unsupported
scope and revoked sources must block the affected work or ask for clarification.

P4 is not a human approval service. TaskRelationStore can capture immutable
assessments and append ACTIVE/STALE/RETRACTED/REPLACED/INVALIDATED events. Actor
is supplied text; there is no authenticated approval event or product relation
review authority contract. ACTIVE alone never establishes trust. Query replays
assess_same_task/assess_plan_revision and rejects invented SUPPORTED payloads.
The existing decision/feedback review interfaces concern different entities and
do not authorize task identity. No new approval button is safe in this scope.

Before: product preparations never supply relation_path; query reports
NO_RELATION_RECORDS even if an external verified sidecar exists. The UI shows
generic confirmation text, plan questions and raw trace. Original observations,
field evidence and valid proposals are available but not made directly useful.
_jobs/_runs are process-local, although underlying Runtime evidence persists.

Independent-review repair: the retained source bytes are not an authorization
catalog. ProductTaskSources is now bound to ProductRepository.current_evidence
in both the hot and saved-read composition. Every parsed original span must
still have exact current catalog membership before use; a revoked evidence
reference cannot be regenerated from unchanged text/version alone.

Implementation ownership: main handles product projection/UI and saved reads;
one bounded worker handles existing P4 tool-to-step binding. Final independent
Reviewer checks the entire isolated diff. User authorization permits this
autonomous implementation; no extra phase approval is inferred from stale docs.
