# SPEC: Semantic Ingestion

**Status:** IMPLEMENTED IN THE CURRENT V1 WORKTREE. The bounded browser and
real-Gemini release acceptance is recorded in
[the product roadmap](../../PRODUCT_ROADMAP.md) (2026-10-08). V1 feature work is
frozen; this status does not imply a commit, CI run, or deployment. Detailed
run receipts remain local under `work/` and are excluded from the public package.

Approval basis: explicit user authorization in the 2026-10-07 Step 3 task, after
the product contract/roadmap reconciliation and SQLite design review. This is
not an external reviewer approval. The implementation boundary is limited to
the three-step plan below and does not authorize live Provider calls, source
workspace writes, benchmark evaluation, or changes to the V2 planner/retrieval
algorithms.

## Problem

LinkLoom's V2 retrieval stack can answer temporal and multi-hop questions once structured DecisionRecord values and provenance already exist. Current MemoryAgentBench input is narrower: parse_ordered_facts parses numbered FactConsolidation statements, and build_temporal_memory maps those parsed facts to structured records. DecisionMaterializer.propose accepts an already structured DecisionRecord; it does not extract one from conversation.

Project teams instead keep decisions in meeting notes, timestamped chat, project documents, decision logs, and sometimes emails. Without a governed raw-artifact-to-memory boundary, an adapter cannot turn these sources into a fair V2 input. Semantic Ingestion adds that boundary while preserving the existing deterministic temporal lookup and retrieval stack.

## Product outcome

A team member can ingest a small timestamped conversation and ask what the team currently decided, what it previously decided, and which exact source spans support both answers. Clear low-risk decisions may enter memory under an audited policy. Ambiguous or high-risk candidates remain reviewable. A model must never create authoritative memory without source evidence and an explicit materialization authorization.

This SPEC follows the current Team Decision Recovery / Temporal Decision
Intelligence direction. The root `SPEC.md`, `DEV_SPEC.md`, README files, and
`docs/PRODUCT_ROADMAP.md` now distinguish the historical Scanner foundation
from the current product contract. The current architecture and the preserved
product-history language are described in those documents.

## Goals

- Convert one narrow source type—timestamped plain meeting/chat text—into validated, source-grounded decision candidates.
- Distinguish decisions from proposals, questions, rejected options, plans, action items, opinions, and speculation.
- Resolve candidate entities and relations against explicit workspace context and a governed canonical relation catalog.
- Preserve event time, ingestion time, and valid time as different concepts.
- Reuse DecisionRecord, DecisionCandidate, DecisionMaterializer, DecisionMemoryWritePolicy, SourceReferenceRegistry, and TemporalDecisionStore where their contracts fit.
- Make duplicate ingestion, source changes, and extraction corrections auditable and safe.
- Prove current and historical retrieval through synthetic end-to-end cases.

## Non-goals

- Full Slack, email, document-drive, or meeting-service connectors.
- PDF/OCR, attachments, image/audio/video, ASR, multimodal parsing, or arbitrary email threading.
- Broad entity resolution, ontology learning, or automatic canonical-relation expansion.
- A general-purpose knowledge graph, graph database, or open-ended graph reasoning engine.
- Benchmark-specific adapters or tuning for RHELM, DECADE, or individual QA examples.
- Replacing the existing relation planner, bounded traversal, ContextAssembler, or Reader.
- Silent real-source mutation or automatic deletion of existing memory.
- Optimizing end-to-end QA accuracy before ingestion quality is measured.

## Users and workflow

1. A user selects or supplies one timestamped plain-text artifact in a named workspace.
2. LinkLoom identifies message spans and their source timestamps, then extracts typed candidate claims with exact evidence references.
3. Deterministic validation checks schema, grounding, entities, canonical relations, temporal semantics, conflicts, and duplicate identity.
4. A risk policy either keeps the candidate out of authoritative memory, routes it to review, or authorizes low-risk materialization with a recorded policy decision. Review may approve or reject a batch of ordinary candidates; it is not a click-per-record requirement.
5. The existing temporal store persists accepted records and supersession.
6. The existing structured lookup and bounded retrieval path returns the current/historical value with source references.

## Architecture boundary

    RawArtifact
      -> deterministic segmentation
      -> provider-neutral structured extraction
      -> CandidateDecisionFact
      -> local validation: claim type, entity, relation, time, provenance
      -> risk/write policy
         -> review or non-authoritative evidence
         -> authorized DecisionMaterializer
      -> TemporalDecisionStore
      -> existing schema-constrained planner
      -> existing deterministic bounded traversal
      -> ContextAssembler
      -> existing Reader

Extraction is an untrusted proposal boundary. It cannot call the store directly. The materializer remains the only authority transition. Candidate, source version, policy outcome, and final memory record must have stable linked IDs.

## Input contract: RawArtifact

V1 accepts UTF-8 plain text with timestamped speaker/message blocks. Connector metadata may be represented later, but connectors are not part of V1.

| Field | Classification | Contract |
|---|---|---|
| workspace_id | Required | Existing authorized workspace scope; never inferred from content. |
| artifact_id | Required | Stable caller/source identity within the workspace. A re-export of the same source must reuse it. |
| source_type | Required | V1 enum value timestamped_text; unsupported types fail closed. |
| content | Required | Exact UTF-8 text bytes or a precisely defined decoded text representation; preserve a byte/content hash for the ingested version. |
| ingestion_time | Derived | UTC time when LinkLoom accepted this artifact version; never substituted for source/event time. |
| content_hash | Derived | SHA-256 of the canonical ingested content bytes. |
| source_uri | Optional | User-visible local or connector reference; redact credentials/query secrets before persistence. |
| parent_id / thread_id | Optional | Stable source grouping if supplied; not required for the timestamped-text slice. |
| author | Optional | Artifact-level owner/importer only. Message speaker identity is derived from parsed blocks. |
| metadata | Optional | Small allowlisted JSON metadata only; no arbitrary secret-bearing headers. |
| Segment IDs, offsets, message timestamps | Derived | Deterministic parser output bound to content_hash and parser/schema version. |

If the input is edited while it is being processed, the captured content hash must not change. Process the captured version or stop and retry as a new artifact version.

## Segmentation contract

The first parser recognizes explicit lines in this form:

    [2026-10-01T10:03:00Z] Alice: We decided to use Vendor A for launch.

The timestamp must be an unambiguous ISO-8601 value with an explicit timezone. The speaker label and message body are kept separately. Message IDs and Unicode-character start/end offsets are derived deterministically from artifact identity, content hash, parser version, and message ordinal. Unparsed text remains attached to the artifact but cannot be silently assigned a timestamp. A parser failure is visible and does not create memory candidates from the affected text.

## Candidate contract: CandidateDecisionFact

This is an intermediate, non-authoritative value. It should extend or compose with the existing DecisionCandidate; it must not be a second persistence model that competes with DecisionRecord.

Minimum fields:

| Field | Meaning |
|---|---|
| candidate_id | Stable identity derived from source version, span, normalized claim, and extraction fingerprint; not a fresh random ID on every replay. |
| workspace_id, artifact_id, source_episode_id | Existing workspace and episode/source identity used by DecisionRecord. |
| claim_type | DECISION, FACT, PROPOSAL, QUESTION, REJECTED_OPTION, PLAN, ACTION, OPINION, or SPECULATION. |
| subject, subject_key, relation, value | Structured claim fields; subject_key is stable for the decision slot, including canonical relation where required by current uniqueness rules. |
| relation_resolution | CANONICAL_RELATION, NEW_RELATION_CANDIDATE, or UNRESOLVED_RELATION. |
| event_time | When the source message/event occurred, if supplied or deterministically parsed. |
| valid_from, valid_to | When the claim applies; separate from event time and ingestion time. Unknown values remain unknown. |
| temporal_basis | EXPLICIT, DETERMINISTIC_RULE, or UNRESOLVED, plus the source span/rule supporting it. |
| provenance | Exact source identity, source hash, segment/message identity, offsets or verified quote, and quote hash. |
| confidence | Calibratable sub-scores for claim type, entity, relation, temporal interpretation, and overall disposition. Scores do not override deterministic gates. |
| extractor | Provider/model identity, schema version, prompt fingerprint, request/response hashes, usage, latency, and explicit attempt count. |
| candidate_status | CANDIDATE, READY, REVIEW_REQUIRED, DUPLICATE, REJECTED, MATERIALIZED, or INVALIDATED, with a stable reason code. |
| supersedes_candidate_id | Optional proposed link; the materializer/store verifies the actual target and chronology. |

DECISION and clearly asserted FACT can be considered for authoritative Temporal Decision Memory only after all write gates pass. Other claim types are retained as supporting searchable evidence only when useful; they do not become DecisionRecord values. Action extraction remains outside the initial memory slice even though the existing ActionRecord type is available.

## DecisionRecord materialization boundary

The existing DecisionRecord already carries decision_id, workspace_id, subject_key, value, status, valid_from, valid_to, supersedes_id, source_episode_id, source_evidence_refs, provenance_run_id, and optional structured subject/relation. Reuse these fields and the existing DecisionCandidate lifecycle instead of introducing a parallel authoritative record.

Materialize only when all of the following are true:

1. claim_type is an explicit decision or a supported factual state, not a proposal/opinion/speculation.
2. Workspace, subject, and decision slot are resolved.
3. The relation is canonical and allowed by the frozen workspace/catalog version.
4. valid_from is explicit or produced by a documented deterministic rule; unresolved temporal interpretation blocks activation.
5. At least one source evidence span verifies against the exact source version.
6. No unresolved conflict or ambiguous supersession exists.
7. Source and candidate hashes are stable and the idempotency check passes.
8. A human batch approval or a qualifying, versioned low-risk policy authorization is recorded.

DecisionMaterializer.materialize() currently requires a non-empty approved_by value. The implementation must not spoof a human identity to enable automation. Extend the authorization contract explicitly to distinguish HUMAN_BATCH_REVIEW from a versioned AUTO_POLICY decision, recording its rule version, candidate IDs, and reason. If that contract is not implemented, the system must keep candidates staged; it must not bypass the current gate.

The store's active uniqueness is keyed by workspace_id and subject_key, while
structured lookup matches normalized subject and relation. Step 3 uses a
deterministic relation-scoped `subject_key`, preserves a legacy-key alias for
existing structured records, and fails migration on a slot collision. Existing
DecisionRecord data and structured lookup remain readable. The unique active
slot constraint is not weakened. The exact additive schema migration is
reviewed in
[`docs/architecture/SEMANTIC_INGESTION_SQLITE_MIGRATION.md`](../../architecture/SEMANTIC_INGESTION_SQLITE_MIGRATION.md).

Unresolved and review-required CandidateDecisionFacts must survive process
restart. Store their immutable typed payload/fingerprint, scalar source and
workflow fields, append-only review/policy events, and materialization receipt
in the existing SQLite store. Current workflow state is a queryable projection;
the append-only event history records every transition.

Materialization, evidence writes, previous-record interval closure, receipt,
candidate state transition, and audit events share one SQLite transaction.
Human approvals and policy authorizations bind to the exact workspace and
immutable candidate fingerprint. A candidate with changed content, source
version, or extraction fingerprint cannot inherit an earlier approval.

## Provenance model

Every candidate maps through this chain:

    workspace + artifact_id + content_hash
      -> parser_version + segment_id/message_id + exact span
      -> candidate_id + extraction_fingerprint
      -> DecisionRecord.source_episode_id + source_evidence_refs
      -> final retrieval evidence

Prefer offsets into immutable content, with content_hash, char_start, and char_end using a documented Unicode indexing convention. Store a quote and quote SHA-256 as a verification aid, not as a replacement for source identity. If offsets cannot be preserved by a connector, use a stable source block or message ID plus an exact quote that can be revalidated against the same content hash. If neither exact offsets nor a verifiable exact quote exists, return PROVENANCE_INVALID and do not create an authoritative record.

The final source_evidence_refs value must point to registered, workspace-scoped source evidence accepted by SourceReferenceRegistry. A Reader claim must always be traceable to those references; model output alone is never provenance.

## Temporal semantics

Keep three clocks separate:

- **Event time:** when the statement or decision event occurred, usually the timestamp on its message.
- **Ingestion time:** when LinkLoom processed this source version.
- **Valid time:** when the decision/fact is intended to apply, represented by DecisionRecord.valid_from and valid_to.

Use event time as valid time only when the source unambiguously says the decision takes effect at that event. Do not substitute ingestion time. Relative dates such as “next Monday” remain TEMPORAL_UNRESOLVED unless a deterministic rule has an explicit reference date, timezone, locale, and unambiguous interpretation. V1 supports explicit ISO timestamps/dates and exact source message timestamps; it does not promise general natural-language date parsing.

For a future-effective decision, an approved DecisionRecord may be persisted
with its exact future `valid_from`. When it supersedes an existing record, the
prior record's `valid_to` is set to that boundary and the new record explicitly
links to it. Current and as-of lookup resolve the half-open valid-time interval
at query time, so the old value remains current before the boundary and the
new value becomes current at/after it. No background scheduler or physical
midnight activation is required. Only one future successor per slot may be
pending; overlapping future successors require review.

For a later accepted decision, set supersedes_id to the verified predecessor. The previous record remains queryable through its validity interval. A conflict, ambiguous target, or non-chronological replacement stays a candidate and cannot change current memory.

## Relation and entity governance

The extractor receives a frozen canonical relation catalog and schema version. Allowed outcomes are:

- CANONICAL_RELATION: exact canonical value selected, with a confidence score and source-grounded subject/value.
- NEW_RELATION_CANDIDATE: a useful phrase has no safe canonical mapping; it is stored for review and does not change the catalog.
- UNRESOLVED_RELATION: no safe mapping; candidate remains non-authoritative.

Mapping synonyms to existing relations may use a small versioned alias table and/or constrained structured extraction. An LLM phrase is never sufficient to add a canonical relation. A human or separately approved governance action must add a relation and bump the catalog version/fingerprint before future materialization.

Entity normalization is similarly bounded: reuse an exact workspace entity or an explicit alias; do not merge people/projects/vendors through fuzzy similarity alone. Unresolved entities remain candidates or supporting evidence.

## Decision versus ordinary content

| Type | Authoritative Temporal Decision Memory | Supporting searchable evidence |
|---|---|---|
| Explicitly selected/approved decision | Eligible after all gates | Yes |
| Explicit factual state relevant to a decision slot | Eligible only when the schema can represent it and provenance is verified | Yes |
| Proposal or tentative suggestion | No | Yes when useful |
| Question or unresolved issue | No | Yes when useful; may be surfaced as unresolved evidence |
| Rejected option | No independent current decision; preserve as evidence attached to a supported decision when the source explicitly rejects it | Yes |
| Superseded decision | Historical authoritative record, linked by supersedes_id | Yes |
| Plan / intention | No in V1 unless explicitly adopted as a decision with an effective time | Yes |
| Action item | Not in the decision-memory slice; future mapping may use ActionRecord | Yes |
| Opinion / speculation | No | Usually omit from materialized memory; raw artifact may remain searchable under source policy |

Classification is a typed, schema-validated decision based on the statement, its speaker/context, and evidence; prompt wording is not the only control. An ambiguous classification is not promoted to DECISION by default.

## Review and automatic-write boundary

Human review is required for conflicts, a proposed new relation, unresolved entity identity, uncertain temporal meaning, uncertain supersession, or material source/provenance mismatch. Missing or unverifiable provenance is a hard rejection from authoritative memory, not a low-confidence auto-write.

Human review may resolve an ambiguous structured field through an append-only
adjudication overlay bound to the immutable candidate fingerprint. The
captured extraction payload and its fingerprint never change. The overlay may
attest an intended subject, confirm a relation already marked canonical, or
set an explicit valid-time start; its reviewer, reason, timestamp, and overlay
fingerprint are stored with the approval event and linked from the
materialization receipt. It cannot add a relation, replace an extracted value,
or change source evidence. Correcting an extracted value uses the separate
extraction-correction workflow. If the reviewer cannot resolve every required
field, the candidate remains blocked.

Ordinary extraction need not require one approval per claim. A versioned
low-risk policy may authorize a batch when every candidate is explicit,
well-grounded, canonical, temporally clear, duplicate-free, and above
calibrated field-specific confidence thresholds, with no contradictory
source. The decision and policy fingerprint must be auditable. Step 3 starts
with no calibrated confidence profile installed, so the default policy keeps
otherwise eligible candidates in review. Policy authorization becomes
available only when an explicitly versioned calibration profile is supplied.
Conflicts, replacements/supersession, future-effective records, and uncertain
semantics require human review. Unsupported classes remain supporting evidence
only; invalid provenance or workspace mismatch is rejected.

## Idempotency and incremental ingestion

Use layered identities:

1. Artifact version: workspace_id, artifact_id, content_hash.
2. Extraction run: artifact version plus parser/schema version, extractor provider/model, prompt fingerprint, relation-catalog fingerprint, and temporal-policy version.
3. Candidate: extraction run plus stable evidence span and normalized claim.
4. Materialized decision slot: workspace, normalized subject/relation slot, normalized value, effective time, and verified evidence identity.

Re-ingesting the same artifact version with the same extraction fingerprint is a no-op or returns the prior sealed extraction. A duplicate candidate must not create a second authoritative record. A new model/prompt version creates a new extraction run for comparison; it does not overwrite accepted memory merely because its output differs.

An edited artifact creates a new content version. Preserve the prior version and provenance. Use DecisionReconciler/source inventory to mark affected records NEEDS_REVALIDATION when their evidence is no longer present or cannot be verified; do not silently delete them. Unchanged exact spans may be rebound only after hash and quote validation.

## Correction and supersession semantics

- **Source correction:** same source identity, new content hash. Append a new version; revalidate dependent spans and mark invalid references for review.
- **New decision:** a later source explicitly changes the selected value. Keep the prior decision as history and materialize the new one with supersedes_id and a valid-time boundary.
- **Extraction correction:** the source is unchanged but an earlier extraction was wrong. Append a correction event linking the bad candidate/record to the new one; mark the old candidate INVALIDATED or the record NEEDS_REVALIDATION as appropriate. Preserve both audit trails. Do not represent an extraction bug as a new business decision.
- **Source deletion/unavailability:** retain the source tombstone and hashes; mark dependent memory for revalidation and prevent unsupported evidence from being presented as verified. Never erase decision history silently.

## Failure taxonomy

Failures are stable machine-readable reason codes. None may silently create an authoritative record.

| Reason code | Result |
|---|---|
| SOURCE_PARSE_FAILED | No candidates for unparsed affected spans; record parser diagnostics. |
| EXTRACTION_FAILED | Preserve artifact and explicit provider failure; no candidate promotion. |
| MALFORMED_EXTRACTION | Reject schema-invalid/extra-field output; do not repair by guessing. |
| RELATION_UNRESOLVED | Keep a non-authoritative candidate or evidence; catalog unchanged. |
| ENTITY_UNRESOLVED | Keep candidate for review; no entity alias or write. |
| TEMPORAL_UNRESOLVED | Keep candidate staged; no current-state change. |
| PROVENANCE_INVALID | Reject authoritative materialization. |
| CONFLICT_REQUIRES_REVIEW | Keep existing memory unchanged and surface both source spans. |
| DUPLICATE | Return existing candidate/record identity; no second write. |
| MATERIALIZATION_REJECTED | Preserve candidate and precise policy/store reason. |
| SOURCE_CHANGED_DURING_INGESTION | Stop this artifact version and require a new hash-bound run. |

## Provider contract, privacy, and audit

- Depend on the existing ModelProviderAdapter abstraction; do not couple to Gemini or DeepSeek SDKs.
- Require strict structured output with a versioned schema and local validation. Extra keys, missing required fields, invalid enums, and invalid evidence refs fail closed.
- Record provider/model, extraction schema version, prompt SHA-256, request and response hashes, usage when supplied, latency, explicit retry count, and terminal reason. Do not store hidden reasoning or full raw provider payloads by default.
- Temperature and other generation settings are explicit and deterministic where available. Application retry limits are configured; SDK retries are disabled or explicitly accounted for. A received response is journaled before downstream validation, and a persisted extraction is not replayed after a local failure.
- Only selected artifact segments are sent to the configured model provider. The user is told when content leaves the local device. No connector or background upload is implied by this SPEC.
- Usage/cost uncertainty is handled through the existing explicit cost policy; missing token counts are unknown, never zero. If a safe cost bound cannot be established, extraction stops before a provider request.

## Minimal V1 scope

**Input:** one timestamped plain-text meeting/chat artifact with explicit timezone offsets and speaker labels.

**Output:** candidates plus provenance; a narrow set of clear decisions may be policy-authorized in a batch; temporal memory can retrieve current and prior values. The synthetic fixture seeds the canonical relation catalog explicitly (for example, uses vendor) so the test does not silently invent ontology.

**Not included:** external source connectors, broad entity linking, arbitrary date interpretation, actions as first-class extraction, attachments, OCR, ASR, or a new graph platform.

## Synthetic acceptance scenarios

All examples below are invented fixtures. They do not use RHELM or DECADE data. Assume the fixture has a seeded canonical relation uses vendor and a source registry bound to the test workspace.

### 1. Simple explicit decision

**Input**

    [2026-10-01T10:03:00Z] Alice: We decided to use Vendor A for the 2026 launch.

**Expected candidate:** DECISION, subject 2026 launch, relation uses vendor, value Vendor A, event time and valid start from the explicit adoption time, canonical relation, high calibrated support.

**Lifecycle/provenance:** exact message span and content hash verify; policy may batch-authorize it; one DecisionRecord is stored with its source episode and evidence ref.

**Query:** current 2026 launch / uses vendor returns Vendor A and cites Alice's exact message.

### 2. Later supersession

**Input**

    [2026-10-03T16:20:00Z] Bob: After the security review, Vendor B replaces Vendor A for the 2026 launch.

**Expected candidate:** explicit decision, same subject/relation slot, value Vendor B, valid start 2026-10-03T16:20:00Z, linked supersession proposal to the verified Vendor A record.

**Lifecycle/provenance:** policy verifies chronology and target; the materializer links the new record with supersedes_id and closes the old interval at the replacement time. Both records retain their own verified source spans.

**Query:** current returns Vendor B; an as-of query before the replacement returns Vendor A; history shows the replacement edge.

### 3. Proposal is not a decision

**Input**

    [2026-10-04T09:00:00Z] Alice: I propose Vendor C for the next launch review; we have not decided yet.

**Expected candidate:** PROPOSAL with exact provenance; no authoritative DecisionRecord and no supersession.

**Lifecycle/provenance:** candidate is non-authoritative supporting evidence.

**Query:** current decision remains Vendor B; a source-evidence search may surface the proposal labeled as such.

### 4. Ambiguous statement

**Input**

    [2026-10-05T09:00:00Z] Bob: Vendor C could replace Vendor B sometime next quarter.

**Expected candidate:** tentative PROPOSAL or REVIEW_REQUIRED; the effective date is unresolved and relation confidence is not sufficient for activation.

**Lifecycle/provenance:** keep staged with TEMPORAL_UNRESOLVED; existing memory remains unchanged.

**Query:** current remains Vendor B. Any surfaced evidence is labeled tentative and includes Bob's exact span.

### 5. Repeated ingestion is idempotent

**Input:** reprocess the exact artifact from scenario 1 with the same parser/extractor/prompt/catalog fingerprints.

**Expected candidate:** same stable extraction/candidate identity or a DUPLICATE result.

**Lifecycle/provenance:** no additional authoritative record, no duplicate evidence row, and the prior artifact version/hash is reused.

**Query:** one current Vendor A record remains.

### 6. Conflicting sources

**Input**

    [2026-10-06T11:00:00Z] Alice: We selected Vendor C for the 2026 launch.
    [2026-10-06T11:01:00Z] Bob: The decision is still Vendor B; no replacement was approved.

**Expected candidate:** two conflicting claims for the same slot with distinct speaker and span provenance.

**Lifecycle/provenance:** CONFLICT_REQUIRES_REVIEW; neither claim changes current memory until reviewed.

**Query:** current remains Vendor B and the conflict is exposed as unresolved, not silently resolved by timestamp or model confidence.

### 7. Future-effective decision

**Input**

    [2026-10-20T12:00:00Z] Alice: Starting 2026-11-01, Vendor C replaces Vendor B for the 2026 launch.

**Expected candidate:** explicit decision with event time Oct 20 and valid_from Nov 1; the two times remain distinct.

**Lifecycle/provenance:** stage as a future-effective candidate. Before Nov 1, current and as-of queries still return Vendor B. At/after Nov 1, deterministic due-candidate activation materializes Vendor C and preserves Vendor B's prior interval.

**Query:** Oct 25 returns Vendor B; Nov 2 returns Vendor C; both cite their respective source spans.

### 8. Extraction correction

**Input:** the scenario 2 source remains unchanged. A prior extractor version incorrectly emitted Vendor A; a corrected, schema-valid extraction emits Vendor B from the same exact source span.

**Expected candidate:** new extraction fingerprint; corrected candidate links to the prior candidate/record as an extraction correction, not as a new business decision.

**Lifecycle/provenance:** preserve the old run and record; mark the incorrect candidate invalidated or dependent memory for revalidation; authorize and materialize the verified correction with an auditable correction event.

**Query:** after correction, current returns Vendor B with the same source evidence span; no second provider call occurs when replaying the already persisted corrected response.

## Evaluation plan

First evaluate semantic ingestion, not end-to-end QA accuracy. Build a small human-annotated synthetic corpus with explicit spans, decision types, relation labels, event times, valid-time intervals, and supersession edges. Freeze the annotation version before comparing extractor configurations.

| Metric | Definition |
|---|---|
| Extraction precision | Emitted eligible claims that are supported by the labeled source evidence. |
| Extraction recall | Labeled decisions/facts that were emitted as valid candidates. |
| Relation accuracy | Correct canonical relation among candidates with a relation label. |
| Temporal accuracy | Separate exact/interval accuracy for event time and valid time; unresolved cases are reported, not imputed. |
| Provenance validity | Candidates whose span, source hash, and quote verify against the correct artifact version. |
| Supersession accuracy | Correct predecessor and effective-time boundary among labeled changes. |
| False-authoritative rate | Non-decisions, unsupported claims, proposals, or unresolved claims that reached authoritative memory. Target is zero for the synthetic acceptance suite; report confidence intervals on larger sets. |

Report denominators, review rate, auto-materialization rate, duplicate rate, and all failure reason counts. Do not improve aggregate recall by relaxing provenance, relation, or false-authoritative gates. End-to-end QA evaluation comes only after ingestion behavior is stable and separately frozen.

## Failure behavior

The stable reason codes are specified in the Failure taxonomy section. Provider transport failures remain distinct from malformed structured output, policy review, and local materialization rejection. Accepted provider output must be durably recorded with its response hash and known usage before validation. A local failure after acceptance must not be reported as a Provider error or replayed as a new request.

## Mainstream architecture mapping

- **Information/structured extraction:** the model converts source spans into typed candidates that are locally validated.
- **Event extraction:** timestamped source messages yield events and claims; source event time is distinct from the interval in which a decision applies.
- **Knowledge-graph construction:** subject/relation/value records form a constrained relation index. This does not make LinkLoom a full Knowledge Graph system: V1 has no open ontology induction, graph-wide inference, or general graph query language.
- **Temporal knowledge ingestion:** accepted claims carry valid-time intervals and verified supersession links.
- **Agent memory consolidation:** evidence moves through candidate, policy, review, and materialized memory states; the state transition is auditable and replay-safe.

## Interview value

The earlier evaluation path began with benchmark-supplied structured facts and then measured Temporal Memory and retrieval. Semantic Ingestion makes the product path explicit:

    raw enterprise artifact
      -> semantic extraction with source spans
      -> governed temporal memory
      -> schema-constrained query decomposition
      -> deterministic multi-hop retrieval
      -> context assembly and cited Reader

That story demonstrates a real Agent/FDE boundary problem: model extraction is probabilistic, while provenance checks, relation execution, lifecycle changes, and retrieval are locally validated or deterministic. Evidence can show where confidence ends, what was reviewed, why a record became current, and how it can be corrected. It is a more credible product architecture than a benchmark-shaped parser, without claiming that V2 already ingests arbitrary enterprise data.

## Relationship to external benchmarks

RHELM and DECADE do not set the product schema or acceptance cases. Once Semantic Ingestion is implemented and frozen, compatible raw-conversation formats may support supplementary development or interoperability evaluation. Because prior RHELM sample answer values were exposed in browser inspection, RHELM must not be described as a strict untouched held-out set under the previous zero-exposure rule. A separately isolated unseen portion would need its own evidence and protocol. No benchmark-specific examples or Gold values are used by this SPEC.

## Implementation Readiness Gate

Reviewed and approved on 2026-10-07 for Step 3 implementation:

1. The source and candidate contracts above define a non-authoritative extraction boundary.
2. The user explicitly authorized the Step 3 boundary; the authorization model does not fabricate human identity.
3. The future-effective query-time validity rule and relation-scoped `subject_key` migration are defined in the SQLite architecture note.
4. The eight synthetic scenarios have objective expected records, workflow states, provenance, and lookup outputs.
5. Root product contract and roadmap now identify Team Decision Recovery as current and preserve Scanner as historical infrastructure.
6. The plan contains three independently testable steps and excludes changes to V2 retrieval algorithms.
7. Candidate persistence, workflow events, immutable receipts, workspace scoping, and atomic materialization are defined before implementation.

The 2026-10-08 release report records the focused offline regression and
acceptance evidence for the implemented Step 3 boundary. The separate precommit
report records the current deterministic release gate and commit review status.

## Learning reflection

- **Role:** Worker
- **Files reviewed:** decision memory models/materializer/policy/store/source registry, provider and TeamDecision contracts, MemoryAgentBench adapter, relation planner/traversal, ContextAssembler, tests, V2 qualification and external-heldout report.
- **What changed:** product contract, approved Step 3 boundary, and the reviewed SQLite persistence/migration decision were aligned before production changes.
- **What the evidence says:** current DecisionMemory tables and transaction behavior are reused; current-time lookup must evaluate valid-time intervals to represent future-effective records safely; default automatic policy remains disabled without calibration.
- **Next step:** implement only the approved Step 3 workflow and verify it on synthetic fixtures before independent review.
