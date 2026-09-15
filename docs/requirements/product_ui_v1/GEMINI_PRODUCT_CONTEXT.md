# LinkLoom Product UI V1 — Gemini Product Context

Date: 2026-09-15
Status: Design input only. Do not implement product code from this file.

## 1. Product truth

LinkLoom is not a general chatbot, document summarizer, generic knowledge-base
Q&A surface, agent trace viewer, or developer runtime console.

Its primary job is:

> Recover what a team finally decided from scattered project documents,
> meeting notes, decision records, and status updates — then show why that
> answer is trustworthy, what must happen next, what is unresolved, and where
> evidence is missing or conflicting.

The product's core value is **decision recovery + evidence grounding**.

The first target users are project leads, PMs, PMOs, and project team members.
Their first question is normally “So what did we finally decide?” They need a
fast answer, but they also need to distinguish an approved decision from a
proposal, prototype observation, stale draft, or unsupported inference.

## 2. Real product capabilities today

The current Python product has no Web UI. The implemented backend can:

1. accept a natural-language question for one local Markdown workspace;
2. run a durable, read-only `team_decision` workflow;
3. let a model autonomously choose `search_notes` and
   `read_verified_note` calls;
4. use the existing local `ToolRuntime` to validate and execute those calls;
5. use Gemini or DeepSeek adapters for a real multi-turn model/tool loop;
6. produce a strict provider-neutral `TeamDecisionResult`;
7. reject malformed output or claims citing evidence the model did not
   actually observe;
8. persist run status, tool ledger, checkpoint, result artifact, and trace;
9. fail closed on runtime/provider/structured-grounding errors;
10. remain read-only: it does not modify project notes or execute actions.

The real runtime lifecycle includes accepted, running, paused, completed,
failed, rejected, stale, and expired states. Normal Team Decision execution is
synchronous at the current Python boundary, but durable traces and checkpoints
are persisted. Important trace events include run accepted/started/completed/
failed, step started/completed/failed, provider requested, tool called/
completed/failed, and checkpoint saved.

Agent process is supporting provenance only. Normal users should not see
tokens, turns, model internals, `ToolRuntime`, or raw tool ledger by default.

## 3. Strict business result contract

The actual serialized output contract is:

```json
{
  "schema_version": "team-decision-result/v1",
  "decision": {
    "value": "string or null",
    "status": "approved | partial | insufficient_evidence | not_found",
    "evidence_refs": ["verified evidence id"]
  },
  "rationale": [
    {
      "point": "evidence-grounded reasoning",
      "evidence_refs": ["verified evidence id"]
    }
  ],
  "rejected_alternatives": [
    {
      "alternative": "recorded alternative",
      "reason": "recorded rejection reason",
      "evidence_refs": ["verified evidence id"]
    }
  ],
  "actions": [
    {
      "description": "recorded action",
      "owner": "string | explicit unassigned | null",
      "deadline": "YYYY-MM-DD | null",
      "status": "pending | in_progress | blocked | completed | unassigned",
      "evidence_refs": ["verified evidence id"]
    }
  ],
  "unresolved_items": [
    {
      "description": "recorded unresolved item",
      "owner": "string | explicit unassigned | null",
      "deadline": "YYYY-MM-DD | null",
      "status": "pending | in_progress | blocked | unassigned",
      "evidence_refs": ["verified evidence id"]
    }
  ],
  "uncertainty": {
    "status": "none | partial | insufficient_evidence | conflicting_evidence",
    "statement": "string or null",
    "unknown_fields": ["field path"],
    "evidence_refs": ["verified evidence id"]
  },
  "evidence_refs": ["ordered deduplicated union of claim refs"]
}
```

Every material decision, rationale point, rejected alternative, action,
unresolved item, and factual uncertainty statement owns claim-level evidence
references. A path or opaque ref by itself is not evidence. Evidence records
available to the UI include:

```json
{
  "evidence_id": "ev_p1_0001",
  "relative_path": "03-final-decision.md",
  "line_start": 10,
  "line_end": 10,
  "quote": "The Atlas Lantern team selected Aster A as the approved model provider for the",
  "reason": "matched query terms",
  "source_kind": "note_body",
  "status": "verified",
  "content_sha256": "...",
  "quote_sha256": "..."
}
```

Internal hashes and `ev_p1_0001`-style IDs may exist for diagnostics, but must
not become the primary user-facing evidence language. Users should understand
the source filename, quoted passage, location, and relationship to a claim.

## 4. Product semantics the UI must preserve

- **Approved is not merely mentioned.** A proposal, candidate, prototype,
  recommendation, experiment, or discussion is not a decision.
- **Current authority outranks a stale draft.** A later informational note does
  not supersede an approved decision record merely because it is newer.
- **Assigned is not proposed.** Suggested ownership must not be rendered as an
  assignment.
- **Completed is not planned.** Readiness work can remain blocked or in
  progress after a provider decision is approved.
- Missing owners and deadlines stay unknown; they must not be invented.
- Insufficient evidence is a valid product outcome, not a generic runtime
  failure.
- Provider failure, malformed structured output, and evidence-validation
  failure are actual errors and must not be disguised as business uncertainty.

The visual system must help users distinguish at a glance:

1. confirmed/approved;
2. inferred or partial;
3. unresolved;
4. unknown/insufficient evidence;
5. conflicting evidence;
6. runtime failure.

Do not solve this with a row of generic colored badges alone.

## 5. Real demo case: `mps-001`

Workspace: `model_provider_selection`
Project name used in content: **Atlas Lantern**

User question:

> Which model provider was finally approved for the Atlas Lantern pilot?

Correct final decision:

> Aster A was approved as the model provider for the synthetic pilot and next
> integration slice.

Grounded rationale:

> The decision prioritized an auditable regional data boundary and an owned
> operational path over the faster early prototype.

Rejected alternatives recorded in the authoritative decision note:

- Borealis B: residency exception and additional operational burden were not
  acceptable for this slice.
- Cedar C: no auditable region commitment and streaming behavior did not meet
  the adapter requirement.

Recorded follow-through from later status notes:

- Lin Qiao completed the local adapter contract check on 2026-01-26.
- Chen Rui owns regional evidence validation, due 2026-02-05, blocked pending
  legal confirmation of the `eu-north` processing statement.
- Omar Wu owns the on-call runbook, due 2026-02-07, still in progress.

Unresolved/readiness boundary:

- Regional evidence validation is blocked.
- The on-call runbook is still in progress.
- These open gates do not revoke the approved Aster A decision and do not mean
  Borealis B was adopted.

Authoritative source files and roles:

- `03-final-decision.md` — approved `decision_record`, dated 2026-01-22;
- `02-comparison-review.md` — reviewed `technical_review`, dated 2026-01-15;
- `04-action-status.md` — in-progress `status_update`, dated 2026-01-29;
- `06-adoption-readiness.md` — pending `status_update`, dated 2026-02-05;
- `01-early-proposal.md` — superseded `working_draft`, dated 2026-01-08;
- `05-office-hours-prototype.md` — informational `discussion_note`, dated
  2026-02-02, explicitly not adoption evidence.

This data may be used as a UI development/demo fixture, but it must not be
hard-coded into application business logic.

## 6. Real backend limitations relevant to design

- There is no production HTTP API or Web UI yet.
- The ordinary CLI does not currently expose the `team_decision` workflow or
  bootstrap a real Gemini/DeepSeek client; current real-provider execution is
  exercised by guarded smoke harnesses.
- The Python runtime call is synchronous, though the UI can run it in a
  background worker and project persisted trace events into product-level
  progress such as searching, reading, and preparing the answer.
- Real-provider output is fallible. A recent real Gemini `mps-001` run reached
  search and read but failed strict structured evidence validation. The UI must
  give a humane retry/error surface without exposing internal error machinery.
- No backend capability exists for auth, billing, workspace management,
  mutation, task execution, or editing source notes. Do not design or imply
  those capabilities.

## 7. Required V1 user experience

The primary surface is a desktop-first Web application. A first-time viewer
must understand within roughly 30 seconds that LinkLoom recovers a team's final
decision and exposes the evidence behind it.

The experience must include:

- workspace/project context;
- a natural-language query entry state;
- a complete run progression: loading, searching, reading, answer ready;
- final decision;
- rationale;
- evidence;
- actions;
- unresolved items and uncertainty;
- empty/no-query state;
- insufficient-evidence state;
- runtime error state;
- responsive behavior.

The final design must decide the information architecture rather than mirror
the backend JSON field order.

Evidence must be a core interaction, not an appendix. Consider inline
citations, claim/source focus, source preview, expandable quotes, or a
side-by-side reading mode. Choose the smallest interaction model that makes
trust legible.

Agent trajectory must be hidden by default. If retained, place it behind a
secondary “How LinkLoom found this” provenance entry using user language such
as searched, checked, and verified — never token/turn/runtime-console language.

## 8. Visual constraints

Aim for a mature information/research/decision tool, not an AI startup landing
page or generic SaaS dashboard.

Explicitly avoid:

- every block becoming a rounded card;
- purple/blue AI gradients and glow;
- KPI grids and dashboard tile layouts;
- icon + title + helper copy repeated for every block;
- heavy glassmorphism;
- chat bubbles as the primary answer model;
- three-column overload or a screen full of panels;
- decorative chrome that competes with the decision;
- a Notion clone or a templated admin dashboard.

Define a coherent system for typography hierarchy, spacing, density, surface
hierarchy, borders, interaction states, evidence highlighting, source
treatment, uncertainty, loading, empty, and error states. Body content must be
readable and portfolio screenshots should look like a credible finished
product.

## 9. Required design process and deliverables

First produce three meaningfully different product-structure directions. They
must differ in core interaction model and information architecture, not merely
color or rearrangement. For each direction include:

- core interaction model;
- page information hierarchy;
- what users see first;
- how decision claims and evidence relate visually;
- how uncertainty appears;
- whether/where agent process appears;
- why it fits LinkLoom;
- risks and drawbacks;
- approximate implementation complexity.

Do not assume cards, sidebar, chatbot, three columns, timeline, or a Notion-like
document. Think from the user job first.

After Codex records a V1 selection, produce a final Design Spec containing:

1. selected direction;
2. core user flow;
3. page structure;
4. desktop wireframe;
5. component hierarchy;
6. interaction specification;
7. typography, spacing, density, surfaces, borders, and state system;
8. evidence interaction;
9. uncertainty interaction;
10. loading, empty, insufficient-evidence, and error states;
11. desktop primary layout;
12. responsive behavior;
13. implementation notes that preserve current backend truth.

Design authority: Gemini owns Product/UX/Visual Design. Codex owns engineering
selection criteria and implementation. Do not write application code during
the direction or Design Spec steps.

## 10. Direction-selection criteria delegated to Codex

Codex will select one V1 direction using:

- alignment with decision recovery + evidence grounding;
- comprehension by a first-time viewer in 30 seconds;
- demo clarity;
- fit with the current backend contract;
- modest V1 implementation cost;
- portfolio/showcase value;
- avoidance of feature inventory and speculative backend promises.

The most feature-rich direction should not win by default.

## 11. Repository references

Use these as factual sources when deeper inspection is needed:

- `docs/requirements/m1_1_team_decision_action/SPEC.md`
- `src/linkloom/agents/team_decision.py`
- `src/linkloom/runtime/models.py`
- `src/linkloom/runtime/graph.py`
- `src/linkloom/observability/events.py`
- `docs/requirements/m1_team_decision_eval_seed/dataset.jsonl`
- `docs/requirements/m1_team_decision_eval_seed/workspaces/model_provider_selection/`
- `.artifacts/m1_2_real_provider_smoke/`

Do not read credentials, `.env` values, private vaults, or unrelated runtime
subsystems for this design task.
