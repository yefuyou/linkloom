# M3-F TaskObservation discovery SPEC

The 2026-10-19 user request approves this boundary. Base:
`31dde91f0e9b6015abc5754e995692a2f1db89ec`; isolated branch/worktree:
`codex/linkloom-m3-task-discovery`.

## Contract and architecture

Add an independent M1 scheduling module only. Host supplies immutable RawArtifacts,
an expected workspace, a SourceReferenceRegistry and a read-only current-source
loader. No source enumeration, automatic registration, storage mutation or runtime
integration is introduced. Reuse ProviderNeutralSemanticExtractor (one attempt),
SemanticExtractionResult, TaskProposal and build_task_observation unchanged.

One admitted document is one exact bounded context window, including original
message headers. Canonical parse_source_text segments remain the sole provenance
of observations. The model supplies task work and optional owner/project/vendor/
literal deadline. Cross-message proposals are projected onto canonical message
fragments and revalidated separately: fields never migrate between messages.
An explicit model grouping is supporting-only, not business identity. Shared
owners, projects or dates never cause merging. Missing cross-message bindings are
reported as partial; no new M1 validation rules are substituted.

Before and after extraction verify current source identity/hash and every
canonical evidence registration/version/hash. Missing/stale/revoked/foreign
sources do not yield released observations. Limits bound inventory, characters,
UTF-8 bytes and calls. Oversized sources are not silently truncated. No retry or
implicit cache. Exact duplicate inputs/proposals are reported; conflicting
proposals at the same immutable task location cannot silently overwrite one another.

Only PLAN/ACTION claims with grounded nonempty task descriptions may become
observations. A conservative veto checks the full source sentence around task
work for suggestions, hypotheses, quotations, negation and cancellation; it only
rejects, never discovers tasks. It is not a general semantic classifier. Ambiguous
or unsupported cases stay failed/partial rather than authoritative tasks.

## Coverage and output

Declare `bounded-document-single-pass/v1` before extraction. It checks the entire
admitted document once; parsing issues, failures, claim saturation and skipped
sources prevent complete policy coverage. Successful empty output means
NO_TASKS_DETECTED, never NO_TASKS_EXIST. Even complete policy coverage is not a
proof of semantic exhaustiveness. Report valid observations, partial observations,
failed extractions/proposals, unprocessed sources, model groupings, exact receipts
and coverage separately. All observations remain SUPPORTING_ONLY / UNCONFIRMED.

## Fixed acceptance

1. FakeModel exercises the real structured tool contract before any live request.
2. New public synthetic cases cover zero/one/multiple tasks, cross-message work,
   omitted and first-person owners, missing deadline year, mixed plan/correction,
   discussion, cancelled/suggested/quoted/hypothetical/negated work, and distinct
   tasks sharing project/owner/day.
3. Source updates, unavailable old versions, cross-workspace/unregistered sources,
   incomplete parsing, duplicates, malformed output and oversized input have
   explicit outcomes and exact evidence checks.
4. At most three small new real requests if credentials and provider conditions
   permit; otherwise NOT_RUN. No Gemini 2.5 default, no old holdout tuning.
5. Separately report task recall/precision, field accuracy, evidence validity,
   calls/usage/failure causes. Fake results prove contracts, not model quality.
6. Deliver five raw meeting messages -> automatic output without providing Gold
   or task endpoints to the model; reproducible evidence, DELIVERY.md, independent
   read-only REVIEW.md and fixes. Exact stage/commit/normal push and remote SHA
   verification, then stop. No merge/PR or changes to other worktrees.

## Non-goals

No Coordinator/RuntimeAdapter/ModelLoop, ContextAssembler, semantic_context.py,
semantic_analysis.py, existing extractor, evals/m3_task_impact data/Gold/scorer or
P1-P4 edits. No real vault, new dependency, formal decision authority, deployment,
business task resolution or source mutation.
