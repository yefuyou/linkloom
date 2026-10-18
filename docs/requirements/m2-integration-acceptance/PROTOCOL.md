# M2 integration and final acceptance protocol

Approved boundary: the user's 2026-10-18 integration request, original
`M2_RELATION_DESIGN.md`, `M2_IMPLEMENTATION_PLAN.md`, `M2_EVAL_SPEC.md`, and the
existing B/C/P4 SPECs. This is integration and acceptance, with no new Agent,
Provider, model adapter, dependency, real vault access, or semantic redesign.

## Integration

Baseline `eee882f4c676e900ee8895ac5b09f5fb2b3e6e36`. B and C each have parent
`af001a4d968676c30979254ac70dfb750e8f9ad6`; their single commits add disjoint
paths. Cherry-pick only `98e6fde` and `526d59e`; preserve the P4 baseline.
Only production adjustment: strip surrounding whitespace from a work-reference
target fragment. C02 first fails with UNKNOWN in both proof directions; after
normalization only its actual proving direction is SUPPORTED. C10 remains a
known discovery miss, never a negative relationship.

## Freeze and isolation

Before any H01-H04 relation execution: independently review materialized public
inputs, evaluator-only Gold, exact M1 observations and source snapshots; commit
the production implementation and acceptance runner/scorer; record its commit,
all source/runner/input/snapshot hashes, rule versions, Gold hash, and protocol.
Commit FREEZE.json separately before predict. LF-normalized SHA256 is used for
cross-checkout text reproducibility; actual RawArtifact content hashes continue
to cover exact source content, with Unicode offsets calculated by M1.

The prediction process imports neither fixture authoring nor scorer. It opens
only public inputs, the freeze, and frozen implementation files. Gold hashing
is evaluator-only. `materialize` constructs M1/source evidence and never invokes
relation rules. `predict` writes four case predictions before `score` reads
Gold. H04 phases omit future sources, preserve old immutable bytes, give stale
locators the current registry version, and use all current observations, not
only selected candidate endpoints. Fixtures are fresh synthetic copies of the
published design. These are **author-visible retained acceptance cases**, not
external blind testing. If exposed failures lead to a future change, the rerun
must be labeled an exposed regression; original results remain immutable.

## Predeclared scoring

Per original SPEC: fixed endpoints and complete evidence are evaluated
separately from candidate recall. A positive must match typed endpoints,
predicate, scope/snapshot and necessary evidence; an invalid positive is FP
and also FN for a Gold positive. Zero denominators are null. No unannotated
graph space is treated as negative. Exact positive/negative proof fragments and
source requirements are in Gold. Identity checks use canonical endpoint pairs;
revisions retain direction. Source bytes, hashes, quote ranges, and canonical
assessment records are checked independently by the scorer.

H01: S+, R+, missing owner/year; NOT_COMPARABLE/null. H02: I+, I-, I? with
SOURCE_CLAIM_ONLY and failed formal authority. H03: S+, R CONFLICT, BLOCKED/null.
H04: s0 S+/R+, BEFORE 20→22/+2; s1 old S/R stale and unpublished, new S+/R-,
NOT_APPLICABLE/null; s2 new outcomes unchanged, old R RETRACTED and dependent
comparison blocked. s1 and s2 are distinct snapshots, including two new-S
positives. Old s1 S/R and s2 R are additional unpublishable atoms. Thus holdout
positive denominators are S=5, R=2, I=1; unsupported predicates have N/A coverage.
Revision Accuracy has five questions (H01, H03, H04 s0/s1/s2); original Dev has
five (D01, D04 B→A/C→B, D05, D07). Additional exposed V cases are supplementary.
COMPUTABLE accuracy separately checks old/new date, DAY precision, BEFORE bound
and signed delta. Null never becomes zero. UNKNOWN/CONFLICT/REFUTED states,
false merge, unsupported negative/causal assertion, formal authority and stale
publication safety are reported separately.

Acceptance gates remain original SPEC §6.5. Pytest counts are engineering
regression evidence; they cannot establish semantic acceptance. Record every
failed stage and actual output without tuning after exposure.

## Delivery and Git

Use one independent read-only Reviewer for integration and pre/post acceptance
review. Preserve failure attribution and structured actual demonstrations.
Commit integration on `codex/linkloom-m2-integration-acceptance`. Immediately
before publishing, re-read origin's target HEAD; it must still be `eee882f`.
Push normally with no force to `codex/linkloom-m2-progress` only if that check
and ancestry allow a fast-forward. Never update the existing A worktree or
create a PR. Stop after delivery; M3 production adapter remains absent.
