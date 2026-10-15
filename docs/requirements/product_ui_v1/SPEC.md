# LinkLoom Product UI V1 — Milestone SPEC

## Status

**APPROVED** — the user explicitly authorized Gemini-led design, Codex-led implementation, direction selection, backend integration, tests, screenshots, and up to three Gemini design reviews in the request that opened this milestone.

Planner: Gemini (Product/UX/Visual) with Codex product-context and architecture synthesis
Worker: Codex
Reviewer: Gemini through a fresh Antigravity CLI review session, with Codex engineering verification

## Problem

LinkLoom has a real read-only decision-recovery runtime and a strict, evidence-grounded `TeamDecisionResult`, but no end-user product surface. Existing CLI and runtime artifacts communicate implementation details rather than the user outcome: what the team decided, why, why it is trustworthy, and what remains incomplete.

## User-visible outcome

A project lead, PM, PMO, or team member can ask a decision question for one workspace and see:

- the final decision first;
- rationale and rejected alternatives;
- actions and unresolved items;
- claim-level citations connected to source passages;
- confirmed, inferred, unresolved, and unknown states;
- a restrained running state based on real runtime status/events;
- an explicit insufficient-evidence result;
- an operational failure state distinct from business uncertainty.

The experience must communicate LinkLoom’s purpose within 30 seconds to a first-time viewer.

## Scope

- Desktop-primary web UI following `DESIGN_SPEC.md`.
- Responsive single-column mode with an evidence inspector sheet below 1024 px.
- Thin, provider-neutral presentation projection for `TeamDecisionResult`, runtime state, tool evidence, and result artifacts.
- A local read-only HTTP boundary using Python’s standard library.
- A `RunBackend` interface and real `RuntimeEngine` adapter that can start and inspect team-decision runs with an already-configured engine.
- Deterministic demo backend and `mps-001` fixtures for UI development and screenshots.
- Initial, running, success, insufficient-evidence, empty, and runtime-error views.
- Evidence selection, source preview/quote fallback, keyboard navigation, and accessible status announcements.
- Automated contract/integration tests and browser-level screenshot verification.
- Up to three meaningful Gemini screenshot review rounds.

## Non-goals

- Runtime, Provider, or backend architecture redesign.
- New model providers, provider selection UI, or credential management.
- Auth, workspace management, billing, settings, admin, memory, or MCP.
- Write access to source notes.
- An agent trace console or runtime-control dashboard.
- A general chat interface.
- Public deployment, packaging, commit, or push.

## Architecture boundary

```text
Browser UI
  ↕ local JSON HTTP
LinkLoomUIService
  ├── ResultProjector (strict presentation mapping)
  ├── DemoRunBackend (deterministic screenshot states only)
  └── RuntimeRunBackend (injected configured RuntimeEngine)
        ↕
      Runtime status/checkpoint/result artifact/tool ledger
        ↕
      existing TeamDecisionResult + verified evidence
```

### Responsibilities

- `ResultProjector` validates the business result through `TeamDecisionResult`, gathers only observed evidence from the durable tool ledger, assigns presentation citations, and produces JSON-safe UI data.
- `RuntimeRunBackend` owns only asynchronous invocation and observation of an injected `RuntimeEngine`. It does not configure providers or change runtime policy.
- `DemoRunBackend` replays fixture states with the same public UI payload shape. It must be isolated from production projection logic.
- `LinkLoomUIService` validates request shape, delegates lifecycle work, and returns safe JSON responses.
- The browser owns presentation state such as selected evidence, inspector visibility, current query text, and polling timers.
- The existing runtime remains the source of truth for run lifecycle, result validity, evidence, and errors.

## Data flow

### Live runtime

1. Browser posts a query to the local UI service.
2. `RuntimeRunBackend` creates a `RunRequest(workflow="team_decision")` and runs the configured engine in a background thread.
3. Browser polls the run resource.
4. The backend reads the latest runtime status/checkpoint.
5. On completion, it reads the runtime-owned result artifact, validates the strict `TeamDecisionResult`, collects matching verified evidence from the tool ledger, and projects the result.
6. The browser renders citations and evidence sources without exposing internal IDs as primary labels.

### Demo

1. Browser selects a deterministic fixture state through a development-only query parameter.
2. `DemoRunBackend` returns the same public shape used by the live backend.
3. Fixture content is sourced from the frozen `mps-001` notes and is never inspected by rendering business logic.

## Public contracts

### Start run

`POST /api/runs`

```json
{
  "query": "Which model provider was finally approved for the Atlas Lantern pilot?"
}
```

Returns a safe run resource with a server-issued UI run ID and an initial/running state.

### Inspect run

`GET /api/runs/{ui_run_id}`

Returns one of:

- running presentation state;
- successful projected decision;
- insufficient-evidence projected result;
- paused/attention state;
- runtime/provider/contract failure.

### Workspace context

`GET /api/context`

Returns only display name, optional safe document count, default demo query, read-only statement, and supported mode.

### UI result invariants

- A material result is parsed with `TeamDecisionResult.from_dict` before rendering.
- A result with contract failure has no decision payload.
- Every rendered claim reference resolves to an observed evidence record, or is explicitly shown without a preview.
- Friendly citation numbers are a presentation mapping and never replace durable evidence IDs internally.
- Missing owners render as `Unassigned`; missing deadlines render as `Not recorded`.
- Source paths are relative and traversal-safe.
- Operational errors never become insufficient-evidence outcomes.

## Running-stage mapping

Product stages use existing runtime facts:

- `accepted` or a newly started job → `Searching the workspace`
- a completed `search_notes` record → `Reading decision records`
- a completed `read_verified_note` record or review step → `Checking supporting claims`
- completed result → `Answer ready`

If available runtime facts do not justify a later stage, keep the last justified stage. Do not invent percentages or counts.

## Failure modes

- Empty query → client validation plus 400 response if submitted.
- Unknown UI run → 404 safe error.
- Runtime/model/provider failure → failure view with safe message and collapsed safe technical code.
- Invalid/incomplete `TeamDecisionResult` → fail closed as contract error.
- Evidence ref absent from observed ledger → fail closed rather than displaying grounded content.
- Evidence quote available but full source unavailable → quote-only inspector.
- Source hash mismatch → no full-source preview; preserve verified quote and explain fallback.
- Poll request failure → retain last trustworthy UI state and offer retry.
- Empty workspace → explicit empty state.

## Acceptance criteria

1. A 1440 × 1024 successful screenshot makes “decision recovery + evidence” understandable within 30 seconds.
2. Initial/query, running, successful result, and insufficient-evidence screenshots exist and are visually complete.
3. The successful `mps-001` result shows Aster A, actual rationale, rejected alternatives, actions, unresolved readiness, and real source excerpts.
4. Clicking a claim citation activates the corresponding source and passage; internal evidence IDs are not primary UI.
5. `confirmed`, `inferred`, `unresolved`, and `unknown` have distinct words and visual treatment.
6. Insufficient evidence is a completed epistemic outcome and is visibly distinct from runtime/provider/contract failure.
7. Agent trajectory is hidden by default under `How LinkLoom found this` and contains only product-language stages.
8. Initial, empty, running, success, insufficient, and error states are implemented.
9. Below 1024 px, evidence opens as an accessible inspector sheet without dropping evidence or uncertainty.
10. Keyboard citation activation, evidence navigation, Escape-to-close, visible focus, and polite running announcements work.
11. An integration test proves `RuntimeRunBackend` projects a real completed `RuntimeEngine` team-decision run and its observed ledger evidence.
12. A contract test proves invalid or unobserved evidence fails closed.
13. A source fallback test proves no surrounding note text is fabricated when only a quote is available.
14. Source notes remain byte-for-byte unchanged during integration tests.
15. The implementation adds no runtime/provider/auth/memory/MCP redesign and no write-capable source path.
16. Relevant automated tests pass and browser screenshots are reviewed.
17. Gemini receives the Design Spec plus current screenshots for up to three meaningful reviews; high-value feedback is addressed and consciously deferred feedback is recorded.

## Approval record

The user delegated V1 direction selection to Codex + Gemini and explicitly asked Codex to continue through implementation, screenshot review, and final engineering report without per-visual-decision involvement. The selected direction remains within that authorized milestone and introduces no materially different product behavior.
