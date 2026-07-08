# Agent Rules For Vault Steward

This repository is SPEC-first and multi-agent governed.

Use `SPEC` as the canonical planning and acceptance term. Do not introduce
alternate labels for project requirements, acceptance criteria, or task gates.

## Hard Rules

1. Do not implement a feature before its SPEC exists.
2. Do not let the same agent plan, implement, and accept the same task.
3. Do not modify a user's real vault unless a dry-run has been reviewed and the
   human explicitly approves the exact mutation plan.
4. Do not silently delete, rewrite, or merge user-authored notes.
5. Do not hard-code private paths into reusable core logic.
6. Do not treat AI-generated drafts as verified knowledge.
7. Do not mark a task complete unless acceptance checks are documented.

## Required Task Flow

```text
Planner -> SPEC / task plan
Human -> approval or adjustment
Worker -> implementation within approved scope
Reviewer -> independent acceptance check
Main coordinator -> final summary
```

The Planner, Worker, and Reviewer roles must be separate for any implementation
or mutation-capable task. A single agent may not author the SPEC, perform the
work, and approve the result.

## File Safety

Real-vault mutation is forbidden unless all are true:

- dry-run plan exists;
- diff or move plan is visible;
- human approval is explicit;
- audit log path is defined;
- rollback or recovery note is documented.

Approval must name the target vault path and the proposed operation. Generic
permission to "clean up the vault" is not enough.

## Objective Acceptance Gates

A task may be accepted only when the Reviewer can point to objective evidence:

- the governing SPEC or task plan;
- the exact files changed;
- the checks run, with pass/fail or not-applicable status;
- confirmation that no real vault was mutated, or the approved dry-run and
  approval record if mutation occurred;
- any remaining risks or blocked checks.

## Current Phase Restriction

Phase 0 allows only governance and design documentation.

Do not create implementation modules such as scanner, writer, merge planner,
retriever, or agent runtime until Phase 1 SPEC is approved.
