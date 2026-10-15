# Agent Rules For linkloom

Use milestone-driven development within the human-approved architecture and SPEC boundary.

The canonical user roadmap is `docs/PRODUCT_ROADMAP.md`. Use `SPEC` as the only
planning and acceptance term. Do not create a competing phase plan or preserve
an obsolete roadmap as an alternate source of truth.

## Hard Rules

1. Do not implement a feature before its SPEC and implementation plan exist and
   the human approves the active boundary.
2. Use independent acceptance review for milestone completion, major architecture changes, and permission or real-vault boundaries.
3. Do not use a real vault during early fixture-based development.
4. Do not expose write-capable tools through read-only commands.
5. Do not modify a real vault unless a reviewed dry-run exists and the human
   approves the exact plan, path, and operation.
6. Do not silently delete, rewrite, merge, rename, move, or tag user notes.
7. Do not hard-code private paths into reusable logic.
8. Do not treat AI-generated text, relation scores, or diagnoses as verified
   knowledge without source evidence.
9. Do not mark a task complete without objective acceptance evidence.

## Milestone Task Flow

```text
Planner -> SPEC / implementation plan / acceptance
Human -> approval or adjustment
Worker -> autonomous implementation and verification within approved scope
Reviewer -> independent milestone acceptance check
Main coordinator -> evidence summary and Git boundary
```

## Role Boundaries

- Planner writes planning artifacts only and does not approve them for the human.
- Worker implements only an approved task and cannot change its acceptance
  criteria after starting.
- Reviewer reports pass, fail, or blocked with evidence and does not silently
  fix the work being reviewed.
- The main Agent may handle routine implementation, tests, and refactoring; independent review remains required at the boundaries above.

## Vault Safety

Real-vault mutation remains forbidden until Milestone 6 is independently
accepted on synthetic fixtures.

Even after that milestone, a real write requires:

- a visible per-file dry-run;
- exact approval naming plan ID, vault path, and operation;
- validation that target files have not changed since preview;
- a recoverable backup;
- an audit log;
- a tested rollback path.

Generic permission such as "clean up my vault" is not exact approval.

## Objective Acceptance Evidence

A Reviewer must be able to point to:

- the governing roadmap milestone and feature SPEC;
- exact files changed;
- checks with pass, fail, or not-applicable status;
- sample fixture inputs and expected outputs;
- confirmation that no real vault was touched;
- remaining risks and skipped checks.

## Working Mode

Development is the default. Learning, career analysis, and interview coaching
are opt-in and must not interrupt an authorized implementation.

Determine current product state from the roadmap, feature contracts, actual
source, Git status, and tests. Keep temporary status and hard-coded gates out
of this file. The developer reading path is in [docs/README.md](docs/README.md).

Within an approved milestone, continue through internal tasks until acceptance
criteria pass or a real blocker or material architecture/scope decision arises.
Keep verification proportional, preserve unrelated changes, and report known
baseline failures separately from new regressions.

Do not commit, push, or change remote state unless the corresponding action is
authorized. New projects stay local or private by default.

Delete only reviewed files within the explicitly authorized cleanup scope, and
use the system Recycle Bin; if recycling is unavailable, preserve the files.
