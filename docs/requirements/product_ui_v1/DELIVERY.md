# LinkLoom Product UI V1 — Delivery

## Status

The approved Product UI milestone is complete. Gemini owned the product, UX, and visual direction through Antigravity CLI; Codex implemented the selected direction, runtime projection, local UI boundary, verification, and review fixes.

## Design

- Selected direction: **Decision Brief + Split Source Inspector**.
- Rationale: it makes the final decision and its proof understandable together within the 30-second demo target, maps cleanly to the existing `TeamDecisionResult`, and avoids turning LinkLoom into a chat surface or runtime console.
- Source artifacts: `DIRECTIONS.md`, `DIRECTION_SELECTION.md`, and `DESIGN_SPEC.md` in this directory.
- Review record: `GEMINI_REVIEW.md` in this directory.

## Implementation

- `src/linkloom/ui/projection.py`: strict, provider-neutral projection from durable runtime facts into a safe product payload. It accepts only observed verified evidence and fails closed on invalid results or missing claim evidence.
- `src/linkloom/ui/backend.py`: thin asynchronous adapter around an injected, already-configured `RuntimeEngine`; no Provider or Runtime redesign.
- `src/linkloom/ui/demo.py` and `demo_data/mps-001.json`: isolated UI-development fixture derived from the real frozen Atlas Lantern notes. Only the two documented fixture questions replay; arbitrary questions fail truthfully instead of receiving a fixed answer.
- `src/linkloom/ui/server.py`: localhost-only JSON/static server with payload limits, JSON media-type enforcement, Host validation, CSP, and safe errors.
- `src/linkloom/ui/static/`: semantic initial, running, successful result, insufficient-evidence, empty, and operational-error views; synchronized claim/source citations; collapsed provenance; responsive evidence inspector.
- `python -m linkloom.ui --port 8765` starts the deterministic local UI-development preview.

## Screenshots

- `output/playwright/initial.png`
- `output/playwright/running.png`
- `output/playwright/success.png`
- `output/playwright/insufficient.png`
- `output/playwright/error.png`
- `output/playwright/mobile-actions.png`

All primary screenshots were captured at 1440 × 1024 with installed Chrome. The 390 × 844 responsive flow was also checked interactively: selecting a citation opens the fixed evidence sheet, moves focus to the inspector title, preserves the selected line range, traps focus, and closes with Escape.

## Design review

Round 1 fixed the incomplete source range, below-fold unresolved content, missing inspector line range, ambiguous outcome/evidence accents, and excessive heading repetition.

Round 2 reviewed all five states and returned no P0 finding. It explicitly recommended **SHIP FOR PORTFOLIO & DEMO — V1 approved**. Its three P1 findings were resolved:

- only the clicked claim receives the prominent evidence highlight;
- the insufficient-evidence conclusion is no longer repeated under unresolved items;
- inspector support labels use concise semantic categories.

No third review round was necessary.

A focused pre-merge responsive audit subsequently found and closed one mobile
Actions issue: stacked rows now retain visible Action, Owner, Due, and Status
labels, and the small desktop headers meet AA contrast. Gemini reviewed the
390 × 844 result, found no new P0/P1 issue, and returned `MERGE`.

The independent engineering review then closed three non-visual product
boundaries before integration: retry now re-fetches workspace context after an
initial connection failure; untrusted Runtime messages are never exposed in
technical details; and the mobile evidence inspector is inert and hidden from
navigation until it opens as a focused modal dialog.

## Verification

- 48 focused and adjacent regression tests passed across strict `TeamDecisionResult`, verified evidence context, vertical slice, final-contract communication, UI projection/demo, live `RuntimeEngine` integration, HTTP behavior, mobile action-label preservation, and safe retry/error/modal behavior.
- `RuntimeRunBackend` completed a real synthetic-vault run and proved the source note hash did not change.
- Browser review verified action and decision citations, exact source lines 10–12, responsive open/focus/Escape behavior, error alert semantics, and query-field descriptions.
- Browser failure injection verified that an initial context 503 recovers to the query screen after the server route becomes available, without reloading the page manually.
- `node --check src/linkloom/ui/static/app.js` passed.
- The broader unit + integration suite was attempted: 585 tests passed, while the remaining old tests were prevented from creating or cleaning their Windows temporary directories by the managed sandbox. The focused milestone suite is green; those environment failures were not caused by UI assertions.
- The frozen `mps-001` source notes have no Git diff.

## Review verdict

The five-axis engineering review found no remaining required correctness, accessibility, architecture, security, or performance issue in the Product UI V1 boundary. The implementation adds no runtime/provider/auth/memory/MCP changes and no write-capable vault path.

## Known limitations

- The module launcher is a repository-local UI-development preview; a host application must inject a configured `RuntimeEngine` to run the live backend.
- The deterministic demo intentionally supports only its two documented `mps-001` questions.
- The current `TeamDecisionResult` has no claim-level `inferred` flag. V1 therefore renders approved/partial decisions, unresolved items, and unknown fields explicitly, but never invents an inferred label; that taxonomy is reserved until the backend can state it truthfully.
- There is no auth, workspace management, deployment, or public multi-user server in this milestone.
- The UI loads cited full-note previews into the local browser; pagination and long-vault performance tuning remain outside V1.
