# Vault Steward

Local-first Markdown knowledge-base steward.

This project is intentionally SPEC-first. No feature implementation may start
until the corresponding SPEC, acceptance criteria, and reviewer checks exist.
`SPEC` is the canonical term for requirements and acceptance gates.

## Current Phase

Phase 0: project governance and development method.

No scanner, writer, merge tool, or real-vault mutation is implemented yet.

## Core Idea

Vault Steward will help maintain Markdown or Obsidian-style knowledge bases by
scanning notes, diagnosing structure drift, proposing evidence-backed plans, and
requiring human approval before any write operation.

## Safety Defaults

- Local-first by default.
- Read-only by default.
- Dry-run before any write.
- Human approval for the exact dry-run mutation plan before real-vault changes.
- Audit log for every applied change.
- User-authored content must be preserved unless explicitly approved.

## Documentation Map

- [SPEC.md](SPEC.md): canonical project specification.
- [DEV_SPEC.md](DEV_SPEC.md): phase-by-phase build contract. Implementation starts from here.
- [AGENTS.md](AGENTS.md): hard rules for AI agents working in this repo.
- [docs/README.md](docs/README.md): documentation navigation map.
- [docs/MULTI_AGENT_ROLES.md](docs/MULTI_AGENT_ROLES.md): Planner, Worker, Reviewer separation.
- [docs/SPEC_DRIVEN_DEVELOPMENT.md](docs/SPEC_DRIVEN_DEVELOPMENT.md): required development workflow.
- [docs/PROJECTS_REVERSE_ENGINEERING.md](docs/PROJECTS_REVERSE_ENGINEERING.md): patterns learned from Hope Agent and Modular RAG MCP Server.
- [docs/MARKET_LANDSCAPE.md](docs/MARKET_LANDSCAPE.md): adjacent tools and product differentiation.
- [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md): restore-point and commit-boundary rules.
- [docs/ACCEPTANCE_CHECKLIST.md](docs/ACCEPTANCE_CHECKLIST.md): task-level acceptance template.
