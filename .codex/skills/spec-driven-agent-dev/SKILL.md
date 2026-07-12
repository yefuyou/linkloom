---
name: spec-driven-agent-dev
description: Guide SPEC-first development for linkloom and mature AI-assisted development workflows. Use for Planner/Worker/Reviewer process, milestone and feature SPEC planning, implementation gating, acceptance review, evidence-based review, read-only vault scanner planning, dry-run vault operation design, and any task that must separate planning, implementation, and review before changing code or vault content.
---

# Spec-Driven Agent Development

Use this skill to keep linkloom work evidence-backed, role-separated, and gated by approved specs.

## Required Reading

Before action, read the project contracts that exist in the repo:

- `SPEC.md`
- `DEV_SPEC.md`
- `AGENTS.md`
- `docs/PRODUCT_ROADMAP.md`
- `docs/MULTI_AGENT_ROLES.md`
- `docs/SPEC_DRIVEN_DEVELOPMENT.md`
- `docs/ACCEPTANCE_CHECKLIST.md`
- Relevant `docs/requirements/<feature>/SPEC.md` when present

If a required contract is missing, report it as a blocker or assumption before continuing.

## Workflow

1. Identify the active role: Planner, Worker, or Reviewer. Read `references/role-contract.md`.
2. Load only the reference needed for the step:
   - Feature planning: `references/feature-spec-template.md`
   - Acceptance review: `references/acceptance-review-template.md`
   - Vault operation planning: `references/vault-safety-policy.md`
   - Market positioning: `references/market-landscape.md`
3. Keep one source of truth for the feature under `docs/requirements/<feature>/`.
4. End each step by writing or returning a learning reflection using `references/reflection-template.md`.

## Role Gates

- Planner drafts the SPEC, implementation plan, task list, and acceptance criteria only.
- Worker implements only approved scope from an accepted SPEC/task. Do not expand scope silently.
- Reviewer checks artifacts and evidence only. Do not silently fix issues, self-approve, or merge review and implementation roles.

## Vault Safety

Never perform real vault mutation unless all of these exist:

1. reviewed dry-run output;
2. exact human approval naming the plan, vault path, and operation;
3. validation that target files have not changed since preview;
4. a recoverable backup created before writing;
5. an audit record;
6. a tested rollback path.

If any condition is missing, stop at planning or dry-run evidence.

## First Milestone Feature

The Read-Only Vault Scanner is the first feature slice of Milestone 1, not the
whole product. For `read-only-vault-scanner`, create these planning files first:

- `docs/requirements/read-only-vault-scanner/SPEC.md`
- `docs/requirements/read-only-vault-scanner/implementation_plan.md`
- `docs/requirements/read-only-vault-scanner/task.md`

Do not create scanner code before the human approves the SPEC and implementation plan.
