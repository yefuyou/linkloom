# Vault Steward DEV_SPEC

This file is the build contract. It turns the product-level [SPEC.md](SPEC.md)
into phases, tasks, files, acceptance criteria, and checks.

No implementation task may start unless it has a named section in this file or a
feature-specific `docs/requirements/<feature>/SPEC.md` and
`implementation_plan.md`.

## Phase A: Governance And Project Contract

Status: in progress.

Goal: establish the rules that future implementation must obey.

### A1: SPEC-first project skeleton

Files:

- `README.md`
- `SPEC.md`
- `DEV_SPEC.md`
- `AGENTS.md`
- `docs/README.md`
- `docs/MULTI_AGENT_ROLES.md`
- `docs/SPEC_DRIVEN_DEVELOPMENT.md`
- `docs/ACCEPTANCE_CHECKLIST.md`
- `docs/PROJECTS_REVERSE_ENGINEERING.md`
- `docs/MARKET_LANDSCAPE.md`
- `docs/GIT_WORKFLOW.md`
- `.gitignore`

Implementation points:

- Define the project mission and non-goals.
- Require SPEC before implementation.
- Require Planner, Worker, Reviewer separation.
- Forbid real-vault mutation without reviewed dry-run and exact human approval.
- Record reference-project development patterns.
- Record market landscape and differentiation.
- Establish Git restore-point rules.

Acceptance:

- All listed files exist.
- No business code exists yet.
- No obsolete alternate acronym remains.
- Docs consistently use `SPEC`.
- Docs define Planner/Worker/Reviewer separation.
- Docs define objective acceptance checks.
- Docs mention that `D:\programblog` is dogfood, not a hard-coded product assumption.
- Market landscape is available as a project doc, not only inside a skill.
- Git workflow defines commit boundaries and restore-point policy.

Checks:

```powershell
Get-ChildItem D:\webproject\vault-steward -Recurse
A repository-wide text search for the obsolete alternate acronym returns no matches.
```

## Phase B: Read-Only Vault Scanner

Status: not started.

Goal: scan a fixture vault and optionally a user-provided real vault without
changing files.

Planned files:

- `src/vault_steward/scanner.py`
- `src/vault_steward/types.py`
- `tests/fixtures/sample_vault/`
- `tests/unit/test_scanner.py`
- `docs/requirements/read-only-vault-scanner/SPEC.md`
- `docs/requirements/read-only-vault-scanner/implementation_plan.md`
- `docs/requirements/read-only-vault-scanner/task.md`

Acceptance draft:

- Reads Markdown files.
- Extracts path, title, headings, frontmatter, wikilinks, tags, size, and mtime.
- Does not write to the input vault.
- Works on a synthetic fixture vault.
- Emits deterministic JSON.

No implementation may begin until the Phase B feature SPEC and implementation plan are
approved.

## Phase C: Integrity Auditor

Status: not started.

Goal: detect empty pages, suspiciously short pages, duplicate titles, and
possible migration content-loss cases.

No implementation may begin until Phase C SPEC materials exist.

## Phase D: Taxonomy And Link Planner

Status: not started.

Goal: propose hierarchy and cross-link improvements with evidence paths.

No implementation may begin until Phase D SPEC materials exist.

## Phase E: Dry-Run Mutation Planner

Status: not started.

Goal: generate proposed diffs or move plans without applying them.

No implementation may begin until Phase E SPEC materials exist.

## Phase F: Permissioned Apply And Audit Log

Status: not started.

Goal: apply approved plans and write audit logs.

No implementation may begin until Phase F SPEC materials exist.

## Phase G: Agent Harness

Status: not started.

Goal: compose tools and workflows into a stateful assistant.

No implementation may begin until Phase G SPEC materials exist.
