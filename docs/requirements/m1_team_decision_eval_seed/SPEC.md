# M1 Team Decision & Action Eval Seed SPEC

## Status

This is a synthetic DATA / SPEC / TEST-DESIGN artifact **ACCEPTED AS EVAL
SEED** following final independent semantic re-review. The accepted corpus has
30 cases, six workspaces, and 36 notes; its canonical Git/LF SHA-256 is
`49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`.
Golden 8 is **FORMALLY FROZEN** in
[GOLDEN_8_FREEZE.md](GOLDEN_8_FREEZE.md). Acceptance of this seed does not
accept or implement M1 production behavior.

## Purpose

Freeze a small, repeatable Gold corpus for the first Team Decision & Action
vertical slice. The corpus must let a later read-only Agent evaluation recover
current decisions, rejected alternatives, unresolved work, ownership, status,
and evidence boundaries from realistic Markdown workspaces.

The corpus is deliberately independent of the M0.2 durability contract and
the M0.3 implementation. A future runner may adapt these records to a runtime
contract, but must not change the Gold data to fit a model output.

## Scope

The artifact contains:

- six synthetic workspaces with six Markdown notes each;
- exactly 30 JSONL cases, five per workspace;
- direct recovery, multi-note synthesis, rejected-alternative reasoning,
  unresolved-action recovery, temporal/stale conflict, and
  ambiguous/insufficient-evidence cases;
- symbolic preferred and acceptable tool trajectories;
- deterministic metric mappings for retrieval, tool use, trajectory, and
  structured outcome evaluation;
- a standard-library-only consistency validator.

## Frozen distribution

| Difficulty | Scenario | Cases |
|---:|---|---:|
| 1 | direct factual recovery | 6 |
| 2 | multi-note synthesis | 6 |
| 3 | rejected-alternative reasoning | 5 |
| 4 | unresolved-action recovery | 5 |
| 5 | temporal/stale conflict | 4 |
| 6 | ambiguous/insufficient evidence | 4 |

Every case has at least one distractor, required and forbidden claims, valid
relative evidence references, and a trajectory that cannot finish before
evidence. Hard-negative coverage must include mentioned-vs-decided,
proposed-vs-assigned, planned-vs-completed, stale-vs-current, and
unsupported-claim distinctions.

## Case contract

Each JSONL object uses `schema_version: "team-decision-eval-case/v1"` and
contains:

- identity and scenario fields: `case_id`, `workspace_id`, `difficulty`,
  `scenario_type`, `hard_negative_tags`, `user_question`;
- corpus scope: `source_notes`, `expected_relevant_notes`,
  `distractor_notes`;
- Gold outcome fields: `expected_decision`,
  `expected_rejected_alternatives`, `expected_reasoning_points`,
  `expected_action_items`, `expected_unresolved_items`,
  `expected_evidence_refs`;
- trajectory design: `expected_tool_trajectory`,
  `acceptable_alternative_trajectories`, `trajectory_constraints`;
- grounding and uncertainty: `required_claims`, `forbidden_claims`,
  `groundedness_requirements`, `insufficient_evidence_behavior`,
  `expected_outcome`;
- future evaluation mapping: `metric_mapping`.

Evidence references use only
`workspaces/<workspace_id>/<note>.md#<heading-slug>`. Current, directly
verified evidence takes precedence over older or exploratory notes. A later
runner must keep facts, uncertainty, and suggestions separate and must not
expose Gold labels to the model.

## Acceptance criteria

1. `python docs/requirements/m1_team_decision_eval_seed/validate_dataset.py`
   exits zero and reports 30 cases, six workspaces, and 36 notes.
2. JSONL parses one object per line; IDs are unique; all paths and anchors
   resolve inside this directory.
3. The frozen difficulty/workspace distributions and hard-negative coverage
   match this SPEC.
4. Each case has at least one preferred and one condition-bearing acceptable
   symbolic trajectory; no trajectory permits `final_without_evidence`.
5. No file outside this directory is required to consume or validate the seed.
6. Independent semantic re-review accepted this corpus as an Eval Seed. Any
   future dataset revision requires a new independent review; this historical
   authoring constraint remains the rule for subsequent changes.

## Non-goals

No Eval runner, embeddings, vector database, semantic retrieval, LLM judge,
provider/network call, runtime integration, writeback, real vault access,
M0.2/M0.3 change, M0.4 work, or M1 production implementation is included.

## Evidence and handoff

The Worker handoff records validator output, scope checks, skipped runtime
checks, known semantic-review status, and the next independent-review step.
Git commit, push, and PR are outside this task.
