# SPEC: M1.1 Team Decision & Action Vertical Slice

Status: **APPROVED — FIRST WORKER BATCH**

`WORKER AUTHORIZED — CONTRACT + mps-001 PRODUCTION-PATH PROOF ONLY`

## 1. Authority and baseline

This Child SPEC is governed by the repository `SPEC.md`, `AGENTS.md`,
`docs/PRODUCT_ROADMAP.md`, and
`docs/requirements/linkloom-master/MASTER_SPEC_V2.md`. It narrows Gate D to
the smallest read-only production Team Decision & Action slice. It does not
replace the canonical product roadmap or reopen M0.

Planning baseline:

- master SHA: `54ca4a5b03d8c3e7753f0b2e2d9cd0125923db20`;
- M0.1, M0.2, M0.3, and M0.4: `ACCEPTED`;
- Gate A, Gate B, and M0: `COMPLETE`;
- Team Decision Eval Seed: `ACCEPTED AS EVAL SEED`;
- canonical Git/LF dataset SHA-256:
  `49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`;
- Golden 8: `FORMALLY FROZEN`.

The accepted dataset and Golden 8 are evaluation authorities only. Their
expected values, labels, required claims, and trajectories must never be
included in production instructions or model-visible context.

## 2. Problem

The production runtime can already let a model choose `search_notes` and
`read_verified_note`, execute those calls through the local `ToolRuntime`,
observe durable results, and stop with Final. That path currently returns an
evidence bundle rather than a trustworthy business answer. The model Final is
free text and `RetrievalAgent._project_result()` discards it; `Coordinator`
therefore publishes only evidence and review metadata.

LinkLoom needs one minimal production path that answers an already-recorded
team-decision question as a provider-neutral, schema-validated,
claim-grounded `TeamDecisionResult`.

## 3. User story

As a project-team member, I can ask a natural-language question about an
already-recorded decision and receive a structured result that clearly states
what is known, what remains unresolved, and which verified evidence supports
each material claim, without modifying any note.

Representative product question:

> What did we finally decide, why did we reject the other option, and what is
> still unfinished?

The first executable acceptance target is the frozen `mps-001` case. It proves
the smallest complete path for direct decision recovery before later work
attempts multi-note synthesis or all Golden 8 semantics.

## 4. Exact M1.1 product boundary

```text
natural-language team-decision question
  -> RunRequest(workflow="team_decision")
  -> existing RuntimeEngine.start_multi_agent
  -> existing RuntimeAgentAdapter and Coordinator
  -> existing model-driven RetrievalAgent
  -> existing SingleAgentModelLoop
  -> model-selected search_notes / read_verified_note
  -> existing ToolRuntime, policy, ledger, checkpoint, and resume
  -> verified model-visible evidence
  -> model Final containing one TeamDecisionResult JSON object
  -> deterministic schema and evidence-boundary validation
  -> AgentResult.output_payload
  -> Coordinator result artifact
```

M1.1 reads only synthetic, already-indexed Markdown workspaces. It answers
questions about recorded decisions and actions. It does not create or execute
new actions.

### 4.1 First executable increment

`mps-001` is the first RED-to-GREEN target:

```text
question
  -> search_notes
  -> read_verified_note
  -> Final TeamDecisionResult
```

The controlled model must choose the search and read arguments from actual
observations. The result must recover the approved decision, exclude the
prototype/adoption confusion, and attach the decision claim to a verified
evidence reference visible to the model. Fields not relevant to the direct
question may be empty arrays; they must not contain invented material.

This first increment is an implementation checkpoint, not acceptance of all
Golden 8 behavior and not completion of Gate D.

## 5. Scope

M1.1 includes:

- one additive `workflow="team_decision"` execution boundary while existing
  `ask` and `connect` workflows preserve their accepted behavior;
- one provider-neutral `TeamDecisionResult` v1 contract;
- business-semantic instructions that describe the contract and semantic
  rules without exposing Gold answers;
- strict parsing and boundary validation of model Final JSON;
- claim-level references validated against evidence actually surfaced by
  successful local tool results;
- structured projection through `AgentResult`, `Coordinator`, and the existing
  Runtime result artifact;
- RED-first offline tests using controlled model behavior;
- a first production-path acceptance proof for `mps-001`;
- a measured Golden 8 evaluation step after the first checkpoint, with
  thresholds separately approved by the human.

## 6. Non-goals

M1.1 does not include:

- retrieval ranking changes, embeddings, vector search, hybrid search, or RAG
  expansion;
- RuntimeEngine, model-loop, checkpoint, recovery, Provider adapter,
  ToolRuntime, ledger, Memory, or Coordinator architecture redesign;
- a deterministic Python business extractor or hard-coded case workflow;
- Gold values, labels, answers, expected trajectories, or case IDs in a
  production prompt;
- writeback, Markdown mutation, task creation, external-system action, HITL,
  approval, or irreversible execution;
- UI, MCP, a new multi-agent framework, Planner/Worker/Reviewer agents, or
  generic autonomous planning;
- Provider retry, streaming, new Providers, or real-provider calls;
- the broad 30-case quality program, LLM judge, or regression dashboard;
- M1.2, M2, or M3 implementation.

## 7. Reused production architecture

| Existing component | Reused responsibility | M1.1 boundary |
|---|---|---|
| `RuntimeEngine.start_multi_agent` / `resume_multi_agent` | request persistence, state, result artifact, fresh/resume composition | Extend the existing multi-agent workflow allow-list with `team_decision` and reuse the persisted workflow; no state-machine redesign |
| `AgentRegistry` | specialist identity and workflow capability boundary | Allow coordinator, retrieval, and reviewer identities to receive `team_decision`; Curator remains connect-only |
| `RuntimeAgentAdapter` | verified `VaultReader` callbacks and Coordinator composition | Route `team_decision` through the existing compatibility composition; preserve tool callbacks |
| `Coordinator` | RetrievalAgent task creation, evidence review, final workflow artifact | Propagate validated business payload; do not synthesize it |
| `RetrievalAgent` | model-driven production specialist and AgentResult projection | M1.1 compatibility host only: add locally selected business instruction, strict Final parsing, and claim-ref checks without making Team Decision a permanent retrieval-infrastructure responsibility |
| `SingleAgentModelLoop` | model/tool/observation/Final loop and durable resume | Frozen and reused unchanged |
| `ModelTurnRequest` / `ModelResponse` / `ModelAction` | provider-neutral request and durable response | Final remains JSON text inside the existing Final action; no new loop action kind |
| `search_notes` | model-selected discovery of verified evidence | Frozen for the first executable increment |
| `read_verified_note` | model-selected verified read | Frozen for `mps-001`; current narrow output is recorded as a later expansion limit |
| `ToolRuntime` / policy / ledger | schema, permission, execution, audit, budgets | Frozen and remains sole tool execution authority |
| evidence validation | current-source hash, quote, and evidence identity checks | Reused; TeamDecisionResult adds claim-to-visible-ref membership checks |
| Gemini adapter | maps text Final and tool calls to provider-neutral actions | Frozen; no provider-specific structured-output change |

## 8. Business answer contract

The provider-neutral serialized contract is:

```json
{
  "schema_version": "team-decision-result/v1",
  "decision": {
    "value": "string or null",
    "status": "approved | partial | insufficient_evidence | not_found",
    "evidence_refs": ["opaque verified evidence id"]
  },
  "rationale": [
    {
      "point": "concise evidence-grounded reasoning",
      "evidence_refs": ["opaque verified evidence id"]
    }
  ],
  "rejected_alternatives": [
    {
      "alternative": "recorded alternative",
      "reason": "recorded rejection reason",
      "evidence_refs": ["opaque verified evidence id"]
    }
  ],
  "actions": [
    {
      "description": "recorded action",
      "owner": "string, explicit unassigned, or null",
      "deadline": "YYYY-MM-DD or null",
      "status": "pending | in_progress | blocked | completed | unassigned",
      "evidence_refs": ["opaque verified evidence id"]
    }
  ],
  "unresolved_items": [
    {
      "description": "recorded unresolved item",
      "owner": "string, explicit unassigned, or null",
      "deadline": "YYYY-MM-DD or null",
      "status": "pending | in_progress | blocked | unassigned",
      "evidence_refs": ["opaque verified evidence id"]
    }
  ],
  "uncertainty": {
    "status": "none | partial | insufficient_evidence | conflicting_evidence",
    "statement": "string or null",
    "unknown_fields": ["field path"],
    "evidence_refs": ["opaque verified evidence id"]
  },
  "evidence_refs": ["deduplicated union of material claim refs"]
}
```

### 8.1 Contract choices

- `decision`, `rationale`, rejected alternatives, actions, unresolved items,
  and uncertainty each own their evidence references. A top-level reference
  list alone is not sufficient.
- The top-level `evidence_refs` is a deterministic union for compatibility with
  `AgentResult.output_refs`, Reviewer validation, and RuntimeState projection.
- `owner: null` means no owner was recorded. The string `unassigned` is allowed
  only when evidence explicitly records that state.
- `deadline: null` means no deadline was recorded. Completion dates and review
  dates must not be projected as deadlines.
- Actions are evidence-derived recorded actions only. M1.1 has no suggestions
  field; a suggested next action belongs to a later proposal/HITL slice.
- The status vocabularies are the smallest sets already required by the
  accepted 30-case seed. They are not speculative workflow taxonomies.
- Unknown top-level or nested fields fail schema validation. This prevents a
  model from smuggling Gold, approval, mutation, or suggestion fields into the
  accepted result.

### 8.2 AgentResult projection

The existing `AgentResult` remains the specialist envelope. It gains one
optional JSON-safe `output_payload` field. For a valid M1.1 result:

- `output_type = "team_decision"`;
- `output_payload = TeamDecisionResult.to_dict()`;
- `output_refs = TeamDecisionResult.evidence_refs`;
- `summary` is a short projection, not the source of structured truth.

Existing callers that request or produce `evidence_bundle` retain the current
default and serialized behavior.

## 9. Semantic rules

### 9.1 Current over stale

Authority and recorded status decide precedence before date. A later
authoritative decision record supersedes an older working draft, but a newer
informational or prototype note does not supersede an approved record merely
because its timestamp is later. If authority cannot resolve a conflict, the
result must report `conflicting_evidence`.

### 9.2 Decided versus mentioned

A candidate, prototype, discussion, recommendation, or experiment is not a
decision. `decision.status = approved` requires evidence of approval or an
equivalent authoritative decision state.

### 9.3 Assigned versus proposed

A suggestion is not an assigned action. A named owner requires evidence of
ownership or commitment. Suggested owners remain `null` unless the source
explicitly records `unassigned`.

### 9.4 Completed versus planned

A plan, dry run, preparation step, or scheduled check is not completion.
`completed` requires explicit completion evidence. A completion date must not
be reused as a deadline.

### 9.5 Missing fields

Missing owner and deadline values remain `null`; they are never filled from
plausibility. Unknown requested fields must be listed in
`uncertainty.unknown_fields` when material to the answer.

### 9.6 Rejected alternatives

An alternative appears only when evidence records both the alternative and its
rejection. A rejection reason requires its own supporting refs. Pending review
or lack of approval is not automatically a rejection.

### 9.7 Insufficient evidence

The model must return calibrated uncertainty instead of a forced answer. An
empty successful search may produce `decision.status = not_found`; a searched
evidence boundary that does not establish the requested fact uses
`insufficient_evidence`. Tool failure remains an explicit runtime failure and
must not be converted to business uncertainty.

### 9.8 Evidence versus suggestion

All M1.1 business fields are source-derived claims. Model suggestions are not
accepted in `actions` or `unresolved_items`. Suggestions are deferred to the
later proposal/writeback boundary.

## 10. Evidence discipline

A claim reference is eligible only when all of the following are true:

1. the referenced evidence was present in a successful `search_notes` or
   `read_verified_note` ToolResult visible to the model before Final;
2. the evidence carries a non-empty identity, relative path, current content
   hash, quote, quote hash, and `status = verified`;
3. Runtime evidence validation still resolves it against the current verified
   source;
4. the reference belongs to the current run/task/agent ledger scope.

A path, filename, title, heading, or ref token without supporting content is
not sufficient. When search exposes a complete supporting quote, the frozen
Gold may allow `search_notes -> Final`. When it exposes only discovery
metadata or an incomplete snippet, the model must call `read_verified_note` or
continue searching within the budget.

Deterministic validation proves schema, identity, visibility, and hash
integrity. It does not prove natural-language entailment. Semantic support is
measured by the frozen evaluation target and independently reviewed bad cases.

Every non-null decision, rationale point, rejected alternative and reason,
action, unresolved item, and factual uncertainty statement requires a
non-empty claim-level reference list. Empty/no-evidence outcomes are the only
case where the result may have no evidence refs, and they require a successful
empty search with no prior tool failure.

## 11. Model and Runtime ownership

| Decision or responsibility | Owner |
|---|---|
| Whether and how to search | Model |
| Which discovered evidence to read | Model |
| Whether the visible evidence is sufficient | Model |
| Final decision, rationale, rejected alternatives, actions, unresolved state, and uncertainty wording | Model |
| Mentioned/decided, proposed/assigned, planned/completed, stale/current semantic judgement | Model |
| Allowed tools and tool schemas | Runtime |
| Tool argument validation, permission, execution, budget, and ledger | Runtime |
| Checkpoint, durable artifacts, resume, and hard termination | Runtime |
| Result JSON parsing and schema validation | Runtime |
| Evidence identity, current-source validation, and claim-ref visibility membership | Runtime |
| Semantic entailment scoring against frozen Gold | Evaluation/Reviewer, never production Runtime |

Python must not encode a fixed `search -> read -> final` sequence or copy Gold
answers into the output. Runtime may reject malformed or unsupported output;
it must not rewrite a model answer into a different business conclusion.

## 12. Golden 8 relationship

The matrix below is a planning/evaluation map only. It must never be serialized
into a production prompt. `S`, `R`, and `F` mean `search_notes`,
`read_verified_note`, and Final.

| ID | Scenario and intent | Required business semantics | Temporal / uncertainty rule | Evidence need | Preferred / acceptable trajectory |
|---|---|---|---|---|---|
| `mps-001` | Direct provider decision | Approved provider; no prototype-as-adoption claim | Approved decision record outranks later prototype observation | Decision evidence; rationale only when returned | `S-R-F`; `S-F` only with complete supporting search content |
| `aer-002` | Multi-note rollout boundary | Approved deterministic gate; reporting in progress; multilingual unassigned; threshold inactive | Proposed threshold is not active or assigned | Decision plus reporting, ownership, threshold status/blocker | `S-4R-F`; `S-3R-F` only with omitted facts surfaced by search |
| `drm-003` | Rejected alternative and rationale | Current split policy; 90-day alternative and supported rejection reason; legal sign-off blocked | Superseded draft cannot override approved policy | Review, decision, rejection, legal status/action | `S-4R-F`; `S-3R-F` conditionally |
| `inc-004` | Open remediation actions | Selected mitigation plus three open items with exact owners/deadline/status | Unassigned load test remains unassigned; planned is not complete | Mitigation decision and current open-actions record | `S-2R-F`; `S-R-F` conditionally |
| `ret-005` | Stale/current conflict | Hybrid decision is current; rollout incomplete; pure-semantic draft superseded | Authority/status plus date; later current readiness controls completion | Supersession, final decision, readiness, blocker | `S-4R-F`; `S-3R-F` conditionally |
| `iti-005` | Missing assignment/deadlines | No supported owner/deadline for checksum review; retention explicitly unassigned | Suggested owner is not assigned; absent deadline stays null | Ownership and open-action boundary | `S-2R-F`; `S-R-F` conditionally |
| `drm-002` | Planned versus completed | Split policy; preparation complete; deletion authorization blocked; exception list pending | Preparation is not authorization; proposed owner not assigned | Review, decision, completed work, legal boundary, remaining work | `S-4R-F`; `S-3R-F` conditionally |
| `aer-005` | Partial/insufficient completion evidence | Multilingual slice separate and unassigned; no documented completion | No future owner/date/coverage invention | Rollout scope and unassigned owner | `S-2R-F`; `S-R-F` conditionally |

`mps-001` is the only mandatory executable target for the first Worker
increment. The other seven cases are frozen regression targets used to guide
contract completeness and staged expansion, not permission to broaden the
first implementation batch.

## 13. Demonstrated gaps

### 13.1 Blocking the first executable M1.1 increment

1. The `RunRequest.workflow` allow-list has only `ask` and `connect`; it has no
   `team_decision` business execution boundary.
2. `ModelAction.final_answer` is free text and no `TeamDecisionResult` schema
   exists.
3. `RetrievalAgent` gives only generic retrieval instructions and discards the
   durable model Final during `AgentResult` projection.
4. `AgentResult` cannot carry a structured business payload.
5. `Coordinator` publishes evidence refs and review state but not the model's
   structured business answer.
6. Claim-level evidence membership is not validated; current review checks
   only the aggregate evidence list.

### 13.2 Blocking broader Golden 8 expansion, not `mps-001`

1. Each provider turn sees only the latest ToolResult observation. Earlier
   verified observations are durable but are not assembled into the next
   `ModelTurnRequest`, so multi-note synthesis cannot reliably use all prior
   evidence.
2. `read_verified_note` currently returns the selected search EvidenceRef, not
   a bounded note/section evidence bundle. One read therefore cannot expose
   all relevant sections in one decision record.
3. Retrieval tasks currently have `max_steps = 3`, while frozen multi-note
   preferred trajectories require more model turns.
4. `retrieve_evidence` restarts IDs at `ev_p1_0001` for each search and
   `RuntimeAgentAdapter._evidence_by_id.update()` can overwrite an earlier
   mapping after a later search.

These four gaps must trigger a separate post-`mps-001` planning checkpoint.
They do not authorize a model-loop, retrieval, or ToolRuntime rewrite in the
first Worker increment.

### 13.3 Nice to have or deferred

- provider-native response schemas: deferred; strict local parsing is the
  provider-neutral first baseline;
- richer confidence scores: deferred because the frozen seed does not provide
  calibrated probabilities;
- natural-language entailment validation inside Runtime: explicitly rejected;
  evaluation owns semantic quality;
- Memory/context assembly: M2;
- broad 30-case runner, judge, and dashboard: M3.

## 14. Failure and uncertainty behavior

- malformed JSON, unknown fields, invalid enum/date types, missing required
  claim refs, or refs not visible in successful ToolResults fail closed as a
  normalized Team Decision result-contract error;
- ToolRuntime refusal or tool failure remains a runtime/tool error and is not
  rewritten as `insufficient_evidence`;
- successful empty search preserves the accepted M0.1 completed/no-fallback
  semantics and may project a `not_found` TeamDecisionResult;
- a model Final before any eligible evidence is rejected unless it is the
  validated successful-empty-search outcome;
- resume consumes the same durable Final text and deterministically re-parses
  the same contract; it does not invoke a second model or tool merely to rebuild
  the projection;
- missing owner/deadline remains null and must not cause a fabricated action;
- unresolved conflict yields explicit uncertainty rather than arbitrary
  source selection.

## 15. Acceptance criteria

### Level 1 — deterministic contract

- `TeamDecisionResult` strictly round-trips JSON-safe v1 data.
- Invalid status, date, unknown fields, malformed Final JSON, and unsupported
  claim refs fail closed.
- Required material claims have non-empty claim-level refs.
- Null owner/deadline and explicit `unassigned` remain distinct.
- Existing `RunRequest` and `AgentResult` data without M1.1 fields remains
  backward compatible.

### Level 2 — production-path controlled model

- A controlled model enters through `RuntimeEngine.start_multi_agent`, not a
  direct parser or isolated RetrievalAgent test.
- The first executable case is `mps-001` on a private synthetic fixture copy.
- The model chooses `search_notes`, chooses a discovered ref for
  `read_verified_note`, observes verified evidence, and returns Final JSON.
- The published result artifact contains a valid `TeamDecisionResult`; its
  decision claim ref is a ref actually visible before Final and validated by
  the existing evidence boundary.
- The prototype observation is not reported as adoption.
- No Python sequence hard-codes the trajectory, and ToolRuntime remains the
  only tool execution authority.
- Fresh and resume projection produce the same structured business payload
  without duplicate old calls.

### Level 3 — frozen Golden 8 evaluation

- The actual implementation is evaluated in the frozen manifest order without
  changing dataset or manifest content.
- The report includes task success, decision accuracy, rejected-alternative
  accuracy, unresolved-item recall, action-item accuracy, evidence
  groundedness, unsupported-claim rate, and trajectory violations, using N/A
  where the frozen case does not define a dimension.
- `mps-001` must pass the executable production-path acceptance before any
  broader result is considered.
- Results for the remaining seven cases must be reported honestly, including
  unsupported capability or contract gaps.
- Passing thresholds are `TO BE HUMAN-APPROVED BEFORE ACCEPTANCE`.

M1.1 cannot be called accepted merely because Level 1 unit tests pass. Gate D
cannot be called complete from the first `mps-001` increment alone.

## 16. Claim boundaries

Allowed after the first executable increment:

> The existing production Runtime can return one schema-validated,
> claim-grounded TeamDecisionResult for the frozen direct-decision target using
> a controlled model and verified local evidence.

Not allowed:

- “LinkLoom passes Golden 8” before Level 3 evidence;
- “all business claims are semantically correct” from schema validation alone;
- “multi-note context assembly is complete” while only the latest observation
  is model-visible;
- “the Runtime determines the correct decision”;
- “M1 or Gate D is complete” from `mps-001` alone;
- any writeback, HITL, Memory, vector-RAG, or production deployment claim.

## 17. Human decisions recorded

1. Extend the single persisted `RunRequest.workflow` allow-list with
   `team_decision`; do not add a second `result_type` selector.
2. Authorize the first Worker boundary as contract + `mps-001` only.
3. Defer Golden 8 metric thresholds and Level 3 evaluation until separately
   human-approved.

## 18. Planning references

- [Implementation plan](implementation_plan.md)
- [Planner task record](task.md)
- [Frozen Golden 8](../m1_team_decision_eval_seed/GOLDEN_8_FREEZE.md)
- [Accepted Eval Seed](../m1_team_decision_eval_seed/SPEC.md)

## 19. Learning reflection

The accepted M0 loop already owns model/tool durability. M1.1 does not need a
new Agent framework; it needs a business contract and a lossless final-result
projection. The first direct case can prove that seam. Broader Golden 8 cases
expose separate context/read/step-budget limits and must not be smuggled into
the first implementation batch.

`WORKER AUTHORIZED — CONTRACT + mps-001 PRODUCTION-PATH PROOF ONLY`
