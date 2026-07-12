# SPEC-Driven Development

linkloom follows SPEC-driven development.

`SPEC` is the only canonical label for requirements, scope, acceptance
criteria, and milestone gates. Do not rename or mirror the process under another
term.

## Why

The project touches personal knowledge bases. An LLM-assisted project that can
read and eventually mutate notes needs stronger boundaries than a normal demo.

SPEC-driven development prevents:

- feature drift;
- unsafe vault mutations;
- unreviewed AI rewrites;
- tests that do not match the product promise;
- private-user assumptions leaking into open-source core logic.

## Required Sequence

```text
1. Problem statement
2. SPEC
3. Acceptance criteria
4. Test/check plan
5. Implementation
6. Independent review
7. User-facing summary
```

Implementation starts only after steps 1-4 exist.

Planner, Worker, and Reviewer responsibilities must remain separate for
implementation tasks. The Worker may update docs or code inside the approved
scope, but may not edit the acceptance criteria after work begins or accept its
own result.

## SPEC Template

```md
# SPEC: <feature name>

## Problem

## User Story

## Scope

## Non-Goals

## Inputs

## Outputs

## Permission Model

## Failure Modes

## Acceptance Criteria

## Checks
```

## Minimum Acceptance Criteria For Any Feature

- The feature has a named SPEC.
- Inputs and outputs are explicit.
- File write behavior is explicit.
- Real-vault risk is addressed with a dry-run requirement and explicit human
  approval gate for any mutation.
- Tests or manual checks are listed.
- Reviewer can reject the task using objective criteria.

## Objective Review Evidence

Before a task is accepted, the Reviewer must be able to verify:

- changed files are listed;
- checks are listed with pass, fail, or not-applicable status;
- scope matches the governing SPEC;
- no real vault mutation occurred, or the dry-run output and human approval are
  recorded;
- remaining risks and skipped checks are stated.

## Milestone Gate

No Milestone 1 implementation may begin until the product roadmap, governance
docs, and feature-level SPEC plus implementation plan pass their required human
and independent review gates.
