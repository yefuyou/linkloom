# Reference Project Reverse Engineering

This document records development-method patterns inferred from reference
projects studied during the Agent sprint.

The goal is not to copy their stacks. The goal is to learn how mature projects
make architecture, SPECs, tests, and implementation reinforce each other.

## Hope Agent

Hope Agent is SPEC-driven through layered sources of truth rather than a single
giant specification file.

Patterns observed:

1. **Repo contract layer**: `AGENTS.md` contains cross-PR rules, mandatory
   checks, architecture red lines, Plan Mode semantics, tool approval rules, and
   documentation maintenance rules.
2. **Docs index layer**: `docs/README.md` routes readers to architecture docs,
   agent capabilities, plans, release process, and known doc gaps.
3. **Feature SPEC layer**: concrete specs live in `docs/requirements/`.
   A feature SPEC includes goals, non-goals, numbered requirements, state-machine
   thinking, risks, priorities, and acceptance scenarios.
4. **Implementation-plan layer**: the implementation plan cites the requirement,
   current code evidence, design phases, exact files, test commands, and manual
   acceptance scenarios.
5. **Task ledger layer**: `task.md` separates actual progress from approved
   design.
6. **Architecture-as-guardrail**: docs such as backend separation, transport
   modes, process model, reliability, plan mode, tool system, skill system, and
   subagent behavior constrain implementation.
7. **Two-stage review**: spec compliance and code quality are separate review
   concerns.

Pattern to copy:

```text
AGENTS.md
  -> docs/README.md
  -> docs/architecture/*
   -> docs/requirements/<feature>/SPEC.md
  -> implementation_plan.md
  -> task.md
  -> spec-compliance review
  -> quality review
```

What Vault Steward should copy:

- Keep `AGENTS.md` short and strict.
- Put feature-specific design in `docs/requirements/<feature>/`.
- Treat architecture docs as constraints, not after-the-fact explanation.
- Separate frozen design contract from factual progress ledger.
- Require independent review against the SPEC.

What Vault Steward should not copy yet:

- multi-host runtime complexity;
- background job/event bus complexity;
- full desktop app structure.

## Modular RAG MCP Server

Modular RAG MCP Server is SPEC-driven through a stronger central
`DEV_SPEC.md`.

Patterns observed:

1. **Capability README**: `README.md` explains the system at product and method
   level.
2. **Authoritative build plan**: `DEV_SPEC.md` defines phases and maps them to
   implementation modules.
3. **Task shape**: each spec task names the goal, files to modify,
   implementation points, acceptance criteria, and exact test command.
4. **Implementation mirrors spec boundaries**: source directories align with
   ingestion, retrieval, MCP server, observability, and evaluation responsibilities.
5. **Tests act as acceptance court**: unit tests cover contracts, integration
   tests cover orchestration, E2E tests cover external protocol behavior, and
   eval fixtures prevent regression.
6. **Learning docs are separate**: learning notes, progress, reviews, and
   troubleshooting are not mixed into the runtime build contract.

Pattern to copy:

```text
README capability promise
  -> DEV_SPEC phase contract
  -> module boundary
  -> unit contract test
  -> integration orchestration test
  -> e2e contract test
  -> eval/golden fixture
```

What Vault Steward should copy:

- Add `DEV_SPEC.md` as the build contract.
- Make each phase name expected files, acceptance criteria, and checks before
  implementation.
- Use synthetic fixture vaults as golden cases.
- Test adapter/policy contracts separately from full workflows.
- Keep learning notes separate from build specs.

What Vault Steward should not copy yet:

- vector database complexity;
- MCP server surface;
- dashboard and production observability before local CLI proves value.

## Early Pattern Hypothesis

Vault Steward should use a lighter version of both:

- Hope Agent style for harness concepts and safety boundaries.
- Modular RAG MCP style for phase SPECs, module contracts, and acceptance tests.

## Candidate Vault Steward Pattern

```text
SPEC
  -> module contract
  -> fixture
  -> implementation
  -> acceptance test
  -> docs update
  -> independent review
```

This pattern keeps the project open-source friendly and prevents private-vault
assumptions from becoming hidden implementation requirements.

## Adopted Vault Steward Development Shape

Vault Steward will combine both patterns:

```text
SPEC.md                  product-level contract
DEV_SPEC.md              phase build contract
AGENTS.md                contributor safety rules
docs/README.md           documentation map
docs/architecture/*      implementation constraints, added just-in-time
docs/requirements/*      feature SPEC, implementation plan, task ledger
tests/fixtures/*         synthetic vaults and golden cases
tests/unit/*             contracts
tests/integration/*      workflow orchestration
tests/e2e/*              CLI/user-facing behavior
```

This gives the project a clear learning path:

1. Write product SPEC.
2. Write phase DEV_SPEC.
3. Write feature SPEC.
4. Write implementation plan.
5. Implement only the approved files.
6. Test against fixtures.
7. Review SPEC compliance.
8. Review code quality.
