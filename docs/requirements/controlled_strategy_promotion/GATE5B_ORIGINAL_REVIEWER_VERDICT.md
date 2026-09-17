BLOCK

# Gate 5B Original Reviewer Verdict

Date: 2026-09-17
Role: Independent Reviewer / Architecture Gatekeeper
Gate: 5B — Controlled Strategy Promotion (implementation and offline proof)
Base/master HEAD: `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`

## Review scope

I read the Gate 5 SPEC, implementation plan, task record, Gate 5B review
packet, Gate 5 offline proof, the prior Gate 5A verdict, and all newly owned
Strategy/evaluation source, focused tests, integration proof, and test-only
helpers. The review stayed within Gate 5B. No Runtime, Provider, prompt, tool,
Gold, upstream evaluator, production wiring, commit, push, or Gate 5C action
was performed.

The master HEAD and empty index are unchanged. The 19 unrelated tracked dirty
files and untracked Runtime V2/tasks were preserved. The 5A verdict received
only the authorized cosmetic removal of its three Markdown hard-break trailing
spaces; its APPROVE meaning and text are unchanged.

## Verdict

**BLOCK.** The implementation demonstrates useful lifecycle and isolation
machinery, but two objective 5B contract requirements are not implemented.
They must be corrected before 5B can be approved or 5C can begin.

## Objective findings

### P1 — Comparison rows do not carry the required per-metric evidence refs or context-cost delta

Expected: `SPEC.md` § “Mandatory separate comparison dimensions” (lines
160–165) requires every metric, including the context character/UTF-8 cost
proxy, to have a per-case outcome, evidence ref, and delta. The Gate 5B packet
(lines 39–40) additionally promises baseline/candidate evidence refs in the
sealed comparison and receipt.

Actual: `src/linkloom/strategy/models.py:140-167` defines `MetricDelta` with
only `case_key`, `dimension`, `baseline`, `candidate`, `delta`, and `severity`.
There is no evidence-reference field or dimension-specific locator. The
`ComparisonSummary` at lines 171–185 stores only aggregate candidate
`context_chars` and `context_bytes`; it has no baseline/candidate context
measurements, per-case context row, or cost delta. The runner at
`src/linkloom/evaluation/strategy.py:169-186` creates rows solely from oracle
labels and derives only PASS/FAIL deltas; it never records evidence refs or a
context-cost delta. `StrategyReviewReceipt` embeds this incomplete summary, so
the persisted human receipt cannot audit which observed evidence supports each
dimension result.

Independent evidence: the retained responsive comparison JSON produced by the
integration test has row keys exactly
`case_key, dimension, baseline, candidate, delta, severity`, and only
`context_chars/context_bytes` at summary level. A direct contract inspection
also reports `MetricDelta` fields as
`('case_key', 'dimension', 'baseline', 'candidate', 'delta', 'severity')`.

Required correction: extend the closed comparison contract and producer,
authority, receipt serialization, and tests so every applicable per-case
dimension has a canonical evidence reference (or an explicitly validated
N/E reason), and the context-cost metric records the required baseline,
candidate, and delta accounting. Keep evidence opaque/Gold-safe and preserve
the existing hash/seal bindings.

### P1 — Fairness authority does not enforce identical non-context observations

Expected: `SPEC.md` § “Offline comparison and evidence authority”, step 2,
requires all request fields and tool/document observations to be identical
between branches except for `strategy_context`.

Actual: `src/linkloom/evaluation/strategy.py:117-119` computes
`shared_condition` only from `OfflineObservation.request_received`, excluding
`strategy_context`. `OfflineObservation.trajectory_json` is an opaque JSON
string (`:89-98`), and the runner (`:143-149`) checks only that each received
request equals the constructed request and that those request hashes match
across branches. It never compares or hashes branch trajectory/tool/document
observations. `ComparisonAuthority` consequently has no independent binding
for that missing equality. A harness can return different tool/trajectory
observations while requests match and the oracle still returns a gain; the
comparison can then be recommended accepted without proving the sole-variable
condition.

Required correction: give the observed tool/document/trajectory inputs a
typed, canonical fingerprint or equivalent evaluator-owned binding and include
it in the shared-condition comparison/authority checks (excluding only the
strategy context). Add an adversarial test that mutates a non-context
observation and proves the comparison fails closed.

## Acceptance evidence reviewed

- Focused Gate 5B command: **69 passed** (`134.29s`).
- Offline integration proof: **3 passed** (`2.93s`), independently showing
  test-only accepted machinery, frozen replay
  `insufficient_evidence -> rejected`, and historical unapproved source
  `0 Strategy`.
- `python -m compileall -q src/linkloom/strategy
  src/linkloom/evaluation/strategy.py`: **PASS**.
- `git diff --check`: **PASS**; only unrelated LF/CRLF conversion warnings
  were emitted. An explicit trailing-whitespace scan over owned source/tests/
  docs and the 5A verdict is clean.
- The focused/integration proof paths showed actual baseline/candidate request
  equality, both observation seals before the test oracle, conservative
  `recommend_accept`/`insufficient_evidence` behavior, durable source/
  comparison/human receipt bindings, accepted-only final context, and the six
  synthetic durable saturation strategies under hard 5/4000/16000 bounds.

The required adjacent command was attempted independently. The first run was
blocked by the host's write permission on
`C:\Users\18019\AppData\Local\Temp\pytest-of-18019` (304 passed, 1 skipped,
116 fixture setup errors, and 12 permission-related failures); a project-temp
retry hit the same pytest temp cleanup permission boundary and was stopped.
This is an environment evidence limitation, not counted as a new Gate 5 code
failure, and I do not claim an independent 418-count reproduction.

## Non-blocking limits confirmed

- The responsive FakeModel path proves mechanism and lifecycle boundaries only;
  it is not real Provider efficacy or real human intent.
- The frozen real trace correctly remains `insufficient_evidence` and its
  explicit `human:test_fixture` rejection is not a real user approval.
- Historical mps/aer/iti source approval remains absent (`mps=1` candidate,
  `aer=0`, `iti=0`); no architecture verdict was converted into an Experience
  or Strategy approval. No production accepted Strategy exists.
- Provider use, Runtime/prompt/tool wiring, authentication/RBAC, tokenizer,
  live efficacy, and automatic promotion remain frozen/deferred as required.

## Required next step

Builder must address both P1 findings within the approved Gate 5B boundary,
update the focused adversarial coverage and offline proof, then resubmit a
fresh unified packet for original review. Gate 5C integrity preparation and
any commit remain frozen until a subsequent original Reviewer **APPROVE**.

## Learning reflection

### Step

- Role: Reviewer
- Feature: Controlled Strategy Promotion, Gate 5B
- Files reviewed: Gate 5 SPEC/plan/task/packets/proof, `src/linkloom/strategy/`,
  `src/linkloom/evaluation/strategy.py`, Strategy unit/integration tests and
  test-only bridges.

### What changed

The Worker delivered isolated candidate generation, sealed paired offline
machinery, explicit human test receipts, accepted-only bounded context, and
honest proof labels. The gate remains blocked because the durable comparison
contract is not evidence-addressable per metric and does not enforce all
non-context observation equality.

### What I learned

Passing labels and aggregate artifact hashes are not enough for an auditable
promotion decision: each dimension needs an evidence locator, and a
single-variable request hash must cover every observation that can affect the
oracle. A test-only accepted path can prove mechanics without proving either
real efficacy or a real human decision.

### Evidence and next role

Evidence is listed above, including the independent 69-test focused pass,
3-test integration proof, direct schema inspection, retained JSON inspection,
compile/whitespace checks, and the adjacent-environment permission boundary.
Next role is Builder for the two objective fixes, followed by the original
Independent Reviewer for a fresh Gate 5B decision; no 5C action is authorized
by this verdict.
