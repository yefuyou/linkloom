# Integration and push record — 2026-09-10

## Authorization and destination

The user authorized dependency-ordered commits, pushes, and supporting
documentation on 2026-09-10. Earlier task-specific no-commit/no-push statements
remain historical execution boundaries; this authorization permits publishing
the existing work for review. It does not authorize merging master, running a
real provider, or accepting pending data reviews.

Starting remote refs verified through GitHub:

- master: e5652b4cc1c8df0a57f987c7b6504a7708af9976
- feature/p8-agent-runtime: 3000056d7abe7740ecf58fa85ed02a9ab7b65724
- No existing PR was returned by either GraphQL or REST.

Destination: `codex/integration-20260910`, based on the existing P8 commit.
The original feature branch and master remain unchanged. Commits are stacked
in dependency order; later batches include their ancestors.

## Commit batches

| Batch | Scope | Acceptance boundary |
|---|---|---|
| 1 | Master V2 and planning navigation | Accepted planning baseline; no production change |
| 2 | M0.1–M0.4 offline Runtime, corresponding tests and records | M0.4 task records human acceptance of M0 offline; Gate A remains partial |
| 3 | Trajectory evaluator, 41 cases, fixtures and tests | Existing feature-level independent acceptance; full-suite limitations retained |
| 4 | Team Decision seed, 30 cases and 36 notes | Corrected data awaiting focused independent re-review; not accepted as eval seed |
| 5 | Optional SDK dependency and manual smoke harness | Offline preparation only; real-provider smoke NOT_RUN |

Batch 2 intentionally combines the accumulated Runtime edits. M0.1–M0.4
overlap in graph, adapter, retrieval and durability code, and no intermediate
commits captured their accepted snapshots. Splitting by filename or guessing
historical hunks would create unverified intermediate states. The component
SPECs and regression tests retain the individual behavioral boundaries.

The M0.4 task record contains both offline Runtime acceptance and later smoke
preparation history. Publishing that record in batch 2 does not mean batch 5
files are present until batch 5 lands.

## Validation and merge policy

Validate exported committed trees with their own `src` on PYTHONPATH so local
untracked modules cannot make an incomplete batch pass. Run relevant offline
Runtime, evaluator, seed and smoke checks; exclude the opt-in real-provider
test explicitly. Record results below as they become available.

No current product-test CI run was found on GitHub. The only existing Actions
record was an older Dependency Graph run. Local focused checks do not imply
full-suite success or CI acceptance. Historical full-suite failures involve
legacy external paths and Windows temporary-directory access.

Merge remains a separate decision after reviewing the final commit range and
its evidence. Seed re-review and Gate A real-provider evidence remain visible
open items; neither is silently converted to acceptance by a push.

## Exclusions

Do not stage `.tmp/`, `src/test.md`, `tasks/`, generated egg-info changes,
credentials, host environment files, or real-vault artifacts. These pre-existing
local files are preserved. Validation exports are disposable local artifacts.

## Current verification

Pending execution against the batch commits. This record is updated with
actual results before the final publication handoff.
