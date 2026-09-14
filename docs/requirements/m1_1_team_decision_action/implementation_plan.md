# M1.1 Team Decision & Action Implementation Plan

Status: **APPROVED — FIRST WORKER BATCH**

`WORKER AUTHORIZED — CONTRACT + mps-001 PRODUCTION-PATH PROOF ONLY`

## 1. Objective

Deliver the smallest production vertical slice from a natural-language team
decision question through the accepted model-driven Runtime and verified local
evidence to a structured `TeamDecisionResult`.

The first executable target is frozen case `mps-001`. The first Worker must not
attempt all Golden 8 cases, change retrieval quality, or redesign accepted M0
infrastructure.

## 2. Frozen implementation boundaries

The following are directly reused and must remain unchanged in the first
Worker increment:

- `SingleAgentModelLoop` control flow and durable lifecycle;
- `ModelTurnRequest`, `ModelResponse`, and `ModelAction` action kinds;
- Gemini and all Provider adapters;
- `ToolRuntime`, tool schemas, permissions, ledger, and checkpoint callbacks;
- checkpoint stores, recovery decisions, and resume semantics;
- lexical retrieval ranking and EvidenceRef generation;
- Memory, Curator, Reviewer architecture, writeback, HITL, UI, and CLI;
- the 30-case dataset, 36 notes, Golden 8 manifest, order, labels, answers,
  evidence refs, expectations, and freeze tests.

If the Worker concludes that one of these frozen surfaces must change for
`mps-001`, it must stop and return a concrete blocker to a new Planner. It must
not silently widen the approved file list.

## 3. Recommended minimal design

### 3.1 Request selection

Extend the existing persisted `RunRequest.workflow` allow-list:

```text
ask             (existing)
connect         (existing)
team_decision   (explicit M1.1 business boundary)
```

This is required because the existing `ask` workflow is also used by accepted
M0 evidence-bundle tests. A distinct workflow avoids changing generic ask
behavior and survives resume through the existing persisted request artifact.
No `result_type` is added: it would duplicate business routing and could
conflict with `workflow` without evidence that the concepts are orthogonal.

### 3.2 Final representation

Keep the existing provider-neutral Final action:

```text
ModelAction(kind="final", final_answer=<text>)
```

For `workflow = team_decision`, the text must be exactly one JSON object
matching `team-decision-result/v1`. Local production code parses and validates
it after the durable loop accepts Final. No provider-specific response schema
and no second model loop are added.

### 3.3 Structured projection

Add `AgentResult.output_payload: dict | None` as an optional, JSON-safe field.
For M1.1 it contains the validated `TeamDecisionResult`; existing result types
retain `None` and existing behavior. `Coordinator` propagates this payload into
the nested final result and passes its top-level evidence refs through the
existing review/evidence path.

### 3.4 Claim grounding

The model chooses semantic claims and their refs. Deterministic production code
only:

1. parses the contract;
2. collects all nested claim refs;
3. verifies the top-level ref union;
4. verifies every ref occurred in successful, verified, model-visible tool
   output for the active run/task/agent;
5. lets the existing evidence validator recheck current source/hash/quote
   identity.

It does not decide which provider was approved or whether one note supersedes
another.

## 4. Exact proposed production files — first Worker increment

| File | Change | Why M1.1 requires it |
|---|---|---|
| `src/linkloom/agents/team_decision.py` | **Additive new file** | Own the strict provider-neutral `TeamDecisionResult` v1 dataclasses/parser, enum/date/unknown-field checks, nested claim-ref collection, and JSON-safe serialization. Existing contracts cannot represent the business object. |
| `src/linkloom/runtime/models.py` | **Additive contract change** | Extend the persisted `RunRequest.workflow` allow-list with `team_decision`; existing serialized `ask` and `connect` requests remain compatible. No second business selector is introduced. |
| `src/linkloom/agents/base.py` | **Additive contract change** | Add optional `AgentResult.output_payload`; current fields cannot carry a structured result and `summary` must not become JSON storage. Positional compatibility must be preserved by adding the defaulted field last. |
| `src/linkloom/runtime/graph.py` | **Minimal wiring** | Extend the existing `start_multi_agent` workflow allow-list with `team_decision` and reuse persisted workflow on fresh and resume paths; do not redesign result finalization. |
| `src/linkloom/agents/registry.py` | **Minimal capability update** | Allow coordinator, retrieval, and reviewer identities to receive `team_decision`; Curator remains connect-only. This reuses existing workflow-capability enforcement rather than adding a permission system. |
| `src/linkloom/agents/runtime_adapter.py` | **Minimal wiring** | Route `workflow="team_decision"` into the compatibility composition and include it in existing durable instruction identity. Tool callbacks and evidence execution remain unchanged. |
| `src/linkloom/agents/retrieval_agent.py` | **Behavioral M1.1 compatibility seam** | Build the Gold-free Team Decision instruction, parse the durable Final, validate contract/visible refs, preserve M0 failure and empty-search semantics, and project `output_type`, `output_payload`, and refs. This local reuse must not make decision/rejection/action semantics intrinsic RetrievalAgent responsibilities. Stop for Planner review if isolation requires broad refactoring. |
| `src/linkloom/agents/coordinator.py` | **Minimal projection** | Propagate the already-validated payload to the result artifact and supply claim refs to existing evidence review. Coordinator must not synthesize or rewrite business fields. |

No first-increment production change is proposed for:

- `src/linkloom/runtime/model_loop.py`;
- `src/linkloom/agents/model_adapter.py`;
- `src/linkloom/agents/providers/gemini_api.py`;
- `src/linkloom/tools/runtime.py`;
- `src/linkloom/tools/read_tools.py`;
- `src/linkloom/retrieval.py`;
- checkpoint/recovery/Memory/Reviewer/Curator modules.

## 5. Exact proposed test files — first Worker increment

| File | Change | Responsibility |
|---|---|---|
| `tests/unit/test_team_decision_contract.py` | New | Strict v1 schema, status vocabularies, null/unassigned distinction, dates, unknown fields, nested refs, top-level union, malformed payloads |
| `tests/unit/test_runtime_models.py` | Minimal additive tests | `RunRequest.workflow="team_decision"` round-trip, invalid workflow rejection, and persisted `ask`/`connect` compatibility |
| `tests/unit/test_agent_contracts.py` | Minimal additive tests | `AgentResult.output_payload` JSON safety, round-trip, existing positional/default behavior |
| `tests/integration/test_m11_team_decision_vertical_slice.py` | New | Real `RuntimeEngine.start_multi_agent` production composition, `mps-001`, controlled model trajectory, claim-level refs, result artifact, failure and resume projection |

The following existing tests are regressions only and must not be edited in the
first Worker increment:

- `tests/integration/test_m03_model_driven_retrieval.py`;
- `tests/integration/test_m04_production_e2e_resume.py`;
- `tests/integration/test_retrieval_tool_runtime.py`;
- `tests/eval/test_golden8_freeze.py`.

Candidate later evaluation file, not authorized in the first increment:

- `tests/eval/test_m11_golden8_acceptance.py` — bounded manifest-order
  evaluation adapter and metric report after the metric thresholds and broader
  context boundary receive separate human approval.

## 6. RED-first work packages

### W0 — Baseline and ownership guard

Before editing production code:

1. capture `git status --short` and preserve unrelated paths;
2. run the four existing M0 regression files listed above;
3. run the Golden 8 freeze test;
4. verify dataset and manifest have no diff;
5. create no Provider client and access no credential.

Stop if the accepted production baseline is not reproducible for reasons
inside the proposed M1.1 file boundary.

### W1 — TeamDecisionResult contract RED

Write `tests/unit/test_team_decision_contract.py` first. Required RED cases:

1. valid minimal direct-decision result round-trips;
2. decision status outside the frozen small vocabulary fails;
3. action status outside the frozen small vocabulary fails;
4. missing owner/deadline remains null;
5. explicit `unassigned` remains distinct from null;
6. invalid deadline format fails;
7. a material claim with no evidence refs fails;
8. nested refs not represented in the top-level union fail;
9. unknown fields, including Gold/expected/mutation/suggestion fields, fail;
10. malformed or non-object JSON fails.

Implement only the contract file needed to turn W1 GREEN.

Reviewer checkpoint R1: confirm the schema expresses business facts without
encoding Golden answers and without introducing a deterministic extractor.

### W2 — Request and AgentResult contracts RED

Add RED tests to the existing contract test files:

- `workflow="team_decision"` survives `RunRequest.to_dict()/from_dict()`;
- invalid workflow fails closed;
- existing `ask` and `connect` round-trips remain unchanged;
- existing request payloads still load;
- `AgentResult.output_payload` accepts only JSON-safe data;
- existing AgentResult constructors and evidence-bundle serialization remain
  compatible.

Then make only the additive `runtime/models.py` and `agents/base.py` changes.

### W3 — RetrievalAgent business projection RED

In the new integration test, first prove current behavior is RED:

- a controlled model can return valid Team Decision JSON, but production drops
  it or cannot select the contract;
- a model Final containing a fabricated/unseen claim ref must not produce a
  completed Team Decision result;
- a malformed business Final must fail with a safe normalized contract error;
- a premature Final without eligible evidence must fail;
- successful empty search remains completed/no-fallback and produces a
  not-found business result only when explicitly requested.

Then implement the team-decision workflow instruction, strict parsing, visible-ref
membership validation, and AgentResult projection in `RetrievalAgent`.

The instruction may contain:

- the TeamDecisionResult field/schema vocabulary;
- semantic rules from the approved SPEC;
- the user's query and bounded source context;
- a requirement to cite only tool-observed verified evidence.

The instruction must not contain:

- `mps-001` or any other case ID;
- expected provider name, expected answer, required/forbidden claims, Gold
  evidence paths, labels, metric targets, or expected trajectory;
- a fixed tool sequence.

### W4 — Production wiring and result publication RED

Add one production-composition RED test proving the request enters through
`RuntimeEngine.start_multi_agent` and the validated payload must survive:

```text
RunRequest
  -> RuntimeEngine
  -> RuntimeAgentAdapter
  -> Coordinator
  -> RetrievalAgent
  -> result artifact
```

Then apply only the approved wiring changes to `graph.py`,
`runtime_adapter.py`, and `coordinator.py`.

Reviewer checkpoint R2: inspect the production trace and confirm the existing
loop, ToolRuntime, provider adapter, ledger, checkpoint, and recovery files did
not change.

### W5 — `mps-001` executable acceptance RED/GREEN

Use a private test copy of the accepted synthetic
`model_provider_selection` workspace. Do not point production code at the Gold
dataset and do not put expected outputs into the production instruction.

Controlled model trajectory:

1. receive the natural-language `mps-001` question through RunRequest;
2. choose `search_notes` arguments;
3. observe actual search evidence;
4. choose one discovered ref for `read_verified_note`;
5. observe the verified evidence record;
6. return a TeamDecisionResult Final using only the observed evidence ref;
7. let Runtime validate and publish it.

Required assertions:

- trajectory is `search_notes -> read_verified_note -> Final`;
- model-generated tool arguments arrive unchanged at ToolRuntime;
- decision status/value matches the frozen oracle in the test assertion;
- the prototype observation is not projected as adoption;
- every emitted material claim ref belongs to eligible model-visible evidence;
- result artifact, AgentResult projection, RuntimeState refs, ledger, and trace
  agree;
- source fixture bytes are unchanged;
- no Gold fields appear in model requests;
- no real Provider is called.

Do not require rejected alternatives, actions, or unresolved items for this
direct question. Their fields must exist as empty arrays and must not contain
invented values.

### W6 — Resume and focused regression

Prove that an already durable valid Final reprojects to the same
TeamDecisionResult after cold resume without replaying the old model or tool
call. Do not change recovery logic to make this pass; any such need is a
blocker.

Run focused M1.1 and frozen M0 regressions. Record pass/fail/unavailable
results separately. Do not repair unrelated full-suite ACL or legacy-path
failures in this task.

Reviewer checkpoint R3: independently verify scope, contract, trajectory,
claim refs, source immutability, and no Gold leakage. The Worker cannot accept
its own output.

## 7. First RED test matrix

| Priority | Case | Expected proof | Frozen mapping |
|---|---|---|---|
| P0 | Minimal valid contract | Strict structured direct-decision output | `mps-001` |
| P0 | Production search/read/final | Real Runtime path reaches structured result | `mps-001` |
| P0 | Seen-ref membership | Every material claim cites eligible observed evidence | all; first `mps-001` |
| P0 | Unseen/fabricated ref | Fail closed; no completed TeamDecisionResult | all |
| P0 | Malformed Final JSON/schema | Safe contract failure | all |
| P0 | Premature Final | No Final before eligible evidence | all |
| P1 | Rejected alternative + reason | Alternative and reason each grounded | `drm-003` |
| P1 | Open action owner/deadline/status | Exact nullable fields and status | `inc-004` |
| P1 | Suggested is not assigned | No manufactured owner/action | `iti-005` |
| P1 | Planned is not completed | Preparation and authorization separated | `drm-002` |
| P1 | Current over stale | Authority/status, not timestamp alone | `ret-005` |
| P1 | Multi-note synthesis | All prior verified evidence remains model-visible | `aer-002` |
| P1 | Partial completion | Separate scope/unassigned owner, no invention | `aer-005` |
| P1 | Insufficient evidence | Null/unknown fields and calibrated uncertainty | `iti-005`, `aer-005` |

P1 rows guide staged expansion. They are not authorization to edit the model
loop, read tool projection, step limits, or evidence identity in W0-W6.

## 8. Post-`mps-001` expansion checkpoint

After independent acceptance of the first executable increment, a Planner
must re-evaluate four demonstrated limits before attempting the other Golden 8
cases:

1. only the latest ToolResult is included in the next model request;
2. `read_verified_note` returns one selected EvidenceRef rather than a bounded
   verified note/section bundle;
3. retrieval task `max_steps = 3` cannot express frozen multi-note preferred
   trajectories;
4. evidence IDs restart per search and may overwrite the adapter's ephemeral
   map.

The next plan must select the smallest evidence-context/read contract and prove
that it preserves M0 durability. It must not automatically add Memory,
embeddings, a new loop, or a new ToolRuntime.

Suggested semantic expansion order after that checkpoint:

```text
mps-001 direct decision
  -> drm-003 rejected alternative
  -> inc-004 unresolved actions
  -> iti-005 missing fields / proposed-vs-assigned
  -> drm-002 planned-vs-completed
  -> ret-005 stale-vs-current
  -> aer-002 multi-note synthesis
  -> aer-005 calibrated partial/insufficient evidence
```

This order is an implementation sequence, not a change to frozen Golden 8
membership or evaluation order.

## 9. Acceptance ladder

### Level 1 — deterministic contract

Run contract and request/projection tests. Required result: strict schema and
backward compatibility pass without any Provider.

### Level 2 — production-path controlled model

Run the new M1.1 production integration test. Required first result:
`mps-001` reaches a valid claim-grounded TeamDecisionResult through the actual
Runtime/Coordinator/RetrievalAgent/ToolRuntime composition.

### Level 3 — Golden 8 evaluation

Evaluate the actual implementation in manifest order and report:

- task success;
- decision accuracy;
- rejected-alternative accuracy;
- unresolved-item recall;
- action-item accuracy;
- evidence groundedness;
- unsupported claim rate;
- trajectory violations.

Do not treat empty/non-applicable Gold as automatic success. Do not hide
unsupported capabilities. Passing thresholds are:

`TO BE HUMAN-APPROVED BEFORE ACCEPTANCE`

## 10. Regression commands

First Worker focused commands:

```powershell
python -m pytest -q -p no:cacheprovider tests/unit/test_team_decision_contract.py tests/unit/test_runtime_models.py tests/unit/test_agent_contracts.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m11_team_decision_vertical_slice.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m03_model_driven_retrieval.py
python -m pytest -q -p no:cacheprovider tests/integration/test_m04_production_e2e_resume.py
python -m pytest -q -p no:cacheprovider tests/integration/test_retrieval_tool_runtime.py
python -m pytest -q -p no:cacheprovider tests/eval/test_golden8_freeze.py
python -m compileall -q src/linkloom tests/unit/test_team_decision_contract.py tests/integration/test_m11_team_decision_vertical_slice.py
git diff --check
```

Before handoff, verify:

```powershell
git diff --name-only -- docs/requirements/m1_team_decision_eval_seed tests/eval/test_golden8_freeze.py
git status --short
```

Expected dataset/freeze diff output: empty.

## 11. Scope guards

- No production file outside the seven proposed files may change.
- No existing M0 regression file may change.
- No dataset, workspace note, manifest, freeze document, or expected answer may
  change.
- No environment credential may be read and no Provider client may be
  constructed.
- No business value may be hard-coded in production.
- No Python code may choose the semantic answer or enforce a fixed search/read
  trajectory.
- No write-capable tool may be registered or invoked.

## 12. Stop conditions

Stop and return `BLOCKED — <exact incompatibility>` if implementation requires:

- modifying `SingleAgentModelLoop`, Provider adapter, ToolRuntime, ledger,
  checkpoint, recovery, Memory, or retrieval ranking for `mps-001`;
- increasing the first Worker scope to multiple Golden cases;
- exposing Gold data to a model request;
- weakening M0.1 empty-search or explicit tool-failure semantics;
- changing any unapproved production or test file;
- inventing a passing threshold;
- accessing a real Vault, network Provider, or credential.

## 13. Reviewer handoff checklist

The independent Reviewer must verify:

- governing SPEC and exact human-approved Worker boundary;
- changed files match the approved list;
- `mps-001` enters through `RuntimeEngine.start_multi_agent`;
- model owns tool choices and semantic answer;
- Runtime owns schema, evidence identity, permissions, budgets, persistence,
  and resume;
- claim-level refs were visible before Final and validate against the current
  source;
- result artifact preserves the structured payload;
- old evidence-bundle requests still work;
- no production prompt contains Gold data;
- no real Provider or Vault was used;
- all checks have PASS/FAIL/SKIPPED/UNAVAILABLE status;
- remaining Golden 8 gaps are reported, not hidden.

## 14. Learning reflection

The smallest M1.1 seam is not another retrieval system. It is an explicit
business-result request, a strict structured contract, and lossless projection
of the already durable model Final. The frozen direct case can prove that seam
without solving accumulated multi-note context; those broader gaps remain
visible for a separately approved next increment.

`WORKER AUTHORIZED — CONTRACT + mps-001 PRODUCTION-PATH PROOF ONLY`
