# Answer-first consolidation delivery

Base: `df39f0fe2b72dcb37deb0f4fd6f115123b5c6991` (already includes
`b766f055bde89fa6202c480339eda58f46108fe7`). Branch:
`codex/linkloom-product-consolidation`.

## Integration status

The add/modify portion of `4f18c4ffd64d8e0087d65aabcd532c56ea765714`
was applied precisely using `git diff --diff-filter=AM` and `git apply --3way
--index`; no conflicts. Shared atomic output preserves UTF-8/LF, replace and
failure cleanup; research exports remain lazy and callable. Discovery was not
replaced. Local budget commit `58ef237` is excluded; the verified remote Transport
tip was b766f055 at task start.

Full slimming integration is **incomplete**: automatic approval review rejected
recycling the retired characterization script and five tracked egg-info files,
requiring explicit approval for those six targets under the deletion rule. The
files remain present and tracked. A user approval request is pending; no deletion
or alternate untracking operation was performed. Existing worktrees were not edited.

## User-visible result

- Current Chinese supplier question: Wrenwell, effective 7 October 2026 UTC,
  reviewed formal decision, zero model calls, exact original quote first.
- Spaced Chinese 6 October question: Birchline; selected historical valid-time
  label and explicit caveat that this is not a historical knowledge snapshot.
- Passive replacement question through FakeTransport: verified Birchline →
  Wrenwell, accepted Validator/Reviewer, replacement cause prominently unknown.
- Ambiguity: friendly category/current value cards, no default first choice;
  keyboard selection retains the original question.
- Relative time: exact date required; confirmed Support scope survives date
  selection. 15 September returns no verified effective formal decision.
- Cedar remains an explicitly unapproved proposal in history.
- UNCERTAIN and non-accepted Agent payloads never render draft conclusions as a
  Final. No authorization produces a clear request-specific consent prompt.

Only existing result fields govern status. This change introduces presentation
helpers, not a second validator, store, schema or Runtime. Evidence order remains
the backend order; all original evidence, receipts and accepted result payloads
remain accessible without truncation. Source text and audit identities are not
translated. Chinese grammar/date changes are syntactic normalization only.

## Verification

- Unit: 70 passed across scope discovery (35), Unified Ask (16), product
  transport hooks (4), transport protocol (15).
- HTTP: Discovery (7) and Unified Ask (14) passed, covering legacy endpoints,
  workspace/date constraints, FakeTransport, proposal exclusion and UNCERTAIN.
- Original SDK FakeTransport HTTP suite: 2 passed, 6 timed out during SDK TLS
  context initialization before Provider execution. Thread dump identifies
  `ssl.create_default_context` in Google SDK initialization (11–14 seconds per
  measured CA load). A process-local wrapper that only raises the test HTTP
  client wait from 30 to 180 seconds then passed all 8 original tests in 376.70s.
  All original assertions, SDK construction, TLS checks, transport behavior and
  Provider budgets were unchanged. The default test command still encounters
  this local loading limit; no production timeout was extended.
- Slimming scanner/retrieval/CLI: 24 passed, 1 Windows symlink skip.
- Existing DOM race harness passed: stale query/workspace/date/scope responses,
  clarification continuation, request permission reset, localization, empty
  decisions, unknown cause, non-accepted/draft suppression and HTML escaping.
- JS syntax and diff whitespace checks passed.
- Isolated offline wheel install passed (`--no-index --no-deps`); installed CLI
  and UI help work; installed JS/CSS bytes match source; lazy public retrieval
  exports work. No globally installed package was modified.
- Actual browser: current/history/Agent running and accepted/unknown, ambiguity
  keyboard selection, exact-date continuation, no formal historical decision,
  unapproved proposal disclosure, Timeline, UNCERTAIN, permission denial then
  single permitted Fake request then denial again. Workspace switch clears old
  content. Rapid query/date/workspace scenarios and narrow long-content checks
  passed: later Expansion/Northstar query wins over Support; changing the date
  while a request is pending leaves no old answer; switching to Other during
  an Atlas investigation clears query/result and suppresses late polling.
  Core and stress-case screenshots are retained in ignored `work/browser/`.
- At 375/320 CSS pixels with long project, filename, receipt/reference and
  expanded audit, no horizontal overflow. Literal `<img ... onerror=...>` source
  stayed text; no image element or script dialog was created.
- One independent bounded review found no high-confidence UI/trust/race/escaping
  defect requiring a fix. Its additional Python run stopped at the same offline
  HTTP timeout; its DOM harness and whitespace check passed.
- Known old product-flow tests require ignored frozen Gold/CASES files; they
  were not regenerated or claimed passed. No frozen experiment data changed.

## Local demonstration (no API key)

From this branch's independent checkout:

```powershell
$env:PYTHONPATH='src;tests/integration'
$env:LINKLOOM_RETRIEVAL_MODE='bm25'
python tests/integration/run_unified_ask_demo.py --answer-first --port 8801
```

Open localhost:8801, select Ask. Atlas has Launch supplier history and budget;
Other has several reviewed projects, one unreviewed-only project and a long
content case. `--require-agent-permission --port 8802` exercises consent using
FakeTransport, with no real Provider socket. Demo data lives under ignored work/.

These are synthetic cases: deterministic receipt/source validation is real;
Agent generation is FakeTransport. Earlier V1 real Gemini acceptance remains
historical evidence, not end-to-end acceptance of the new Discovery/UI path.
Real Gemini generation requests in this task: 0. No new dependencies or Runtime
abstractions. Single-slot discovery and precise-date clarification boundaries
remain; no multi-decision causal reasoning is claimed.

Frontend delta from Discovery: JS +169/-60; CSS +51/-0 (net +160 lines).
Production file added: `text_output.py`; production files deleted: none, pending
recycle approval. No dependencies added. The 14 non-deleting slimming patches
were checked against the exact source commit, preserving baseline context.
