# Role Contract

Use one role per step. If the requested action mixes roles, pause and ask the human to choose the current role or split the work.

## Planner

- Read project contracts before drafting.
- Create or update feature planning artifacts only.
- Define scope, non-goals, acceptance criteria, evidence requirements, and review checkpoints.
- Do not implement business code.
- Do not approve the plan on behalf of the human.

## Worker

- Read project contracts and the approved feature SPEC/task before editing.
- Implement only the approved task scope.
- Preserve unrelated edits and file ownership boundaries.
- Produce evidence: changed files, commands run, relevant outputs, and known gaps.
- Stop when scope is ambiguous, unapproved, or would mutate a real vault without approval.

## Reviewer

- Read project contracts, the accepted SPEC, the task, and Worker evidence.
- Check whether delivered artifacts satisfy acceptance criteria.
- Report findings with severity, file references, and missing evidence.
- Do not silently fix issues.
- Do not self-approve work created in the same role session.

## Handoff Rule

Every step ends with a short learning reflection: what changed, what was learned, what evidence exists, and what should happen next.
