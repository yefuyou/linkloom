# M1.1 Team Decision & Action Planner Record

Status: **APPROVED — FIRST WORKER BATCH**

`WORKER AUTHORIZED — CONTRACT + mps-001 PRODUCTION-PATH PROOF ONLY`

## 18. Worker implementation record — first batch

Status: **IMPLEMENTED — AWAITING INDEPENDENT REVIEW**

Implemented the approved first batch only:

- added the standalone `TeamDecisionResult` business contract, exact JSON
  parsing, nullable owner/deadline handling, and claim-to-observed-evidence
  validation;
- extended the one persisted `workflow` route with `team_decision`; no
  `result_type` was added;
- kept `RetrievalAgent` as a compatibility host only: it passes a durable Final
  plus observed ToolResult refs to the business contract and publishes the
  validated payload without assigning business meaning itself;
- preserved the existing RuntimeEngine → RuntimeAgentAdapter → Coordinator →
  RetrievalAgent → SingleAgentModelLoop → ToolRuntime composition;
- added controlled FakeModel production-path proof for synthetic `mps-001`:
  search → verified read → strict Final → TeamDecisionResult → durable result
  artifact;
- added an explicit fabricated evidence-ref failure test. The retrieval task
  fails closed, no Team Decision artifact is published, and the safe
  `TEAM_DECISION_CONTRACT_ERROR` is visible in `agent.task.failed` trace data.

RED evidence before implementation:

- missing Team Decision module: `3 failed` (`ModuleNotFoundError`);
- before the workflow/payload wiring: `2 failed, 18 passed` (unsupported
  `team_decision` workflow and absent AgentResult payload);
- production vertical slice initially failed at AgentTask construction because
  `agents.base` retained an obsolete second `ask/connect` allow-list. The
  correction imports Runtime's single `VALID_WORKFLOWS` source of truth.

GREEN evidence:

- contract/request/AgentResult focused tests: `23 passed`;
- M1.1 production vertical-slice tests: `2 passed`;
- M1.1 + M0.3/M0.4 focused production regression slice: `58 passed in
  71.89s`;
- final approved regression slice (also including existing Retrieval ToolRuntime
  and Golden 8 freeze tests): `71 passed in 83.07s`;
- `python -m compileall -q src/linkloom tests`: pass;
- `git diff --check`: pass (only existing checkout CRLF and protected-temp
  directory warnings).
- full `python -m pytest -q -p no:cacheprovider tests`: **not a clean
  environment-wide gate** — `522 passed, 1 skipped, 33 failed, 143 errors in
  372.79s`. The repeated setup error is an existing Windows ACL denial at
  the local pytest temporary directory; remaining failures are
  outside this Worker boundary in legacy relation/evaluation fixture paths.
  The approved M1.1 and M0 production-focused slices above pass.

No real Provider, credential, real Vault, Eval Seed, Golden 8 artifact,
multi-note context accumulation, step-budget expansion, Memory, writeback, UI,
MCP, vector retrieval, commit, or push was performed.

Learning points:

1. A single workflow allow-list avoids mismatched business routes at the
   RunRequest and AgentTask boundaries.
2. A structured model Final must be both schema-valid and grounded only in
   tool evidence observed by the model; schema validity alone is insufficient.

Interview evidence:

- `tests/integration/test_m11_team_decision_vertical_slice.py` proves a
  controlled model policy through the actual production composition and shows
  that fabricated claim evidence is rejected before result publication.

Remaining issues / next review focus:

- independent Reviewer must assess this Worker diff; this record is not an
  acceptance decision;
- Golden 8 / Gate D remain deferred and unclaimed;
- multi-note context, larger trajectories, stable multi-search evidence IDs,
  and broader business semantics require a separate Planner checkpoint.

## 18.1 Worker correction record — Reviewer blocking findings

Status: **CORRECTED — AWAITING INDEPENDENT RE-REVIEW**

This correction stays inside M1.1 Slice A.

1. **Decision contract coherence.** `approved` and `partial` now require a
   non-null decision value and direct decision refs. Only `not_found` and
   `insufficient_evidence` permit a null decision value. Evidence belonging to
   rationale, actions, or uncertainty cannot satisfy the decision claim.
2. **Outer failure propagation.** A `team_decision` retrieval contract failure
   now returns a failed coordinator result without fallback publication. A
   failed evidence review does the same. The durable failed state preserves the
   safe stable error code and the resulting artifact ref. Existing `ask` and
   `connect` fallback behavior is unchanged.
3. **Real frozen input proof.** The integration test copies only the frozen
   `model_provider_selection` workspace into its private test root, reads the
   actual `mps-001` question plus source/distractor metadata, and never passes
   any `expected_*` field to the FakeModel or production request. The test
   asserts original and copied workspace hashes remain unchanged.
4. **Cold resume proof.** A SQLite test checkpointer crashes only after a
   durable Team Decision Final. A new engine resumes from that record, does not
   call the replacement FakeModel or replay tool ledger entries, and publishes
   the exact same TeamDecisionResult as the fresh run. A separate fabricated
   durable Final is revalidated and fails closed during resume.

Additional production seam required by the real `mps-001` path: the Coordinator
now reserves two bookkeeping slots for Reviewer/final publication. This does
not increase the durable model-loop `max_steps` budget; it permits exactly the
existing three model turns (`search -> read -> Final`) to reach publication.

RED evidence:

- contradictory null material decisions: `2 failed, 8 passed`;
- new frozen-workspace integration suite before fixes: `4 failed` — valid
  three-turn path exhausted Coordinator bookkeeping budget, failed Team
  Decision runs had no result ref/stable outer error, and the required resume
  proof was absent.

GREEN evidence:

- correction contract + frozen mps-001 + failure + resume tests, M0.3, M0.4,
  ToolRuntime, and Golden 8 freeze: `78 passed in 17.99s`.

No Seed, Golden 8 manifest/workspace, real Provider, credential, Memory,
writeback, UI, MCP, vector retrieval, commit, or push was changed or used.

## 18.2 Worker Slice B record — `aer-002`

Status: **ACCEPTED — REVIEWER CYCLE 2**

The independently accepted Slice B adds only the bounded, durable
model-visible evidence needed for the real frozen multi-note rollout case.  It
does not claim broader Golden 8 semantics.

Completed work:

- the Team Decision model loop retains at most eight prior successful verified
  ToolResults as `ModelTurnRequest.evidence_context`, scoped to the active
  run/task/agent and bounded to 24 KiB;
- the `team_decision` model budget permits the required multi-note trajectory
  without changing unrelated workflow behavior;
- claim publication derives eligible refs from the hash-verified durable Final
  request (context plus current observation), not the full historical ledger;
- controlled `aer-002` model tests derive output from exact visible evidence
  values and fail when a required visible status class is removed;
- the real frozen private-copy path proves multi-note synthesis and cold
  resume, including no tool or replacement-model replay.

Actual Slice B files:

- `src/linkloom/agents/coordinator.py`, `registry.py`, and
  `runtime_adapter.py` — bounded Team Decision model budget wiring;
- `src/linkloom/runtime/model_loop.py` and `src/linkloom/agents/model_adapter.py`
  — persisted verified-evidence context;
- `src/linkloom/agents/retrieval_agent.py` — exact Final-request ref boundary;
- `src/linkloom/agents/providers/gemini_api.py` — provider projection of the
  bounded context;
- `tests/integration/test_m11_aer_002_vertical_slice.py` and
  `tests/unit/test_m11_verified_evidence_context.py` — real path, cap, and
  resume regressions;
- `REVIEW_GATE.md` — worker/reviewer durable handoff evidence.

Acceptance evidence:

- independent Reviewer Cycle 2 verdict: **ACCEPT**;
- frozen-path, focused vertical, `mps-001`, and Golden 8 freeze suite:
  `16 passed`;
- focused context/provider/durability suite: `87 passed`;
- M0.3/M0.4/ToolRuntime compatibility suite: `53 passed`;
- additional durable boundary suite: `106 passed`;
- compile and whitespace checks passed; frozen sources had no diff;
- no real vault, provider, credential, network call, writeback, UI, MCP,
  vector/embedding, memory framework, commit, or push was used.

Learning points:

1. A model can make a multi-note decision only from evidence actually exposed
   in its durable request; a runtime ledger alone is not a semantic input.
2. Grounding validation must use the exact Final request boundary, otherwise a
   historically observed but context-evicted ref can be incorrectly published.

Interview evidence:

- run `python -m pytest -q -p no:cacheprovider
  tests/integration/test_m11_aer_002_vertical_slice.py` to demonstrate
  model-visible multi-note synthesis, a context-cap failure, and cold resume;
- explain the distinction between runtime provenance checking and model-side
  semantic synthesis using the persisted Final request artifact.

Remaining issues / candidate next task:

- Slice C (`drm-003` + `inc-004`) remains a separate frozen-case audit for
  rejected alternatives, unresolved actions, owner/deadline/status, and their
  evidence boundaries;
- broader Golden 8 acceptance, provider evaluation, generic memory,
  multi-search ID redesign, and all later semantic slices remain deferred.

## 18.3 Worker Slice C record — `drm-003 + inc-004`

Status: **ACCEPTED — REVIEWER CYCLE 1**

Slice C required no production-runtime change: the accepted Slice B bounded
evidence context, Final-request grounding boundary, budget, and durability
already supported these two real frozen trajectories.  The Slice adds a
controlled, source-derived production-path proof rather than a new semantic
runtime rule.

Completed work:

- `drm-003` proves rejected-alternative reasoning from an independent audit
  reason, current-policy selection over discussion/draft evidence, and a
  blocked legal action with `owner=null` / `deadline=null` despite a proposed
  liaison;
- `inc-004` proves three distinct open actions: blocked with a dated owner,
  in progress with an owner and null deadline, and pending with an explicitly
  unassigned owner;
- the controlled model uses only public case fields, opaque refs found by the
  real search tool, and exact Final-visible values; expected fields are loaded
  solely after execution for assertions;
- evidence-removal regressions prove that an omitted rejection reason fails
  synthesis and an omitted in-progress value is not recreated from hidden
  controller state.

Actual Slice C files:

- `tests/integration/test_m11_slice_c_vertical_slice.py` — real private-copy
  production paths, source-derived semantic policy, evidence-removal checks,
  and frozen-result assertions;
- `REVIEW_GATE.md` — independent review handoff and Cycle 1 ACCEPT evidence.

No Slice C production file changed.

Acceptance evidence:

- independent Reviewer Cycle 1 verdict: **ACCEPT**;
- Slice C vertical suite: `4 passed`;
- Team Decision contract + Slice A/B/C + Golden freeze: `30 passed`;
- M0.3/M0.4/ToolRuntime compatibility: `53 passed`;
- compile and whitespace checks passed; protected frozen paths had no diff;
- no real vault, provider, credential, network call, writeback, UI, MCP,
  vector/embedding, generic memory, commit, or push was used.

Learning points:

1. Current decision selection is semantic work for the model: explicit current
   or selected evidence should outrank a discussion, comment, draft, or
   proposal without moving that judgment into the Runtime.
2. A repeated status mention is not automatically a separate action; model
   synthesis must preserve distinct action subjects while merging two status
   descriptions of the same visible work item.

Interview evidence:

- run `python -m pytest -q -p no:cacheprovider
  tests/integration/test_m11_slice_c_vertical_slice.py` to demonstrate
  rejected-alternative grounding, null-owner discipline, and evidence-removal
  behavior;
- explain why provenance validation checks reference visibility while the
  model, not Runtime Python, resolves authoritative versus discussion evidence.

Remaining issues / candidate next task:

- Slice D (`ret-005 + iti-005`) must separately audit stale/current
  precedence, proposed versus assigned ownership, null discipline, and
  calibrated uncertainty;
- Slice E, full Golden 8 evaluation, real-provider evaluation,
  multiple-search identity redesign, raw note reads, and generic memory remain
  deferred.

## 18.4 Worker Slice D record — `ret-005 + iti-005`

Status: **ACCEPTED — REVIEWER CYCLE 1**

Slice D added an independent, source-derived production-path characterization
of the already accepted Team Decision boundary.  It required no production
runtime change: bounded Final-visible evidence context, source-reference
validation, result durability, and the contract already support the two frozen
trajectories.

Completed work:

- `ret-005` proves a current approved hybrid boundary is selected over a
  superseded draft, and that an incomplete rollout produces a blocked action
  only when its status plus owner/date are visible in grounded evidence;
- `iti-005` proves a named suggestion is not promoted to an assigned owner,
  preserving a null `insufficient_evidence` decision, no invented action, and
  separately recorded pending and explicitly-unassigned unresolved work;
- the controlled model receives only public case inputs, opaque search refs,
  and the real Final-visible evidence context; frozen expected fields are used
  after execution solely for assertions;
- evidence-removal regressions prove missing supersession or
  explicitly-unassigned evidence cannot be reconstructed from hidden state.

Actual Slice D files:

- `tests/integration/test_m11_slice_d_vertical_slice.py` — real private-copy
  production paths, source-derived authority/assignment synthesis,
  durable-result assertions, and no-hidden-answer reductions;
- `REVIEW_GATE.md` — independent review handoff and Cycle 1 ACCEPT evidence.

No Slice D production file changed.

Acceptance evidence:

- independent Reviewer Cycle 1 verdict: **ACCEPT**;
- Slice D vertical suite: `4 passed`;
- Team Decision contract + accepted Slice A/B/C/D + Golden freeze:
  `34 passed`;
- M0.3/M0.4/ToolRuntime compatibility: `53 passed`;
- compile and whitespace checks passed; protected frozen paths had no diff;
- no real vault, provider, credential, network call, writeback, UI, MCP,
  vector/embedding, generic memory, commit, or push was used.

Learning points:

1. Authority is semantic: a model must use source language to distinguish a
   current approval from a stale draft instead of a Runtime timestamp rule.
2. A person mentioned in a proposal is not an owner; absent assignment and
   deadline evidence must remain explicit nulls, not guessed values.

Interview evidence:

- run `python -m pytest -q -p no:cacheprovider
  tests/integration/test_m11_slice_d_vertical_slice.py` to demonstrate
  stale/current selection, owner null discipline, and evidence-removal
  failure;
- explain why the Runtime validates provenance and serializes bounded context
  while the model alone resolves cross-note authority and assignment meaning.

Remaining issues / candidate next task:

- Slice E (`drm-002 + aer-005`) separately audits planned-versus-completed,
  pending-versus-rejected, insufficient evidence, and unsupported-claim
  control;
- final full Golden 8 evaluation, real-provider evaluation, multiple-search
  identity redesign, raw note reads, and generic memory remain deferred.

## 18.5 Worker Slice E record — `drm-002 + aer-005`

Status: **ACCEPTED — REVIEWER CYCLE 2**

Slice E adds an independent, source-derived production-path characterization
of the last two frozen cases.  It required no production-runtime change: the
accepted bounded Final-visible evidence context, source-reference validation,
durability, and strict contract already support both trajectories.

Completed work:

- `drm-002` proves the formal current split policy is selected over drafts and
  discussion, with two completed preparation checkpoints distinguished from
  an unauthorized deletion, plus separately grounded blocked and pending legal
  work with null owner/deadline discipline;
- `aer-005` proves a separately tracked scope is not silently treated as
  completed, retains explicitly unassigned ownership, and declares calibrated
  completion/owner/deadline uncertainty without a judge-sample shortcut;
- reviewer Cycle 1 found a reachable frozen target literal in the controlled
  model.  Cycle 2 replaced it with generic observed explicit-rejection and
  independent-rationale selection, and expanded the anti-leak scan over every
  helper reachable from the model decision path;
- evidence-removal regressions now prove missing preparation, separate-scope,
  or explicit-rejection evidence cannot recreate the final answer from hidden
  state.

Actual Slice E files:

- `tests/integration/test_m11_slice_e_vertical_slice.py` — real private-copy
  production paths, source-derived planned/completed and partial-scope
  synthesis, full reachable-controller anti-leak scan, and evidence-removal
  checks;
- `REVIEW_GATE.md` — Cycle 1 blocker, Cycle 2 correction, and independent
  ACCEPT evidence.

No Slice E production file changed.

Acceptance evidence:

- independent Reviewer Cycle 2 verdict: **ACCEPT**;
- Slice E vertical suite: `4 passed`;
- Team Decision contract + accepted Slice A/B/C/D/E + Golden freeze:
  `38 passed`;
- M0.3/M0.4/ToolRuntime compatibility: `53 passed`;
- compile and whitespace checks passed; protected frozen paths had no diff;
- no real vault, provider, credential, network call, writeback, UI, MCP,
  vector/embedding, generic memory, commit, or push was used.

Learning points:

1. A passing vertical test is not enough: a controlled model must not contain
   any reachable frozen-answer fragment, even if the value is only used for
   evidence selection rather than emitted verbatim.
2. “Prepared” and “authorized/completed” are separate semantic claims.  They
   need separate visible evidence so the result cannot turn a dry run into a
   completed real operation.

Interview evidence:

- run `python -m pytest -q -p no:cacheprovider
  tests/integration/test_m11_slice_e_vertical_slice.py` to demonstrate
  anti-Gold control, planned-versus-completed distinction, and partial
  uncertainty;
- explain how a reviewer caught a hidden-answer shortcut after a passing test,
  and how a transitive helper-source audit plus evidence-removal regression
  repaired it without moving semantic judgment into Runtime code.

Remaining issues / candidate next task:

- final full Golden 8 production evaluation, using only the accepted frozen
  cases and model-visible evidence, must report its exact coverage and leave
  real-provider evaluation unclaimed;
- real-provider evaluation, multiple-search identity redesign, raw note reads,
  and generic memory remain deferred.

## 18.6 Worker Final Gate record — Golden 8 production evaluation

Status: **ACCEPTED — REVIEWER CYCLE 1**

Completed work:

- executed the exact eight frozen synthetic cases in canonical manifest order
  through the accepted production path, using private copies of their real
  synthetic workspaces and Final-visible verified evidence only;
- recorded the outcome as execution evidence rather than inventing a Level-3
  aggregate quality threshold: all eight primary paths and their case-specific
  decision/action/unresolved/evidence assertions passed;
- obtained an independent Reviewer Final Gate acceptance after separate source,
  trajectory, freeze, regression, compatibility, compile, and diff checks.

Actual Final Gate files:

- `REVIEW_GATE.md` — Worker execution report plus independent Reviewer Cycle 1
  verdict, evidence, boundary, and deferred scope;
- `task.md` — this accepted progress record.

No Final Gate production or test file changed.

Acceptance evidence:

- independent Reviewer Cycle 1 verdict: **ACCEPT**;
- manifest-order Golden 8 primary production command: `8 passed`;
- Git/LF Golden freeze: `1 passed`;
- complete accepted-slice suite plus freeze: `23 passed`;
- M0.3/M0.4/ToolRuntime compatibility: `53 passed`;
- compile and whitespace checks passed; protected frozen sources had no diff;
- no real vault, provider, credential, network call, writeback, UI, MCP,
  vector/embedding, generic memory, commit, or push was used.

Metric boundary:

- task success is `8/8`; the three cases with expected rejected alternatives
  each passed their case assertions; asserted trajectory violations were `0`;
- unsupported-claim rate and any normalized aggregate quality score remain
  **UNAVAILABLE** because no approved scorer or Level-3 threshold exists;
- this is not real-provider, broad-30-case, or production-vault acceptance.

Learning points:

1. An evaluation result is only as strong as its declared metric and threshold:
   fixture assertions can prove specified behavior without proving general model
   quality.
2. Evidence provenance is a product boundary: every final reference must come
   from a successful, Final-visible verified ToolResult, rather than an
   evaluator's hidden expected answer.

Interview evidence:

- run `python -m pytest -q -p no:cacheprovider
  tests/integration/test_m11_team_decision_vertical_slice.py::test_frozen_mps_001_production_path_persists_current_decision_from_real_workspace
  tests/integration/test_m11_aer_002_vertical_slice.py::test_aer_002_synthesizes_multiple_verified_notes_visible_to_the_final_model_turn
  tests/integration/test_m11_slice_c_vertical_slice.py::test_slice_c_frozen_cases_synthesize_rejection_and_open_actions_from_visible_evidence
  tests/integration/test_m11_slice_d_vertical_slice.py::test_slice_d_frozen_cases_preserve_current_authority_and_insufficient_assignment
  tests/integration/test_m11_slice_e_vertical_slice.py::test_slice_e_frozen_cases_preserve_preparation_and_partial_scope_boundaries`
  to reproduce all eight bounded production paths;
- explain why the report distinguishes an executed 8/8 fixture gate from a
  human-approved quality threshold or real-provider claim.

Remaining issues / candidate next task:

- any Level-3 aggregate metric, real-provider or broad-30-case evaluation,
  multiple-search identity redesign, raw note reads, generic memory, and all
  real-vault work require an explicit later SPEC/approval; none is started.

## 1. Planner role and authority

Role: Planner only.

This record reconstructs current production behavior and freezes a proposed
Worker boundary. It does not approve the plan for the human, implement code,
change tests, accept M1.1, complete Gate D, or authorize a Provider call.

Governing artifacts:

- repository `SPEC.md`, `DEV_SPEC.md`, and `AGENTS.md`;
- `docs/PRODUCT_ROADMAP.md`;
- `docs/requirements/linkloom-master/MASTER_SPEC_V2.md`;
- [M1.1 SPEC](SPEC.md);
- [M1.1 implementation plan](implementation_plan.md);
- accepted Team Decision Eval Seed and formally frozen Golden 8.

## 2. Synchronized repository baseline

- fetched `origin` on `2026-09-11`;
- local `master` was a strict ancestor of `origin/master` and was
  fast-forwarded without checkout reset/rebase/stash/clean;
- current branch after safe synchronization: `master`;
- current master SHA:
  `54ca4a5b03d8c3e7753f0b2e2d9cd0125923db20`;
- master contains `Merge pull request #2 from yefuyou/docs/golden8-freeze`;
- Golden 8 source commits are present (`8fa02ad`, `f2dc7ca`).

Pre-existing unrelated working paths were preserved:

- modified `README.md`;
- modified generated `src/linkloom.egg-info/SOURCES.txt`;
- untracked `.tmp/`, `assets/`, `src/test.md`, and `tasks/`.

No reset, restore, clean, stash, amend, rebase, force push, commit, or push was
performed.

## 3. Accepted capability baseline

```text
M0.1 = ACCEPTED
M0.2 = ACCEPTED
M0.3 = ACCEPTED
M0.4 = ACCEPTED
Gate A = COMPLETE
Gate B = COMPLETE
M0 = COMPLETE
```

Evaluation baseline:

- Team Decision Eval Seed: `ACCEPTED AS EVAL SEED`;
- 30 cases, six synthetic workspaces, 36 notes;
- canonical Git/LF SHA-256:
  `49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`;
- Golden 8 status: `FORMALLY FROZEN`;
- ordered IDs: `mps-001`, `aer-002`, `drm-003`, `inc-004`,
  `ret-005`, `iti-005`, `drm-002`, `aer-005`.

The freeze proves evaluation identity only. It does not imply the current
Agent passes the cases.

## 4. Current production execution trace

```text
User query
  -> RuntimeEngine.start_multi_agent
  -> RuntimeAgentAdapter.run
  -> Coordinator.run
  -> RetrievalAgent.execute
  -> SingleAgentModelLoop.run / resume
  -> ModelTurnRequest
  -> model chooses search_notes or read_verified_note
  -> ToolRuntime validates and executes
  -> ToolResult becomes the next observation
  -> model chooses another tool or Final
  -> ModelLoopResult.final_answer
  -> RetrievalAgent._project_result
  -> AgentResult
  -> Coordinator review/result projection
  -> RuntimeEngine._finish_multi_agent
  -> durable result.json + RuntimeState.result_ref
```

### 4.1 Stage-by-stage findings

| Stage | Decision owner | Data in | Data out | Business semantics preserved | Business semantics lost or unavailable |
|---|---|---|---|---|---|
| User / `RunRequest` | Caller; Runtime validates envelope | query, `ask/connect`, budgets, dry-run | persisted request artifact and run identity | exact query, limits, source selection | no result-contract selector; Team Decision intent is not explicit |
| `RuntimeEngine.start_multi_agent` | Runtime | request, source paths, injected model | verified source context, RuntimeState, adapter call | query, source hash/fingerprint, budgets, read-only mode | no business-result type propagated |
| `RuntimeAgentAdapter` | Runtime composition | query, source context, state, model, ledger | Coordinator with verified tool callbacks | VaultReader freshness, hash-backed evidence, ToolRuntime composition | injected Memory is returned as refs after execution, not model-visible; deferred from M1.1 |
| `Coordinator` | Python owns task routing; model does not | workflow/query/source context | RetrievalAgent task and later Reviewer task | allowed retrieval capabilities, aggregate evidence refs, failure/fallback state | creates retrieval task with `max_steps=3`; drops RetrievalAgent business Final because none is projected |
| `RetrievalAgent` instruction | Python defines safe task; model owns next action | query + serialized source context | generic retrieval instruction | no Gold/private path, bounded JSON request, model-driven stopping | no decided/mentioned, proposed/assigned, stale/current, uncertainty, or output schema instruction |
| `SingleAgentModelLoop` | Model proposes; Runtime controls boundary | instruction, tools, latest observation, previous call | durable ModelResponse/ToolResult sequence and Final text | real model ownership of search/read/stop; checkpoints and resume | only latest observation enters each next ModelTurnRequest; accumulated evidence is not assembled |
| `search_notes` | Model chooses arguments; Runtime executes | query, source context, limit | list of verified line-level EvidenceRef objects | relative path, content hash, line, quote/hash, status | lexical result may be incomplete for a multi-claim answer; evidence IDs restart per search |
| `ToolRuntime` | Runtime | model ToolCall + policy | validated durable ToolResult or safe error | exact arguments, allow-list, budgets, ledger, checkpoint, no local provider execution | intentionally owns no business semantics |
| `read_verified_note` | Model chooses ref; Runtime verifies | one discovered ref | current selected EvidenceRef, or minimal verified note identity | current source/hash/ref verification | for a search evidence ref it returns the same line-level evidence, not all relevant note sections |
| Model Final | Model | instruction + latest observation | durable free-text `final_answer` | model owns synthesis and termination | no enforced TeamDecisionResult structure or claim-level ref contract |
| `RetrievalAgent._project_result` | Python projection | durable state, ledger, Final status | `AgentResult(output_type="evidence_bundle")` | empty-search and explicit search/read failure semantics; verified output refs | discards `ModelLoopResult.final_answer`; no decision/rationale/actions/uncertainty payload |
| Reviewer | Runtime/Reviewer | aggregate output refs and placeholder subject payloads | evidence/schema decision | current evidence refs are revalidated | does not inspect claim-linked Team Decision fields |
| Coordinator final projection | Python projection | AgentResult + review | result dict | aggregate evidence, task/ledger/review metadata | no business answer survives |
| Runtime finalization | Runtime | Coordinator result | durable result artifact and final state | generic JSON artifact, trace, result ref, source immutability | cannot recover fields that Coordinator never supplied |

## 5. What is directly reusable

No replacement is needed for:

- model-selected trajectory and termination;
- provider-neutral `ModelTurnRequest -> ModelResponse` boundary;
- Gemini ToolCall/Final normalization;
- ToolRuntime tool schemas and local execution authority;
- policy, ledger, durable artifacts, checkpoints, and fail-closed resume;
- verified VaultReader source checks;
- search EvidenceRef content/hash/quote identity;
- Reviewer aggregate evidence validation;
- generic result artifact persistence.

The first M1.1 Worker should build on these seams and leave them structurally
unchanged.

## 6. Frozen Golden 8 capability matrix

This matrix is Planner/evaluation information. It must not enter production
prompts or model requests.

| ID | Scenario | Question intent | Decision/rejection/action semantics | Temporal or missing-data rule | Evidence boundary | Preferred; acceptable |
|---|---|---|---|---|---|---|
| `mps-001` | direct decision | identify approved provider | approved decision; no required rejection/action inventory | prototype mention is not adoption | authoritative decision evidence; rationale when returned | `S-R-F`; `S-F` only with complete search content |
| `aer-002` | multi-note | current rollout plus three open areas | deterministic gate current; reporting in progress; multilingual unassigned; threshold inactive | proposal is not active or assigned | decision, owner/status, threshold current/proposal/blocker | `S-4R-F`; conditional `S-3R-F` |
| `drm-003` | rejection rationale | why 90-day policy rejected and current legal boundary | split policy current; 90-day rejected for audit/archive reason; legal sign-off blocked | stale proposal cannot override approved policy | review finding, decision/rejection, legal status/action | `S-4R-F`; conditional `S-3R-F` |
| `inc-004` | unresolved actions | list open remediation, owners, dates, status | three exact open items; one blocked, one in progress, one unassigned | planned/unassigned is not completed/owned | decision and open-action status | `S-2R-F`; conditional `S-R-F` |
| `ret-005` | stale/current | select Feb 3 draft or Feb 14 decision; rollout state | hybrid current, pure-semantic superseded, rollout incomplete, security action blocked | authority/status plus date; newer informational evidence does not automatically win | supersession, final decision, readiness/blocker | `S-4R-F`; conditional `S-3R-F` |
| `iti-005` | insufficient assignment | owner and deadlines for checksum/retention | checksum owner/deadline unknown; retention explicitly unassigned | suggested owner is not assigned; missing date stays null | ownership and open-action boundary | `S-2R-F`; conditional `S-R-F` |
| `drm-002` | plan vs completion | why split policy; completed vs blocked checkpoints | preparation complete; deletion authorization blocked; exception list pending | preparation is not authorization; no invented owners | review, decision, completed/remaining work, legal boundary | `S-4R-F`; conditional `S-3R-F` |
| `aer-005` | partial/insufficient | whether multilingual slice was included/completed | separate, unassigned slice; completion not established | no future owner/deadline/coverage invention | rollout scope and ownership status | `S-2R-F`; conditional `S-R-F` |

Legend: `S = search_notes`, `R = read_verified_note`, `F = Final`.

`mps-001` is the first executable target. Frozen order remains unchanged for
evaluation; the proposed implementation order may differ only to stage risk.

## 7. Exact demonstrated gaps and classification

| Gap | Evidence in current code | Classification |
|---|---|---|
| No explicit Team Decision result selection | `RunRequest` has workflow only; `ask` currently means evidence-bundle behavior | **BLOCKING M1.1 first increment** |
| No TeamDecisionResult contract | no production dataclass/schema exists | **BLOCKING M1.1 first increment** |
| Generic instruction only | `RetrievalAgent._retrieval_instruction()` asks only to retrieve sufficient evidence | **BLOCKING M1.1 first increment** |
| Free-text Final discarded | `ModelLoopResult.final_answer` exists but `_project_result()` never uses it | **BLOCKING M1.1 first increment** |
| AgentResult has no payload | only output type/refs/summary fields exist | **BLOCKING M1.1 first increment** |
| Coordinator loses business result | final result contains evidence/candidates/review only | **BLOCKING M1.1 first increment** |
| No per-claim visible-ref validation | Reviewer validates aggregate refs and placeholder subject payloads | **BLOCKING M1.1 first increment** |
| Only latest observation is model-visible | durable request construction supplies one `observation` and one `previous_tool_call` | **BLOCKING broader Golden 8; DEFER first increment** |
| Verified read is line-level for search refs | adapter returns the selected EvidenceRef instead of a bounded note/section bundle | **BLOCKING broader multi-claim/multi-note expansion; DEFER first increment** |
| Retrieval task step budget is 3 | Coordinator default and registry max are 3; several frozen traces require more turns | **BLOCKING broader Golden 8; DEFER first increment** |
| Search evidence IDs can collide | each retrieval call begins `ev_p1_0001`; adapter map uses update | **BLOCKING multiple-search reliability; DEFER first increment** |
| Provider-native output schema absent | Gemini accepts text Final | NICE TO HAVE; local strict parser is sufficient baseline |
| Confidence calibration absent | AgentResult confidence remains null | DEFER; no frozen probability calibration exists |

## 8. Planning decisions

1. The center business object is `TeamDecisionResult`, not a new retrieval
   abstraction.
2. Extend the single persisted workflow allow-list with
   `workflow="team_decision"`; do not add `result_type` as a second business
   selection mechanism. Existing `ask` and `connect` behavior remains intact.
3. Keep `ModelAction.final_answer` as provider-neutral text. Require exact JSON
   and validate it locally; do not change ModelAction, model loop, or Gemini.
4. Add optional `AgentResult.output_payload` and propagate it losslessly.
5. Runtime validates structure and evidence identity/visibility. The model
   decides business meaning. Evaluation checks semantic correctness.
6. M1.1 has no suggestion field. `actions` and `unresolved_items` are recorded,
   evidence-derived facts only.
7. `mps-001` is the first and only mandatory executable case for the initial
   Worker boundary.
8. Broader Golden 8 gaps require a new post-`mps-001` Planner checkpoint.

## 9. Proposed first Worker production boundary

Only these production files:

1. `src/linkloom/agents/team_decision.py` — new structured business contract;
2. `src/linkloom/runtime/models.py` — additive persisted workflow allow-list;
3. `src/linkloom/agents/base.py` — optional structured AgentResult payload;
4. `src/linkloom/runtime/graph.py` — multi-agent workflow allow-list plus
   fresh/resume workflow wiring;
5. `src/linkloom/agents/registry.py` — existing agent workflow-capability
   allow-list update;
6. `src/linkloom/agents/runtime_adapter.py` — RetrievalAgent/workflow wiring
   and durable instruction identity;
6. `src/linkloom/agents/retrieval_agent.py` — Gold-free instruction, strict
   parse, visible-ref checks, projection;
7. `src/linkloom/agents/coordinator.py` — validated payload publication.

No other production file is approved by this Planner draft.

## 10. Proposed first Worker test boundary

1. `tests/unit/test_team_decision_contract.py` — new contract tests;
2. `tests/unit/test_runtime_models.py` — additive request-type tests;
3. `tests/unit/test_agent_contracts.py` — additive payload tests;
4. `tests/integration/test_m11_team_decision_vertical_slice.py` — new
   `mps-001` production-path tests.

Existing M0 and Golden freeze tests are regressions and remain unchanged.
`tests/eval/test_m11_golden8_acceptance.py` is a candidate later file only,
after a separate expansion/threshold approval.

## 11. First RED matrix

| Order | RED assertion | Initial target |
|---:|---|---|
| 1 | strict minimal TeamDecisionResult parses and round-trips | contract |
| 2 | invalid enum/date/unknown field/missing claim ref fails | contract |
| 3 | `team_decision` workflow persists safely; `ask`/`connect` remain compatible | request boundary |
| 4 | AgentResult carries JSON-safe structured payload without breaking legacy | projection boundary |
| 5 | production path currently cannot publish valid Team Decision Final | production characterization |
| 6 | `mps-001` runs `search -> read -> final` under model policy | first executable target |
| 7 | model tool args arrive unchanged at ToolRuntime | M0 ownership preserved |
| 8 | fabricated/unseen claim ref fails closed | claim grounding |
| 9 | premature Final without evidence fails | evidence discipline |
| 10 | malformed Team Decision Final fails safely | schema boundary |
| 11 | durable Final resume reproduces the payload without old-call replay | M0 durability reuse |
| 12 | successful empty search preserves no-fallback semantics | M0.1 compatibility |

After this checkpoint, separately approved RED cases map to rejection
(`drm-003`), actions (`inc-004`), missing fields (`iti-005`), completion
(`drm-002`), stale/current (`ret-005`), multi-note synthesis (`aer-002`), and
partial/insufficient evidence (`aer-005`).

## 12. Acceptance ladder

### Level 1 — deterministic contract

Strict schema, nullable-field, claim-ref, request, and AgentResult tests pass
offline.

### Level 2 — production-path controlled model

The actual Runtime/Adapter/Coordinator/RetrievalAgent/ToolRuntime path produces
a structured, claim-grounded `mps-001` result and preserves it across the
durable result artifact and resume boundary.

### Level 3 — Golden 8 evaluation

The actual implementation is run against all eight frozen cases in frozen
order. Report task success, decision accuracy, rejected-alternative accuracy,
unresolved recall, action accuracy, evidence groundedness, unsupported claim
rate, and trajectory violations. Unsupported cases remain visible.

Passing thresholds: `TO BE HUMAN-APPROVED BEFORE ACCEPTANCE`.

Level 1 alone cannot accept M1.1. The first `mps-001` increment alone cannot
complete Gate D.

## 13. Deferred work

### Post-`mps-001` M1.1 expansion checkpoint

- accumulated verified-evidence context;
- bounded verified note/section reads;
- larger but bounded retrieval model-step allowance;
- stable non-colliding evidence identity across multiple searches;
- remaining Golden 8 business-semantic increments.

### M1.2 or later

- proposed writeback, task creation, approval, execution, and HITL.

### M2

- Memory/context assembly, hybrid retrieval, embeddings/vector retrieval, and
  cross-session context strategy.

### M3

- broad 30-case automated quality program, optional judge, dashboard, and
  long-term regression analysis.

## 14. Unresolved human decisions

1. Approved: add `team_decision` to the single `RunRequest.workflow`
   allow-list; do not add `result_type`.
2. Approved: first Worker authorization is limited to contract + `mps-001`.
3. Deferred: exact Level 3 metric thresholds remain human-approved before
   broader evaluation acceptance.

## 15. Planning artifact scope

Created by this Planner:

- `docs/requirements/m1_1_team_decision_action/SPEC.md`;
- `docs/requirements/m1_1_team_decision_action/implementation_plan.md`;
- `docs/requirements/m1_1_team_decision_action/task.md`.

No production code, tests, dataset, Golden 8 manifest, workspace notes, or
freeze artifact was intentionally modified.

## 16. Verification record

Final Planner verification:

- `git diff --check`: **PASS** (exit `0`); Git emitted LF-to-CRLF
  warnings only for the pre-existing user modifications in `README.md` and
  `src/linkloom.egg-info/SOURCES.txt`;
- `python -m pytest -q -p no:cacheprovider
  tests/eval/test_golden8_freeze.py`: **PASS**, `1 passed in 1.27s`; the only
  warning was the environment's existing `pytest-asyncio` fixture-scope
  deprecation warning;
- planning-only changed-path audit: **PASS**; exactly the three planning files
  in this directory are new for M1.1;
- protected path audit: **PASS**; no production file under `src/linkloom`, no
  test, and no Eval Seed or Golden 8 artifact was modified by this Planner;
- dataset/manifest/freeze-test path audit: **PASS**, empty status output;
- planning-file whitespace/final-newline audit: **PASS**;
- credential/secret-value scan of the three planning files: **PASS**, no value
  matched;
- provider calls and credential access: **NOT PERFORMED**.

The protected-path status check emitted an access warning for the pre-existing
`tests/.pytest-trajectory-2b-data/` directory. It did not report a changed
protected file and did not affect the frozen-test result.

## 17. Learning reflection

### Step

- Role: Planner
- Feature: M1.1 Team Decision & Action
- Files reviewed: production Runtime, agent, model, tool, evidence, tests,
  accepted seed, and Golden 8 freeze artifacts.

### What changed

Three planning artifacts define the first business-result seam and a staged
RED-first implementation boundary. No business implementation changed.

### What was learned

1. The model already owns the production retrieval trajectory, but its durable
   Final is discarded before the result artifact.
2. A direct case can prove the structured-result seam, while multi-note Golden
   cases expose separate accumulated-context, read-granularity, step-budget,
   and evidence-identity limits.

### Evidence

- current master and merge history;
- direct source inspection of RuntimeEngine, RuntimeAgentAdapter, Coordinator,
  RetrievalAgent, SingleAgentModelLoop, model contracts, ToolRuntime, tools,
  schemas, and focused tests;
- exact frozen Golden 8 records read from the accepted dataset;
- verification commands recorded below after execution.

### Next step

Human reviews the recommended request/contract/file boundary. Only after
explicit approval may a separate Worker write the first RED tests.

`WORKER AUTHORIZED — CONTRACT + mps-001 PRODUCTION-PATH PROOF ONLY`
