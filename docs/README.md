# linkloom Docs

This directory separates product intent, implementation gates, architecture
constraints, feature requirements, and development method.

## Start Here

- [PRODUCT_ROADMAP.md](PRODUCT_ROADMAP.md): the one authoritative user-facing
  roadmap, from Can Read through confirmed mutation.
- [../SPEC.md](../SPEC.md): product mission, principles, system boundaries, and
  non-goals.
- [../DEV_SPEC.md](../DEV_SPEC.md): milestone-to-implementation gate map.
- [../AGENTS.md](../AGENTS.md): hard rules for AI contributors.

## Process Docs

- [MULTI_AGENT_ROLES.md](MULTI_AGENT_ROLES.md): Planner, Worker, Reviewer roles.
- [SPEC_DRIVEN_DEVELOPMENT.md](SPEC_DRIVEN_DEVELOPMENT.md): required task sequence.
- [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md): objective completion checklist.
- [PROJECTS_REVERSE_ENGINEERING.md](PROJECTS_REVERSE_ENGINEERING.md): patterns learned from reference projects.
- [MARKET_LANDSCAPE.md](MARKET_LANDSCAPE.md): adjacent tools and product differentiation.
- [GIT_WORKFLOW.md](GIT_WORKFLOW.md): commit and restore-point policy.

## Just-In-Time Architecture Docs

Architecture documents are added only when an approved milestone needs them.
Likely topics include:

- note index and scan boundary;
- passage and citation data model;
- relation ranking and explanation;
- health policies and evaluation;
- action provenance and routing;
- permission, preview, audit, and rollback.

`Just-in-time architecture`:
Writing a design constraint immediately before the feature that needs it,
instead of creating speculative architecture for distant features.

## Feature Requirement Shape

Each non-trivial feature uses one source-of-truth folder:

```text
docs/requirements/<feature-name>/
  SPEC.md
  implementation_plan.md
  task.md
```

`SPEC.md` is the design contract. `implementation_plan.md` is the executable
handoff. `task.md` records factual progress and evidence.
