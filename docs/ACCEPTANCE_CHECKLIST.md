# Acceptance Checklist

Use this checklist before marking any task complete.

All requirements and acceptance gates must refer to a `SPEC`. Do not use
alternate process names.

## SPEC

- [ ] A SPEC exists for the task.
- [ ] The SPEC is named in the final evidence.
- [ ] Scope is explicit.
- [ ] Non-goals are explicit.
- [ ] Inputs and outputs are explicit.
- [ ] Permission and privacy behavior are explicit.

## Multi-Agent Process

- [ ] Planner output exists.
- [ ] Worker did not change the acceptance criteria.
- [ ] Reviewer independently checked the result.
- [ ] Planner, Worker, and Reviewer roles were not collapsed into one agent for
      implementation or mutation-capable work.
- [ ] Main coordinator summarized evidence instead of self-approving.

## Safety

- [ ] No real vault mutation unless explicitly approved.
- [ ] No silent delete, rewrite, merge, or move.
- [ ] Dry-run precedes write operations.
- [ ] Any real-vault approval names the exact vault path and operation.
- [ ] Audit behavior is defined for write operations.

## Quality

- [ ] Checks ran or were explicitly marked not applicable.
- [ ] Each check has an objective pass, fail, or not-applicable result.
- [ ] Failure modes are documented.
- [ ] Paths changed are listed.
- [ ] Remaining risks are stated.
