# M3-E SPEC / implementation boundary

The user's M3-E request is the approved milestone boundary, including normal
merge, commit and push. It supersedes historical release-freeze prose only for
this scope. No new live Provider, real vault, formal memory or frozen eval edits.

## Baseline

New independent worktree from D 31dde91f0e9b6015abc5754e995692a2f1db89ec,
normally merge C 9520362261f8f850191c0fa82f04bdc6452ddc05. Their common ancestor
is 26392432dcef70692461e6f2f50bc1bd5528f69a; changed file sets do not overlap.
Original branches/worktrees remain untouched. No reset/clean/rebase/force/PR.

## Contracts and minimal integration

C is an independent context library, D owns the existing runtime. Keep the
SingleAgentModelLoop, Coordinator, RetrievalAgent, ToolRuntime, Reviewer and
ModelArtifactStore. Add one opt-in host ContextRuntimeConfig for task_relations;
other workflows and unconfigured task_relations retain their compatibility path.

RuntimeAgentAdapter supplies the existing _assemble_context callback, extended
with C EvidenceGroup/subgoal/RequestUsage parameters. A runtime-owned request
preparer is invoked before new durable model requests, never over existing model
artifacts. It reuses C selection, WorkingContext, compact_working_context and
EvidenceRecovery. It is not a second loop or context database.

The host's authorized raw task/semantic snapshots and declared typed questions
provide observable endpoints and source spans. Existing TaskPlan contracts label
operational subgoals; they do not determine semantic answers. Shared source/bridge
requirements are merged into disjoint atomic groups. All declared originals and
competing observations are protected conservatively; missing/oversized groups
remain explicit INCOMPLETE/BLOCKED. No Gold, expected answer or phrase classifier.

## Source identity and recovery

Add a read-only host source interface on TaskRelationTools (workspace + opaque
version identity only). Re-read current authoritative snapshots on every recovery.
Validate workspace, declared authorization, source registry/version/hash, parser
completeness, original Unicode ranges, revoked sources/endpoints and lifecycle.

Adapt validated exact source spans using the existing canonical Runtime passage
identity function and C EvidenceRef.from_snapshot. Persist the full explicit
mapping (original semantic evidence ID, Runtime passage ID, C reference, original
artifact version); do not translate IDs by string manipulation. C integer source
version is 1 for each immutable artifact-version resource, with the original
version string separately preserved. A changed artifact version cannot reuse it.

Summaries have operational hint authority only. Required quotes are re-read and
validated before they enter C selection/model requests or support an answer.
P4/semantic receipts are rechecked independently; a revoked relation or changed
source cannot be made current by a summary. No historical recovery is enabled in
this integration; history remains labelled in P4 results and cannot prove current
business relationships.

## Persistence, recovery and compaction

WorkingContext epochs, mappings, raw ledger/checkpoint snapshots and audit records
are write-once JSON artifacts beside existing checkpoints in ModelArtifactStore.
RecordRef hashes bind exact immutable copies of the real Runtime state/ledger.
The original ledger and model artifacts are never compressed or overwritten.
Cold recovery follows the last durable request's hash-bound context audit,
validates run/policy/goal, raw record identity and current sources before reuse.
Ambiguous Provider/tool outcomes continue to use the existing Runtime's fail-closed
recovery; never reissue work merely to reconstruct context.

Only old tool interaction/history projections and duplicate operational process
text may be compacted. Protect goal, progress, unresolved questions, failures,
conflicts, evidence refs and raw record refs through C's guarded compactor.
Use deterministic injected Fake Compactor only. Trigger on actual configured
request pressure or existing history-cap pressure, report before/after and every
history omission, recover originals and re-preflight. Missing originals or no room
must block before another model request. No silent unique-evidence deletion.

## Budget contract

Retain existing 8-result/24 KiB verified-evidence, 16-result/128 KiB tool-history,
and 32 KiB user/96 KiB normalized request bounds. ContextPolicy may tighten these
limits, never remove them.
Reserve output tokens and configured request envelope; separately report tool
declarations, latest result, verified source text/mapping, protected WorkingContext,
operational summary, old history and serialization overhead. Feed measured other
request allocation to C RequestUsage, then measure the complete actual prepared
ModelTurnRequest plus configured envelope. Token counts use an injected offline
tokenizer/counter identity; no heuristic is called actual Gemini usage.

## Acceptance / ordered implementation

1. Contract-first source mapping/recovery tests, then minimal bridge implementation.
2. Runtime-owned durable context/preflight/compaction hook with focused tests.
3. Existing Runtime end-to-end Fake/REPLAY: long history auto-compacts; preserves
   goal/unresolved work; cross-file revision and counterevidence; causality recovery
   across turns; stale/revoked/forbidden/invalid spans fail; revoked relations do
   not revive; cold resume does not duplicate charged/tool work; original ledger,
   checkpoints and model artifacts remain hash-consistent; no-space safely blocks.
4. Relevant C/D/P4 and ask/connect/team_decision regression, classifying inherited
   baseline failures. Keep C synthetic coverage metrics historical only. No holdout,
   new Provider or production business-success claims.
5. Reproducible sanitized trace/audit/source/coverage/budget exports, independent
   read-only review of loss/authorization/checkpoint/duplicate-request/compatibility,
   DELIVERY.md and REVIEW.md, precise stage/commit/normal push and remote SHA check.

Known prior boundary: D documents safe failure between terminal Tool Ledger and
observation commit; successful observation-durable resume is distinct. Do not
claim universal recovery or expand into unrelated Runtime recovery redesign.
