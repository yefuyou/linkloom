# M2 — Bounded task relation candidate discovery

Authorized boundary: the task B request on 2026-10-18, based on
`af001a4d968676c30979254ac70dfb750e8f9ad6`. Work only on
`codex/linkloom-m2-candidate-discovery` in its independent worktree.

## Behavior and architecture

Given an explicitly workspace-authorized, bounded inventory of current M1
observations and immutable sources, return ranked investigation candidates for
SAME_TASK, RELATED_TO, AFFECTED_BY and REVISES_PLAN. Discovery does not assess
relations. It never constructs a RelationAssessment, writes a database, mutates
observations, or confirms a deadline change.

One independent module owns discovery. It reuses ObservationRef, EvidenceQuote,
ChangeRef, normalization, scoped task-key parsing, and source/observation
verification. TaskObservationQuery.list supplies observations; query rows can
be passed directly, with stale/conflicting rows excluded and read back again.
The caller owns obtaining the authorized current source inventory and registry.

Signals, strongest first: same explicitly scoped ticket; explicit file/task
reference; same project or a directly declared scoped alias; task/vendor match
to a source-claimed supplier change; bounded lexical overlap inside a known
project. Project-only co-occurrence is an intentionally weak investigation
lead. No owner/date shortcut, semantic Provider, graph closure or alias chain.
Revision hints require a revision cue and an endpoint reference/key, never
merely different dates. AFFECTED_BY uses existing observation -> ChangeRef
endpoint shape and preserves SOURCE_CLAIM authority.

Candidates contain version/payload-bound endpoints, predicate_hint, priority
(ordering only), exact discovery quotes, reason codes and uncertainty. They
carry DISCOVERED / UNCONFIRMED / SUPPORTING_ONLY, with no verdict. Symmetric
relations deduplicate both endpoint orders; revisions retain direction.
All input sizes, output count and per-endpoint fanout are bounded, deterministic
and reported. Invalid observations are excluded with diagnostics; oversized or
ambiguous source inventories fail explicitly. Revalidation repeats discovery
against current sources and requires the same candidate identity. Cached
candidates are never a source of truth.

The simplest alternative is exact normalized description matching across the
workspace; the offline comparison measures its omissions and cross-project
noise on precisely the same queries and budgets. No new dependency is needed.
Future pressure points are source inventory paging, lexical ranking, ambiguous
file links and broader language grammars. These are measured limits, not scope
for an Agent, persistence layer or new model contract in this task.

## Acceptance and checks

1. Discover scoped keys, explicit file/task references, projects/aliases,
   supplier-change investigation leads, and marked weak leads.
2. Deduplicate, isolate workspaces, enforce observable budgets, verify current
   source versions and reject altered/conflicting observations.
3. Source changes invalidate endpoint, alias and change-dependent candidates.
4. Independent synthetic workspace inputs and evaluator-only Gold are separate.
   Freeze predictions before reading Gold; compare baseline/new Recall@K,
   output/noise counts, explicit-reference misses and concrete bad cases.
5. Test same-name tasks across projects, unconnected tasks, ambiguous references,
   truncation and the original five-line safety boundary. No candidate confirms
   identity, impact, revision or delay; existing validators retain responsibility.
6. Focused M1/P1/P2/P3 unit regression only: no real Provider, old heldout,
   unrelated benchmark rerun, root DELIVERY edit or P4 file/schema changes.
7. Independent review, task-owned DELIVERY and reproducible sanitized results;
   commit and normal push to the authorized branch only. No merge or M3.

## Implementation order

Verify input contracts and key/reference discovery with failing tests, then add
project/alias/change signals and caps; build independent offline fixtures and
baseline comparison; run relevant regression; resolve independent review;
record evidence and push. User approval covers this internal loop.
