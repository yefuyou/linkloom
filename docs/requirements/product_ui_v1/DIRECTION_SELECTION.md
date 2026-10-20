# LinkLoom Product UI V1 — Direction Selection

Date: 2026-09-15
Decision owner: Codex engineering lead, based on Gemini design exploration
Status: Selected for V1

## Selected direction

**Decision Brief and Split Source Inspector**

## Why this direction wins

- It communicates the core product in under 30 seconds: decision on the left,
  proof on the right.
- It makes evidence primary without turning the page into an engineering
  evidence table.
- It maps directly to real `TeamDecisionResult` claims and real evidence
  records.
- It supports the complete `mps-001` story without inventing backend features.
- It has the smallest implementation and responsive risk of the three options.
- It yields a distinctive research-tool screenshot rather than an AI dashboard
  or chat transcript.

## V1 adjustments requested from Gemini

- Keep the 60/40 evidence relationship, but avoid a giant banner or boxed card
  around the decision. Use editorial typography and whitespace first.
- Show friendly citation numbers or source names, never opaque evidence IDs as
  the primary UI.
- Keep hashes and raw verification metadata out of the main surface. They may
  appear only inside a secondary technical detail disclosure.
- Do not display fabricated counts such as “three verified sources” unless the
  backend actually supplies the supporting records for that run.
- Preserve business uncertainty separately from runtime failure.
- Keep “How LinkLoom found this” collapsed by default and use user language:
  searched, read, checked, answer ready.
- Allow the initial/query state to remain visually quiet; the source pane may
  become useful only once evidence exists.
- Use real Atlas Lantern content only through demo fixtures or backend payloads,
  never through application business logic.

## Architecture fit

The V1 state boundary can remain small:

```text
workspace context + query
  -> product-level run state
  -> TeamDecisionResult + evidence records
  -> selected claim/evidence focus
```

The design requires no backend redesign, workspace-management system, auth,
write actions, memory, MCP, billing, or settings.
