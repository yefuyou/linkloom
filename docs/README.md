# Vault Steward Docs

This docs directory separates product intent, architecture constraints,
feature-specific requirements, and development method.

## Core Contracts

- [../SPEC.md](../SPEC.md): product-level mission, scope, non-goals, policies.
- [../DEV_SPEC.md](../DEV_SPEC.md): phase-by-phase build contract.
- [../AGENTS.md](../AGENTS.md): hard rules for AI contributors.

## Process Docs

- [MULTI_AGENT_ROLES.md](MULTI_AGENT_ROLES.md): Planner, Worker, Reviewer roles.
- [SPEC_DRIVEN_DEVELOPMENT.md](SPEC_DRIVEN_DEVELOPMENT.md): required task sequence.
- [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md): objective completion checklist.
- [PROJECTS_REVERSE_ENGINEERING.md](PROJECTS_REVERSE_ENGINEERING.md): patterns copied from reference projects.
- [MARKET_LANDSCAPE.md](MARKET_LANDSCAPE.md): adjacent tools and product differentiation.
- [GIT_WORKFLOW.md](GIT_WORKFLOW.md): commit and restore-point policy.

## Future Architecture Docs

These files are intentionally not created yet. They should be added before their
corresponding implementation phases:

- `architecture/overview.md`
- `architecture/data-model.md`
- `architecture/vault-scanning.md`
- `architecture/policy-and-permission.md`
- `architecture/dry-run-and-audit.md`
- `architecture/agent-harness.md`

## Future Feature Requirements

Each non-trivial feature should use:

```text
docs/requirements/<feature-name>/
  SPEC.md
  implementation_plan.md
  task.md
```

`SPEC.md` is the design contract. `implementation_plan.md` is the executable
handoff. `task.md` is the factual progress ledger.
