# Multi-Agent Roles

linkloom uses role separation to prevent one agent from planning,
implementing, and accepting its own work.

For implementation tasks, Planner, Worker, and Reviewer must be separate roles.
The project uses `SPEC` as the canonical term for requirements and acceptance
criteria; do not introduce competing labels.

## Planner Agent

Owns:

- task framing;
- SPEC drafting;
- scope and non-goals;
- acceptance criteria;
- risk identification.

Must not:

- implement code;
- edit production files outside the approved planning docs;
- mark its own plan as complete.
- approve Worker output.

## Worker Agent

Owns:

- implementation inside the approved file scope;
- updating docs that are directly required by the approved task;
- running local checks assigned by the SPEC.

Must not:

- expand scope without approval;
- change acceptance criteria after starting;
- review its own work as accepted;
- modify real user vault data unless an approved dry-run plan names the exact
  target path and operation.

## Reviewer Agent

Owns:

- checking diffs against SPEC;
- verifying acceptance criteria;
- identifying missing tests, unsafe behavior, and scope drift;
- approving, blocking, or requesting changes.
- recording objective evidence for every acceptance decision.

Must not:

- quietly fix implementation while reviewing;
- weaken acceptance criteria to pass the task;
- assume correctness without evidence;
- accept work that lacks a SPEC, changed-file list, or check results.

## Main Coordinator

Owns:

- keeping the process moving;
- reconciling Planner, Worker, and Reviewer outputs;
- reporting final state to the user.

Must not:

- skip Planner or Reviewer for implementation tasks;
- claim completion without the acceptance evidence;
- use "I checked it mentally" as the only verification.
