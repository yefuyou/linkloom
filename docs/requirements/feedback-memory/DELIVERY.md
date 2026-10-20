# Human-Governed Feedback Memory

Base: accepted multi-hop branch commit 70052cd3299c50262d8306353db5840cd1b05036.
Separate branch: codex/linkloom-memory-feedback. No real Provider calls, real
vault use, frozen experiments, formal decision writes or Prompt changes.

## What is reused

MemoryCandidate already supports a JSON value, opaque source_refs, scope, key
and metadata. MemoryLifecycle confirms a candidate with an actor; MemoryStore
appends the existing candidate_created/confirmed/rejected and item_revoked,
item_expired, item_superseded events, replaying active state on restart. Generic
PROJECT scope alone does not identify a project: this adapter requires an exact
metadata.scope_key equal to the current workspace ID, with no global fallback.

Existing Runtime project recall accepts only its own preference/terminology text
contract. This mapping is a separate strict decision-term/v1 value consumed by
FeedbackMemory.snapshot in UnifiedAskService.ask; it is never injected into model
instructions. The existing MemoryStore, not a new storage implementation, saves
it in `<product database>.feedback-memory.jsonl`. The API retains only a bounded
literal term and canonical relation, not conversation text. One continuous
Chinese term or Latin identifier (2–40 characters) is accepted; multi-word phrases
and instruction sentences are rejected at proposal and read time. At most 50 active or
pending entries per workspace are admitted through this product API.

The local human presses “I confirm this terminology” with a reviewer ID and an
explicit human_confirmed flag. This is the existing local-owner trust boundary,
not authenticated enterprise identity. The synthetic browser reviewer was
synthetic-human-reviewer; HTTP acceptance uses human-reviewer. Agent tools cannot
propose or approve these mappings. No automatic approval is implemented.

Every new query reloads active state and builds a private FrozenRelationResolver
snapshot from the unchanged base catalog plus eligible local mappings. Scope,
mapping version (immutable item ID), lifecycle state, actor, scoped state version
and resolver fingerprint are exposed separately in discovery.memory. Pending,
revoked, expired, other-scope and no-longer-eligible mappings have no routing
power. Revoke/expire retains the event history. Replacement requires revocation
followed by a new proposal and approval; this UI does not implement a bulk
supersession workflow or scheduled TTL.

Canonical relations, fixed relation vocabulary, existing aliases and trusted
entity names cannot be overridden. Approval rejects an already active term.
Read-time normalization also detects conflicting targets activated through the
generic local Memory API: Ask returns AMBIGUOUS, no conclusion, and offers
revocation or explicit trusted scope. It never chooses one target by rank.

## Comparable actual results

Frozen input: `我们现在采用哪家供方？`; same Atlas workspace, same formal decisions,
same query time `2026-10-10`. BEFORE was observed on the original base before
implementation. The four-state HTTP replay also checked the formal decision-data
SHA-256 before/after: unchanged. The only routing change was reviewed memory.

| Stage | Discovery | Result | Model calls |
| --- | --- | --- | --- |
| BEFORE | NO_MATCH | needs scope, no conclusion | 0 |
| PENDING | NO_MATCH | needs scope, no conclusion | 0 |
| APPROVED | MATCHED | DETERMINISTIC, Wrenwell | 0 |
| REVOKED | NO_MATCH | needs scope, no conclusion | 0 |

The approved answer still carries its original verified source and human
business-decision receipt. The feedback ID is not used as an evidence reference.
The base resolver fingerprint and every formal Decision Memory record remain
unchanged. One formerly failing fixed question therefore improved from 0/1 to
1/1; revocation restored 0/1. This is not a generalization benchmark.

Acceptance also checks server recreation and a genuinely new Python interpreter:
only the active mapping is restored; a fresh interpreter after revocation again
returns NO_MATCH. Another workspace remains unaffected, including access to
feedback IDs from the first workspace. Conflicting approval returns HTTP 409;
injected conflicting active mappings safely require clarification. Explicit
expiry removes the mapping. Updating the formal source independently invalidates
the old Wrenwell conclusion, even with approved terminology still present.
Three unsupported questions in the scoped negative check produce no false match.

## Product and validation

Ask includes a small “Term not understood? Propose a correction” details panel:
propose → pending → explicit confirm/reject → approved → revoke. The original
question stays in the composer. Mutation invalidates in-flight answers; workspace
switching checks both server context and client response ownership. Accepted
answers explain which approved terminology helped locate the decision.
During feedback saves, Ask submission is disabled (including the keyboard path)
to prevent a just-saved memory response from discarding a racing new question.
An observable DOM regression covers save/submit ordering and question retention.

The real browser at localhost:8803 completed BEFORE, PENDING, APPROVED and REVOKED
using the same question/date and displayed the original source and zero-model
path on success. This is actual browser execution, not a screenshot mockup.
Synthetic screenshots and the complete comparison JSON remain in ignored work/.

Final focused local regression: 139 passed across feedback HTTP, original Memory
Store/Lifecycle/Retriever/Policy, Scope Discovery, Unified Ask, ToolRuntime and
Fake/durable ModelLoop including Provider UNCERTAIN and permission boundaries.
This includes 12 feedback HTTP tests and fresh-interpreter replay. Existing Ask DOM
continuation, stale-response and workspace-isolation checks also passed.
No missing historical artifacts were regenerated; this was not a full-suite or
CI claim. Independent review found that multi-word instruction sentences were
accepted as aliases; the service was narrowed to single literal terms and a real
HTTP/generic-store bypass regression added. A named implementation plan now
extracts the sequence originally present in the pre-implementation SPEC.
Targeted independent re-review closed the code blocker; no new code blockers
were found. The named plan file was extracted at closure, not present as a
separate file before implementation; the SPEC already contained that sequence.

Reproduce offline: set PYTHONPATH=src;tests/integration and retrieval mode bm25;
run pytest tests/integration/test_feedback_memory_flow.py, or launch the existing
tests/integration/run_unified_ask_demo.py --discovery and operate the Ask panel.
The local product API is GET /api/product/feedback?workspace_id=... and POST
/api/product/feedback/{propose,approve,reject,revoke,expire}. Lifecycle actions
require the current workspace, entry ID, actor and human_confirmed=true.

## Limits

This is Human-Governed Feedback Memory: controlled terminology adaptation, with
one local server writer. Concurrent independent process writers are outside the
existing store's contract. In-memory product databases disable this feature.
It does not train weights, rewrite prompts, learn arbitrary expressions, add
business facts, generalize relation paths or turn source relationships into
authorized decisions. Unknown extra wording still requires clarification. The
existing bounded task/date classification and per-request Provider permission
remain unchanged. Real model generalization is not established by fake tests.
