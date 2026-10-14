# Natural-Language Decision Discovery delivery

Base: `b766f055bde89fa6202c480339eda58f46108fe7`.
Branch: `codex/natural-language-decision-discovery`.

## Demo

From this branch's checkout:

```powershell
$env:PYTHONPATH = "src;tests/integration"
$env:LINKLOOM_RETRIEVAL_MODE = "bm25"
python tests/integration/run_unified_ask_demo.py --discovery --port 8798
```

Open `http://127.0.0.1:8798`, select Ask in Atlas. Both extraction and
investigation are offline fakes; no API credentials or network Provider is used.

| Question | Actual result |
| --- | --- |
| 我们现在采用哪家供应商？ | Launch / uses supplier; Wrenwell; deterministic, zero model calls, receipt/source/Timeline |
| 2026年10月6日这个项目用的是哪家供应商？ | Birchline at 00:00 UTC; deterministic, zero model calls |
| 为什么我们从 Birchline 换成了 Wrenwell？ | Existing FakeTransport Agent; Validator/Reviewer accepted; formal replacement verified, replacement reason UNKNOWN |
| 上个月这个项目用的是哪家供应商？ | Exact-date clarification; no invented date or current answer |

Other contains Support/Riverton and Expansion/Northstar. A generic supplier
question shows both; selecting Support resumes the same question. Research/Mallow
is an unapproved proposal and cannot become a trusted scope. Atlas's Cedar
proposal remains clearly unapproved, never the effective conclusion.

## Verification

- Discovery/routing unit tests: 49 passed, including review-reproduced unknown
  lowercase/Chinese/mixed objects and relative/range/ISO timestamp regressions.
- New discovery and existing Unified Ask HTTP tests: 21 passed. Model permission,
  valid-time, rejected evidence/receipts, UNKNOWN cause, legacy APIs, workspace
  isolation, approved future-only scope compatibility and UNCERTAIN without Final
  remain covered. Final interval-date HTTP probes also passed.
- Broader focused trust regression: 118 passed; 2 existing tests could not run
  their assertions because ignored frozen `work/temporal_decision_eval/GOLD.json`
  and `CASES.json` are absent in the base checkout. No frozen fixture was copied,
  edited or regenerated. This is not a whole-repository green claim.
- DOM checks passed: query/date/scope invalidation, latest response wins,
  workspace stale response rejection, candidate then date continuation,
  fresh question rediscovers, external permission resets per request.
- Actual in-app browser: current/historical/Fake Agent queries, relative date
  clarification, candidate choice, unapproved/no match, workspace switch and
  UNCERTAIN were executed, including candidate then exact-date clarification.
  Local screenshots are retained under `work/browser/`.
- JavaScript syntax and `git diff --check` passed. No real vault, real Gemini,
  Runtime, Store, Validator or Reviewer implementation was changed.
- Independent bounded review passed after the unknown-object, date-range and
  candidate/date continuation findings were reproduced and repaired. No blocking
  finding remains in this feature boundary.

Windows note: sandbox HTTP tests needed localhost socket permission. Runtime
temporary artifacts use the default short Windows TEMP path; pointing TEMP into
the deeply nested worktree exceeded Windows path limits in an initial run.

## Implementation and boundary

`UnifiedAskService._trusted_slots()` reuses `recover_decision()`'s checked history.
`discover_scope()` intersects recognized normalized subject/value/relation terms
and aliases; unexplained wording requires explicit scope. It never selects by
rank. `resolve_query_date()` accepts exact dates/aware timestamps, asks for a
point when time is ambiguous, and retains existing as_of semantics. The UI keeps
question-bound clarifications without locking a later question to the old slot.
Approved scheduled records can identify a trusted slot; their future validity is
never substituted for the user's query date or displayed as current truth.

Single-slot, conservative lexical discovery only. It may require manual scope
for unfamiliar synonyms or wording. Relative periods/ranges need an exact point;
multi-decision investigation, arbitrary natural-language understanding and
causal inference without evidence are not supported. Model configuration and
per-request external permission are still required for paid investigation.
