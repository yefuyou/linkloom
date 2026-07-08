# Git Workflow

Vault Steward uses Git as the project safety net. Every meaningful change should
be recoverable, reviewable, and tied to a SPEC-backed task.

## Current Rule

Use small commits at acceptance boundaries, not at every keystroke.

Good commit units:

- project governance skeleton;
- one skill or workflow added;
- one feature SPEC added;
- one implementation slice that passes its tests;
- one review-only correction.

Avoid commits that mix:

- planning docs and unrelated implementation;
- real-vault output and source code;
- formatting churn and behavior changes;
- multiple features.

## Before Work

1. Check repository status.
2. Identify the governing SPEC or task.
3. State the intended changed-file set.
4. Confirm whether the task is planning, implementation, or review.

## Before Commit

1. Review the diff.
2. Run the checks named by the SPEC or task.
3. Ensure no real vault mutation is included unless the exact approved dry-run
   plan required it.
4. Write a concise commit message:

```text
<area>: <change>
```

Examples:

```text
docs: add SPEC-first governance contract
skill: add project development coach
spec: define read-only vault scanner
scanner: index markdown note metadata
```

## Restore Point Policy

Until a finer policy is agreed, create commits after a Reviewer passes an
acceptance boundary. If work is risky or touches many files, create a branch
before implementation.
