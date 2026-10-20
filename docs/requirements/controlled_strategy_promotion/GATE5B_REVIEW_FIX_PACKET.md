# GATE 5B REVIEW FIX PACKET — ROUND 2

Date: 2026-09-17. Worker fixes only, original Reviewer recheck pending.
Original [BLOCK verdict](GATE5B_ORIGINAL_REVIEWER_VERDICT.md) preserved;
[unified packet](GATE5B_REVIEW_PACKET.md) and
[refreshed proof](GATE5_OFFLINE_PROOF.md) are the current handoff.
Base/master `85a6dc3a8429cd67ca853546bf6b9a5c36d1c0b5`; index empty.
No Provider, production wiring, prompt/tool/Runtime/Gold/evaluator changes,
commit/push or Gate 5C action. Original 19 dirty bytes remain outside scope.

## P1 evidence and cost audit

RED: row.baseline_evidence and summary.context_costs absent (AttributeError).
GREEN: every MetricDelta has baseline_evidence/candidate_evidence, exact closed
MetricEvidenceReference: schema_version, case_key, dimension, artifact_sha256,
observation_sha256, reason. This is an explicit qualified locator into a sealed
branch array and its exact case observation, not implicit aggregate hashes.
Reason vocabulary is fixed: sealed_observation/v1 for PASS/FAIL;
oracle_not_evaluated/v1 for N/E; oracle_review_required/v1 for REVIEW_REQUIRED;
tools_not_applicable/v1 only for predeclared N/A. No private prose/answer/path.

Every case has exactly one ContextCostDelta, paired evidence and measured
baseline_chars/candidate_chars/char_delta and baseline_bytes/candidate_bytes/
byte_delta. Complete case set, closed integers, exact arithmetic, nonnegative
measurements, aggregate agreement and unavailable token/currency=null checked.
Authority freshly regenerates each reference and cost row from both sealed
actual observations; orphan observation/case/dimension/hash or cost mismatch
cannot authorize review/reuse. Receipt persists the complete issued summary.

## P1 non-context observation equality

RED: changing trajectory/tools/documents while request fields matched could
still produce recommendation gain (three DID NOT RAISE reproductions).
GREEN: canonical typed ControlledObservationInputs has versioned exact
document_observations_json/tool_observations_json finite array fields.
OfflineObservation consumes that type and canonical final/trajectory JSON;
constructor-bypassed values are closed re-parsed before producer use.

shared_condition now hashes actual received request except strategy_context,
the complete controlled input envelope and full trajectory canonical SHA.
Runner compares branches before Gold or oracle. Authority independently
recomputes from sealed branches and binds the equality to condition_sha256.
v1 intentionally freezes the complete observed trajectory/tool/doc vectors;
only context and evaluated final output may differ. Tool-changing strategy or
responsive trajectory redesign is deferred, not silently another eval variable.

## New adversarial tests / regression

`tests/unit/test_strategy_review_fix.py`: **13 passed**. Includes explicit
paired locator/receipt inspection; per-case cost delta; three producer noncontext
mutations; N/E/review-required reason coherence; missing cost / fake token / wrong
delta; three fully re-sealed/re-hashed producer observation attacks still rejected
by authority; a re-hashed orphan metric locator; noncanonical/extra-field vectors.

Final full Gate 5 focused: **82 passed, 139.29s**.
Required adjacent host command: **418 passed, 1 skipped, 29.68s**.
Combined **500 passed, 1 skipped**. Compileall, owned whitespace/conflict scan
and normal configured git diff --check pass. No criteria weakening or new Gold.
Historical disposition and raw pins remain mps1/aer0/iti0.

Sandbox-only adjacent attempt hit WinError 5 on new pytest basetemp at fixture
access/cleanup; not counted as success or code failure. Verified host recheck
used fresh retained `.tmp/gate5b-recheck-host-7e763b60515d404f8cbccd267a627ce4`.
For independent reproduction use the full adjacent command in implementation
plan with host permission, a different fresh UUID project basetemp, retention
all, resolved target guard and refuse-existing guard. Do not reuse/delete roots.

## Proof and learning reflection

Current persisted FakeModel test acceptance and frozen trace test rejection
both include new explicit evidence and per-case cost rows; all original real
artifacts and round-1 proof outputs remain untouched. Frozen trace still
insufficient_evidence, unknown dimensions not FAIL/PASS fabricated. Historical
unapproved Experience still generates zero Strategy; no production accepted
Strategy or real human intent claimed.

Learned: requesting equal inputs is not observing equal inputs. Audit locators
and actual tool/document/trajectory identity must survive sealed authority and
persisted review, not just labels. Next role: original Reviewer writes
GATE5B_ORIGINAL_REVIEWER_RECHECK_VERDICT.md and callbacks Builder. Only its
APPROVE permits 5C; Builder does not self-approve or begin that stage.
