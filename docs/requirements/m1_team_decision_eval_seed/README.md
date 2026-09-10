# LinkLoom Team Decision & Action Eval Seed

Status: **ACCEPTED AS EVAL SEED** following final independent semantic
re-review: 30 cases, six workspaces, and 36 notes. Canonical Git/LF SHA-256:
`49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`.
Windows CRLF working-tree hashing is not content drift. Golden 8 is
**FORMALLY FROZEN** in [GOLDEN_8_FREEZE.md](GOLDEN_8_FREEZE.md). This is not
M1 production implementation.

## Purpose

This directory proposes a small, synthetic regression corpus for the first
`Team Decision & Action` vertical slice. It tests whether an evidence-grounded
Agent can recover a current decision, explain rejected alternatives, recover
unfinished work, and stop honestly when the notes do not support a claim.

The seed is deliberately independent of the current M0.3 runtime and its
internal schemas. It defines business Gold data and trajectory constraints;
future runners may adapt the records into their own input/output contracts
without changing this Gold set.

## Non-goals

- This is not an Eval runner, model prompt, provider adapter, or ToolRuntime.
- It does not modify production code, the real vault, or user notes.
- It does not score a model, claim a Gate F pass, or replace independent review.
- It does not prescribe one exact tool trajectory when more than one path can
  acquire sufficient evidence.
- It does not imply that any named fictional team, project, or provider exists
  in a real bank, company, or deployment.

## Layout and corpus contract

`dataset.jsonl` contains exactly 30 one-line JSON objects. There are six
workspaces, five cases per workspace, and six Markdown notes per workspace
(36 notes total):

```text
workspaces/<workspace_id>/*.md
```

Workspace IDs and themes are:

| Workspace | Synthetic engineering theme |
|---|---|
| `model_provider_selection` | provider comparison and adoption readiness |
| `retrieval_upgrade` | lexical/semantic/hybrid retrieval rollout |
| `deployment_incident` | timeout, replay, and remediation status |
| `agent_evaluation_rollout` | deterministic release gates and judge diagnostics |
| `internal_tool_integration` | read-only connector and export boundary |
| `data_retention_migration` | active/archive retention and legal sign-off |

Every note has ISO date metadata, a document `status`, an `authority` level,
and stable Markdown headings. Every evidence reference uses the relative form
`<note-path>#<heading-slug>` and is checked against the actual heading.

Each case uses `schema_version: "team-decision-eval-case/v1"` and contains:

- scenario and hard-negative labels;
- source, relevant, distractor, decision, rejected-alternative, reasoning,
  action, unresolved-item, and evidence Gold fields;
- a preferred trajectory plus at least one acceptable alternative;
- trajectory limits, ordering rules, and the mandatory
  `final_without_evidence` prohibition;
- claim-level groundedness, uncertainty behavior, and forbidden claims;
- an explicit mapping to deterministic component, trajectory, and outcome
  metrics.

`difficulty` is an integer level with this frozen distribution:

| Level | Meaning | Count |
|---:|---|---:|
| 1 | direct factual recovery | 6 |
| 2 | multi-note synthesis | 6 |
| 3 | rejected-alternative reasoning | 5 |
| 4 | unresolved-action recovery | 5 |
| 5 | temporal/stale conflict | 4 |
| 6 | ambiguous/insufficient evidence | 4 |

The validator also requires every case to have non-empty `required_claims`
and `forbidden_claims`, at least one distractor, and a disjoint relevant-note
set. All paths must stay below this directory and all content is synthetic.

## Trajectory semantics

The semantic-correction revision is not frozen or accepted. The proposed
Golden 8 remain mps-001, aer-002, drm-003, inc-004, ret-005, iti-005, drm-002,
and aer-005; focused independent data re-review is required before freezing.

## Fact and evidence semantics

- `deadline: null` means no deadline is recorded, never a completion date.
  Completion dates remain explicitly labelled in the action description.
  A due date is not the condition that clears a blocker.
- `owner: null` means no named owner is recorded for this item. The string
  `unassigned` is reserved for explicit unassigned/no-assigned-owner evidence.
  Proposed or suggested names are not assignments. Neither representation
  alone establishes a blocked execution state.
- `pending` on iti-005's unresolved owner/deadline item describes unresolved
  information, not the review's execution status or a blocked assignment task.
  The checksum test is separately documented as blocked on review; test and
  review must not become two independently tracked actions.
- A partial project/verification state belongs in reasoning/decision context,
  not an invented owned action. Approval, staging, canary, dry-run and local
  checks do not establish production completion. Requirements are not results.
- Pending approval is not rejection. `expected_rejected_alternatives` records
  rejected business options, not false answer claims or stale completion prose.
- L6 is an ambiguity category, not a mandate for null decisions. State known
  positives and negatives first, then identify the specific unknown fields.
- inc-001 has no decision target: its null/not_found decision is a schema
  placeholder for not-applicable, not a failure to find the reviewed root cause.
  The reviewed finding is in reasoning and is scored as root-cause correctness.
- Evidence references must support the entire fact, including owner, date and
  status. Nested refs are included in the top-level evidence inventory. That
  inventory is a support pool, not a demand to cite every listed anchor.
  Discussion notes can support experimental scope and non-approval, without
  becoming decision authority. Required support cannot also be a distractor.
- Missing-field claims are scoped to the supplied dated records, not to all
  possible future records. Absence of a date requires checking the relevant
  status records; anchor existence alone does not prove absence or entailment.

## Trajectory interpretation

`expected_tool_trajectory.preferred` and each entry in
`acceptable_alternative_trajectories` are ordered symbolic traces. `final` is
the model's terminal answer, not a local tool. A trajectory is acceptable when
it obtains enough evidence for the case, obeys the case-specific maximums and
ordering rules, and emits a grounded final result. The preferred trace is a
Gold preference, not the only valid answer.

Every case forbids `final_without_evidence`. A successful `search_notes` result
can be evidence when its returned references directly support the answer; a
`read_verified_note` step is preferred when the claim needs section-level
verification. Repeated search/read calls are limited per case so future
evaluation can distinguish useful recovery from looping.

Argument correctness is evaluated against the user's question and the case's
source scope: the runner must preserve model-produced search/read arguments
when passing them to its execution boundary. This seed does not depend on a
particular runtime request class.

Each read targets exactly one previously discovered note. Preferred traces
show one read per relevant note, not multi-note reads. Search discovery is
conditional: one search is never guaranteed to return all required content.
Up to three targeted searches and one extra read beyond the relevant-note
count are permitted. Limits are synthetic safety caps, not measured optimal
retrieval counts; revise only with independent review if later retrieval
experiments show a legitimate path exceeds them. Listed traces are examples,
not an exhaustive whitelist. Extra targeted searches or changed read order
are acceptable within the constraints when they acquire needed evidence.
Identical tool names alone are not redundant calls: compare arguments, returned
evidence, progress, and recovery purpose. No-progress duplicate calls remain
undesirable. A shortened alternative is valid only when actual returned section
content supplies the omitted read, never from a bare path/title/citation.

## Metric mapping

Each case selects only applicable metrics from this registry. The mapping is
part of the test design and is not a score:

### Component metrics

- `retrieval_hit_at_k`: expected relevant note appears in the returned top-k
  retrieval set.
- `mrr`: reciprocal rank of the first expected relevant note.
- `tool_selection_correctness`: selected tool is allowed and useful for the
  next evidence step.
- `tool_argument_correctness`: model-produced arguments preserve the required
  query, note target, and bounded scope.

### Trajectory metrics

- `required_tool_missing`: a necessary evidence tool was never called.
- `unnecessary_tool_calls`: a call adds no needed evidence or violates the
  case's minimality intent.
- `repeated_calls`: duplicate search/read calls beyond the case limit.
- `premature_final`: final answer before sufficient evidence or before a
  required evidence step.
- `invalid_ordering`: read-before-search, final-before-evidence, or another
  case-specific ordering violation.

### Outcome metrics

- `decision_accuracy`: current decision and status match Gold.
- `root_cause_accuracy`: the reviewed cause and contributor distinction match
  reasoning Gold; this is not approval/decision accuracy.
- `action_item_accuracy`: compare description, owner, deadline and status of
  applicable actions, preserving unknown/unassigned and completion-date
  distinctions. Report field-level errors; do not reward invented owners.
- `rejected_alternative_accuracy`: rejected option and reason match Gold.
- `unresolved_item_recall`: unresolved actions/questions are recovered with
  owner/status when the notes provide them.
- `evidence_groundedness`: claims cite valid, supporting Gold evidence.
- `unsupported_claim_rate`: rate of claims not supported by the corpus or that
  violate `forbidden_claims`.
- `task_success`: the requested structured recovery is complete, or the case
  explicitly returns the required uncertainty outcome.

Metric applicability is question-scoped. Direct decision questions do not
require an inventory of rejected alternatives; contextual Gold is not an
extra mandatory answer section. Empty/non-applicable lists receive N/A and
are excluded from recall denominators, not scored as perfect recall. Report
the number of applicable cases with every aggregate. An unsupported extra
claim is still evaluated even when the corresponding recall is N/A.

`partial_with_uncertainty` describes business readiness/evidence limits, not
answer failure: correctly reporting blocked readiness can achieve task_success.
Decision status `partial` on a mixed summary does not rescind its approved
choice. Score approval separately from rollout readiness. These are metric
contracts only; no scorer or Eval runner is implemented here. Structural checks
cannot establish natural-language entailment or acceptable paraphrase quality;
those require independent semantic review (or a separately approved judge).

## Consumption by M1 and M3

M1 may use the workspaces and Gold fields to implement the read-only Team
Decision & Action result: decision, rationale, rejected alternatives,
unresolved items, next actions, and evidence. It must keep source notes
immutable and separate facts from suggestions.

M3.1/M3.2 may load `dataset.jsonl`, run a selected model/runtime against an
isolated copy of one workspace, and compute the registry metrics. A runner
must record dataset/schema version, case ID, model/provider identity, prompt
version, tool policy, raw trajectory, outcome, skipped checks, and failure
attribution. It must never feed Gold labels or hidden expected text into model
context. Optional LLM-judge output must remain separate from deterministic
metrics.

## Known limitations

- The corpus is small and intentionally English-language; it is not a broad
  retrieval benchmark.
- Gold answers are structured expectations, not a complete natural-language
  paraphrase set.
- Tool traces are symbolic and do not prove a particular runtime's crash/
  resume behavior; that belongs to later reliability work.
- Dates and statuses are synthetic snapshots. A future benchmark must define
  its clock and stale-data policy explicitly.
- No live provider, network, external connector, or real-vault behavior is
  exercised here.

## Synthetic/privacy boundary

All project names, workspace content, people, provider names, and dates are
fabricated for repeatable tests. The corpus contains no credentials, private
absolute paths, external URLs, real employer data, or real Obsidian vault
content. Do not replace these notes with personal or employer records.

## Validation command

From the repository root:

```powershell
python docs/requirements/m1_team_decision_eval_seed/validate_dataset.py
```

The validator is standard-library-only and checks data consistency, path and
heading ownership, distribution, trajectory safety, and metric-registry use.
It is not an inference or Eval runner.
