# Task investigation: answer first

The user's 2026-10-22 task is the approved scope. Base:
`bde9d9e2b88c2e69132cf48d1534dc28d4fca764`; branch `codex/task-answer-first-ui`.

## Boundary and architecture

Present the already validated Product TaskResult/TaskReview in the existing Ask
page. Extract the task result renderer into one static script; it receives the
existing escape, translation, date and citation helpers. No new dependencies,
model calls, persisted state or business inference. F, G, Runtime, providers,
SemanticProposer, P4 and the server's result revalidation stay unchanged.

The server owns validation and authority. The browser only formats returned
observations, proposals, step outcomes and current comparisons. It must not
merge same-name tasks, inherit fields across messages, calculate date deltas,
or infer a current owner, approved revision or effective deadline. Raw messages
are source statements; extracted field roles remain interpretations.

## Acceptance

- The first result screen states the bounded conclusion, formal-status limit,
  source fields and open questions in ordinary Chinese/English.
- PARTIAL preserves available source records. UNKNOWN never establishes absence.
  CONFLICT is attributed to the exact proposal or existing step reporting it.
  BLOCKED and rejected/unavailable reads display no previous records or quotes.
- SUPPORTED and EVIDENCE_VALID never imply business approval. Only returned,
  current-valid P4 comparisons from completed steps may display date differences;
  their currentness limits remain explicit.
- Each source observation retains its own fields and unknowns. Exact quotes,
  field bindings, versions, proposal evidence and execution details are expandable.
  Original source buttons use the existing source inspection route.
- Source updates/removal, evidence revocation, cold reads, workspace isolation and
  response races continue through existing checks; no result cache is added.
- Public synthetic data only. HTTP, DOM and real Chromium evidence, including
  before/after screenshots, are recorded. Independent review has no open P1/P2.

## Implementation and verification order

1. Renderer contract tests (RED), then bounded summary/source/proposal rendering.
2. Connect the extracted renderer to Product, retain all read/review gates and
   pointer-only recovery. Run DOM and affected HTTP/security regressions.
3. Exercise normal HTTP/Product flows in Chromium, inspect screenshots and DOM,
   independently review, fix findings, run affected regressions, commit and
   non-force push the requested branch. No PR or other branch changes.

No real vault, external provider, deployment or business approval is in scope.
