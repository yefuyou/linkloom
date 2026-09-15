# LinkLoom Product UI V1 — Implementation Plan

## Increment 1 — Contract and fixture baseline

1. Define public UI payload contracts and fail-closed result projection tests.
2. Add a truthful `mps-001` success fixture generated from real note excerpts and a separate insufficient-evidence fixture.
3. Implement evidence collection from runtime tool-ledger records and optional verified full-source loading.
4. Prove missing/unobserved refs and invalid results do not render.

Done when focused unit tests pass and no frontend exists beyond the selected visual specification.

## Increment 2 — Runtime and local HTTP boundary

1. Define `RunBackend` and workspace context contracts.
2. Implement `RuntimeRunBackend` around an injected, already-configured `RuntimeEngine`.
3. Implement deterministic `DemoRunBackend` for browser review states.
4. Implement local standard-library HTTP routing, safe static asset serving, request validation, and API errors.
5. Add a `linkloom ui` preview entry point without provider configuration changes.

Done when API/adapter integration tests cover initial, running, completed, insufficient, invalid contract, and runtime error behavior.

## Increment 3 — Product UI

1. Build semantic HTML shell, initial/query, running, decision brief, evidence inspector, insufficient, empty, and error views.
2. Implement the Gemini design system and desktop 60/40 structure.
3. Implement citation synchronization, source navigation, provenance disclosure, focus behavior, and responsive inspector sheet.
4. Keep all UI rendering driven by API payloads; no `mps-001` branching in renderer code.

Done when browser interaction tests and accessibility smoke checks pass.

## Increment 4 — Visual review loop

1. Capture initial, running, success, and insufficient screenshots at the reference viewport.
2. Send screenshots plus Design Spec to Gemini through Antigravity CLI for critique.
3. Apply only high-value, scope-safe feedback.
4. Repeat at most two additional times if material issues remain.

Done when the decision/evidence relationship, uncertainty hierarchy, density, and demo comprehension meet the design acceptance checklist.

## Increment 5 — Acceptance and handoff

1. Run focused UI tests, runtime integration tests, frontend/browser checks, and relevant existing regression tests.
2. Inspect the final diff and preserve pre-existing unrelated worktree changes.
3. Record Gemini’s final critique, resolved issues, deliberate deferrals, changed files, test evidence, and known limitations.
4. Stop without expanding product scope.

## Planning reflection

- Product design and engineering responsibilities are separated: Gemini owns direction/spec/visual critique; Codex owns contracts, implementation, and verification.
- The thin adapter extends the existing runtime instead of creating a parallel decision engine.
- The live gateway requires an injected configured engine, avoiding a new provider/credential bootstrap in UI code.
- The deterministic demo backend is explicit and replaceable, preventing sample facts from entering business rendering logic.
- Python standard library HTTP keeps the dependency surface unchanged.
- The incremental-implementation skill references a `definition-of-done.md` file that is absent from that skill package; this plan uses LinkLoom’s project acceptance checklist and the executable acceptance criteria above instead.
