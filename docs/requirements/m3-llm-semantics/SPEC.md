# SPEC — M3-A LLM-first task relation semantics

Approved scope: the user's M3-A request on 2026-10-18, fixed baseline
`26392432dcef70692461e6f2f50bc1bd5528f69a`, independent worktree/branch
`codex/linkloom-m3-llm-semantics`. This document translates that already approved
boundary into contracts; no new milestone approval is required.

## Behavior and ownership

One `ModelProviderAdapter.complete(ModelTurnRequest)` receives an authorized,
bounded source snapshot, user question, validated M1 observations and typed
task/project/change endpoints. A forced structured function call is a data
proposal only. No tool is executed and no Coordinator/Runtime/routing is used.

The model interprets identity, work links, direct causality, business revisions,
project membership/alias, negation, conditionals, conflicting sources, source
corrections and missing evidence. Old P1/P2/P3 semantic regexes are neither
called nor used as acceptance gates. Their source is immutable this round.

Three modules own the new boundary: semantic models (immutable contracts and
authorized snapshot), proposer (prompt/schema, request admission, one provider
attempt and provenance), validation (current sources, exact references,
revocations, typed endpoints, authority and conditional date arithmetic).

The caller owns authorization, the complete bounded observation inventory,
current registry/revocation state and optional read-only formal decision store.
No module discovers credentials, opens a database or stores a P4 assessment.
Validation always uses a supplied current snapshot, without cross-call caches.

## Contracts and trust

Inputs include workspace_id, question, RawArtifacts, canonical allowed spans,
M1 observations, typed endpoint bindings, current SourceReferenceRegistry,
revocation identifiers and input/output token plus source/endpoint/proposal
limits. Authorizing a workspace does not authorize undisclosed source bytes:
only explicitly allowed spans are exposed. Bounds fail before provider use;
truncated or missing context cannot be presented as exhaustive discovery.

Model arguments contain bounded endpoint/span handles and exact source quotes.
The host resolves handles to ObservationRef/ProjectRef/ChangeRef. A unique exact
substring in an allowed canonical span determines Unicode [start,end); ambiguous
or fabricated quotes fail. Endpoint existence, full ObservationRef payload,
source identity/version/hash/currentness, scope and revocations are verified.
No model-supplied authority, confidence, hash or lifecycle flag is trusted.

SemanticProposal contains predicate, typed subject/object, proposed_verdict,
support/counter/missing evidence, reason_codes, changed_fields, host-bound model
and prompt version, source_snapshot_hash and execution/usage provenance.
SAME_TASK and RELATED_TO retain canonical symmetric comparison; revisions and
impact keep direction. A supported revision must reference a supported identity
proposal for the same pair; this is contract consistency, not proof that the
model's identity interpretation is semantically correct.

MODEL_PROPOSED is never a RelationAssessment. EVIDENCE_VALID certifies references
and contract integrity only. A semantically wrong statement with real quotes
may be EVIDENCE_VALID. SEMANTIC_ACCEPTED is an evaluator's independently checked
result, not a model flag, persistence permission or publication state.

Supported/refuted proposals need corresponding evidence; CONFLICT needs both
sides; UNKNOWN needs a concrete missing-evidence explanation. These structural
requirements do not claim that the cited words entail the conclusion.

SOURCE_CLAIM remains unapproved. REVIEWED_DECISION endpoints require the existing
receipt/chain verifier and read-only supplied store. Invalid authority does not
silently downgrade to a source claim. Dates reuse M1 precision/field evidence
and a small date-boundary parser, then Python calendar arithmetic;
they never invoke regex identity/revision replay. Comparison results are
explicitly conditional on evidence-valid model premises and remain unpublished.
Missing years, incompatible boundaries, unknowns and conflicts have null delta.

## Provider and budget

Reuse GeminiProviderAdapter, ModelTurnRequest/ModelResponse/ModelUsage and the
existing official SDK builder. Existing configured model is gemini-3.8-flash;
no Gemini 2.5 default or fallback. First perform read-only models.get and inspect
declared capabilities, actual model name, context limits and usage mapping.
The user explicitly answered that the API budget has no upper limit. This
bounded task nevertheless plans at most three generation requests, serial,
with SDK attempts=1, no semantic-repair retries and synthetic sources only.
Run all offline checks before live Smoke. Actual model response versions,
candidate/thinking tokens, latency and estimated API cost are recorded; unknown
usage/cost stays null. Cost is an estimate from official dated prices, not an
invoice. No real/private vault sources or environment/credential dump is allowed.

## Evaluation and acceptance

Create fresh synthetic Dev variants before production implementation, with
public inputs and separately authored Gold. Cover revisions, direct causes,
cross-message binding, conditional/negated statements, conflict, source
correction, same owner but independent tasks, alias/project scope and missing
year. No case-ID branches in production. FakeModel verifies contracts and
failure boundaries only; it cannot establish LLM semantic quality.

Reuse existing m3_task_impact Dev sources and offline scorer where contracts
allow. If a fixed-candidate/M1 diagnostic bridge is necessary, disclose it and
do not claim task discovery, end-to-end Agent quality or production Adapter.
H01-H04 are exposed diagnostic regressions only. M3 holdout is not executed.
Predictions are written before a separate scorer reads Gold; provider-facing
code never imports evaluator annotations. Report per-state/predicate scores,
false merge/revision, evidence validity vs semantic correctness, call/token/cost
counts and all actual failures; no pytest count substitutes for semantic scores.

Acceptance requires executable FakeModel contracts; fabricated/stale/revoked/
cross-workspace refs and authority escalation rejected; genuine model judgments
can bypass old phrase grammar; no new production DB writes; untouched old
rules/runtime/Gold/history; independent read-only review; reproducible sanitized
artifacts; scoped commit and normal push to the requested independent branch.
Live semantic failures are reported, not hidden by expanding prompt tuning or
the call budget. No requirement to attain a benchmark score on three Smoke calls.

## P4 boundary

Study P4's replay contract and provide the smallest versioned future receipt /
semantic-acceptance/revocation contract. This round only recommends it; neither
P4 schema, replay nor persistence accepts model proposals.
