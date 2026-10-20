# linkloom DEV_SPEC

This is the implementation gate map for the product roadmap. The detailed user
contract lives in [docs/PRODUCT_ROADMAP.md](docs/PRODUCT_ROADMAP.md).

No implementation task may start unless it has an approved feature SPEC and
implementation plan under `docs/requirements/<feature>/`.

## Governance Baseline

Status: Team Decision Recovery / Temporal Decision Intelligence is the current
product direction. Semantic Ingestion Steps 1–3 are implemented in the current
source tree, and the bounded V1 browser journey was validated with real Gemini
on 2026-10-08. V1 feature work is frozen; remaining work is release curation,
packaging, and documentation.

Existing governance artifacts:

- `SPEC.md`
- `DEV_SPEC.md`
- `AGENTS.md`
- `docs/PRODUCT_ROADMAP.md`
- `docs/MULTI_AGENT_ROLES.md`
- `docs/SPEC_DRIVEN_DEVELOPMENT.md`
- `docs/ACCEPTANCE_CHECKLIST.md`
- `docs/PROJECTS_REVERSE_ENGINEERING.md`
- `docs/MARKET_LANDSCAPE.md`
- `docs/GIT_WORKFLOW.md`

Current V1 release gate:

- Do not start new feature work or implement V1.1 backlog items.
- Do not modify the V2 planner, retrieval/traversal, ContextAssembler, Reader,
  benchmark data, or scoring protocol as part of release curation.
- Do not rerun the live Provider acceptance during deterministic release
  verification.

Current feature documents:

- `docs/requirements/semantic-ingestion/SPEC.md`
- `docs/requirements/semantic-ingestion/implementation_plan.md`
- `docs/architecture/SEMANTIC_INGESTION_SQLITE_MIGRATION.md`

The Planner, Worker, and Reviewer roles remain separate. The Step 3 Worker
must provide test and regression evidence; a separate Reviewer checks the
result before acceptance.

## Historical Scanner-Era Roadmap (Preserved)

The following Can Read / Can Find / Can Connect / Can Organize / Can Act /
Permissioned Vault Mutation sections preserve the earlier personal-vault
roadmap. They are historical planning, not the active LinkLoom product
direction. The completed Scanner remains an infrastructure foundation. Vault
Profile and the old search/organization/mutation milestones are not active
implementation gates for the current Semantic Ingestion slice.

## Historical Milestone 1: Can Read

Status: Implemented and independently reviewed on a synthetic fixture.

Completed feature slice: Read-Only Vault Scanner.

Vault Profile was an optional planning slice in the earlier roadmap. It is not
the next active LinkLoom feature.

Historical planning files for the completed Scanner:

- `docs/requirements/read-only-vault-scanner/SPEC.md`
- `docs/requirements/read-only-vault-scanner/implementation_plan.md`
- `docs/requirements/read-only-vault-scanner/task.md`

Implemented file boundary:

- `pyproject.toml`
- `src/linkloom/`
- `tests/fixtures/sample_vault/`
- `tests/unit/test_scanner.py`

Required gate evidence:

- sample-vault command and expected artifacts are explicit;
- scan data contract is explicit;
- Chinese path and Obsidian syntax cases are explicit;
- deterministic-output check is explicit;
- before-and-after vault hash check is explicit;
- symbolic-link and malformed-input behavior are explicit;
- no real-vault acceptance case exists.

`Data contract`:
The exact fields and rules that every generated note record must follow.

## Historical Milestone 2: Can Find

Status: Not started.

Candidate feature: Cited Note Search.

Implementation may start only after Milestone 1 produces a stable note index
and the feature SPEC defines retrieval, citations, evaluation questions,
privacy behavior, and no-evidence handling.

## Historical Milestone 3: Can Connect

Status: Not started.

Candidate feature: Explainable Note Relations.

Implementation may start only after Milestone 2 establishes addressable
passages and retrieval evaluation. The feature SPEC must distinguish explicit
links from suggested relations and forbid automatic link insertion.

## Historical Milestone 4: Can Organize

Status: Not started.

Candidate feature: Vault Health Diagnostics.

Implementation may start only after diagnostic rules, evidence requirements,
configurable exclusions, and false-positive evaluation are specified.

`False-positive evaluation`:
A check of how often the system flags an intentional note as a problem.

## Historical Milestone 5: Can Act

Status: Not started.

Candidate feature: Action Continuity Inbox.

The initial slice extracts explicit tasks, unresolved questions, and stated next
actions with provenance. Its long-term product responsibility is broader: help
users turn evidence into goals, tasks, or experiments; show knowledge
dependencies and unresolved information; and connect outcomes, feedback, and
reflection back to their source knowledge.

Implementation may start only after the feature SPEC defines task types, source
preservation, deduplication, confidence display, the boundary between
user-authored tasks and AI suggestions, and which planning behavior is only a
proposal rather than a commitment.

## Historical Milestone 6: Modify Only After Confirmation

Status: Not started; all real-vault writes forbidden.

Candidate feature: Permissioned Vault Mutation.

Implementation may start only after the feature SPEC defines:

- immutable plan identity;
- dry-run format;
- exact approval record;
- stale-plan rejection;
- backup and rollback contract;
- partial-failure behavior;
- audit log schema;
- synthetic mutation fixtures and destructive tests.

`Stale plan`:
A preview that is no longer safe to apply because one of its target files has
changed since the preview was generated.

## Cross-Milestone Engineering Gates

Every implementation slice must:

1. use a synthetic fixture before private data;
2. preserve Planner, Worker, and Reviewer separation;
3. list exact changed files and checks;
4. keep read-only and write-capable modules separate;
5. add evaluation evidence proportional to user-facing risk;
6. create a Git restore point only after independent review passes the accepted
   boundary.

An Agent harness is not a standalone final milestone. If routing or multi-step
execution becomes necessary, it is introduced inside the milestone whose user
workflow needs it and tested against that milestone's acceptance criteria.
