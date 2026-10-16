# Adaptive Planning: delivery and live evaluation

The opt-in Unified Ask fallback increases strict first-trial success from 25/60
to 33/60 (+13.3 percentage points). Nine previously unrecognized questions now
complete supported investigations with validated plans, grounded facts and an
accepting Reviewer. Reliability remains insufficient for default enablement.
`--adaptive-planning` is **off by default**; integration review is pending.

## Product and safety boundary

The baseline already had model proposal/repair and general TaskPlan/v2 validation
inside Runtime, but Unified Ask source investigation supplied rule plans only.
The new `source-relations/v1` profile translates unfamiliar wording into the
existing project → owner → direct leader or alias → project → owner → direct
leader paths. Every proposal and repair passes general and stricter product
validation before tools. The root must occur literally in the trusted query;
dependent entities come only from the immediately preceding verified read.
There are no additional relations, tools, branches or hops.

Reviewed decisions and recognized rule paths retain their existing routes.
Ambiguous reviewed scopes require clarification. Workspace, date, external-call
permission and read-only admission precede model creation. Source runs do not
capture Decision Memory. A decline is not a valid executable plan or success.
The existing Runtime, Search, Verified Read, Grounding and Reviewer own execution.
Runtime remains bounded to 12 turns, with at most two additional planning calls
(proposal and one repair). No generic Agent framework was introduced.

## Frozen evaluation and results

Sixty new synthetic tasks are split 24 development / 18 validation / 18 heldout.
They vary language, clause order, aliases, document layouts, stale/noisy facts,
ambiguity, dates, missing roots and unsupported requests. Heldout hashes are in
`tests/fixtures/adaptive_planning/freeze_heldout.json`; production behavior was
frozen before its execution. Gold answers and next-hop entities were never sent
to the planning model.

| Primary first-trial measure | A: rules | B: adaptive |
|---|---:|---:|
| Strict tasks passed, all 60 | 25 | 33 |
| Complete supported answers, 33 expected | 8 | 17 |
| Correct insufficient answers, 6 expected | 3 | 3 |
| Correct refusals, 21 expected | 14 | 13 |

A shares B's observed execution only where both use the identical deterministic
plan and Runtime branch. Unsupported A queries use independently observed HTTP
zero-provider refusals. These are paired observations, **not 60 independent live
A trials**. Six additional live A repeats passed 3/6, with 34 generations; they
remain separate and demonstrate execution variance.

B's strict partition results are 14/24, 10/18 and 9/18. Its 60 first trials contain
22 COMPLETED, 18 FAILED, 15 NEEDS_SCOPE, 3 INSUFFICIENT, 1 PROVIDER_UNCERTAIN and
1 CAPTURE_FAILED. A COMPLETED status alone never counts as strict success.
The strict new successes are D05, D11, D12, D15, D18, D19, D22, H03 and H08.
D17 has correct facts but its gold includes an unrelated noise-file requirement;
it remains a strict failure. V18 lost a required foreign-workspace qualification
in validation; the admission guard was fixed before heldout, and the original
failure stays in the score.

There were 23 model-planning tasks / 23 planning rounds, 227 Runtime turns and
four failed tool calls in B. Median planning-provider latency was 7.48 seconds
(4.76–12.26 seconds). B's overall median HTTP latency was 43.73 seconds, mixing
refusals and investigations; this is not a causal latency estimate against A.
Common failures include Chinese entity suffix mismatch, date false positives
around “then”, unsupported response formats, ambiguous entities, dependency
arguments and final-scope mismatch. Invalid or ungrounded plans never become
successful tasks. Offline contracts exercise invalid plans, repair, permission,
date/workspace scope and ambiguity, including zero-call rejection.
Saved HTTP route admission was correct for 58/59 captured first trials (D10's
missing capture is unscored). This only measures reaching source admission or
rejecting before tools, not plan correctness or completion; `routing.json`
records each route and proposal/rejection event separately.

C executes BM25 search, verified reads and a single real summary generation on
the same 60 questions. Its frozen minimal prompt omitted explicit relation
direction definitions: reversed owner edges were correctly rejected by grounding.
It scored 0/60 (55 INSUFFICIENT, 4 COMPLETED, 1 FAILED). C is diagnostic evidence
of this weak baseline, **not evidence that Agent outperforms a properly specified
retrieval baseline**. Its flat output also lacks a faithful clarification route.
The first seven C trials used trial 1; the remaining 53 used trial 2 because a
strict uncertain B trial locks that task/trial across phases. No C question was
repeated or selected by best outcome. A proposed revised-prompt rerun was rejected
by automatic approval review as outside the frozen comparison scope; that change
was reverted and only the original comparison was completed.

## Real provider accounting

| Item | Recorded value |
|---|---:|
| Model | Gemini 3.8 Flash |
| Total generation requests | 353 / 400 |
| Known token-cost estimate, thinking included | $2.2618395 |
| Budget committed, unknown usage charged full reserve | $2.9806875 / $15 |
| First to last request | 04:17:49–05:53:04 UTC, 2026-10-16 |
| Elapsed request window | 95 minutes 15 seconds / 3 hours |
| Strict Provider Outcome Uncertain | 8; maximum consecutive streak 1 |
| SDK automatic retries | 0 |

All 353 reservations have terminal records. Nine requests have unknown cost;
their full $0.079872 reserve is retained, including one non-strict unknown.
There were no pending reservations and the two-consecutive-uncertain stop was
not reached. Trials were serial, at most two per case, with a 130-second request
timeout. The 259 B-phase requests include preflight and the separate seven-call
browser repeat; A used 34 and C used 60. Ledger history was never reset.

Preflight pinned the official Developer API endpoint and SDK 2.22.0, verified
permission/compatibility and bounded input/output costs before generation.
The SDK does not expose the billed credential principal; account ownership was
not independently attested. Prices are estimates from the [official pricing
page](https://ai.google.dev/gemini-api/docs/pricing.md), not a billing invoice.
See committed `preflight.json`, `request-costs.json` and `results.json` under
`docs/evaluation/adaptive_planning/`. The raw new Ledger remains local under
`work/adaptive-planning/provider.jsonl`; old experimental Ledgers were untouched.

## HTTP and browser acceptance

The real Ask form executed “Who manages Marble Gate, and who is that manager's
direct supervisor?” through model planning → Runtime → Search → Verified Read
→ Final → Grounding → Reviewer → UI. It found Nora Bell and then Evan Grey,
with one planning call, six Runtime turns, two searches and three verified reads.
The accepted Final, source quotes and model-plan label are in `browser-final.json`
and `browser.png`. This is D10 trial 2. Trial 1's Windows artifact-copy failure
lost its HTTP Final capture, so it stays CAPTURE_FAILED in the primary score.

## Verification and remaining work

See [VALIDATION.md](VALIDATION.md) for focused passing checks and historical
suite limitations. Earlier independent reviews found and helped repair routing,
Ledger persistence and strict scoring defects. The last independent acceptance
audit could not run because its agent hit the account usage limit; no final
independent sign-off or CI pass is claimed. The primary agent checked saved
accounting, first-trial denominators, frozen scope and final diff.

Reproduce offline aggregation with `PYTHONPATH=src;.;tests/integration` and
`python scripts/adaptive_report.py --output work/new-evidence.json` (new path).
Fresh authorized live runs use `scripts/adaptive_live.py --preflight`, then
`--partition dev --variants B --trial 1 --run UNIQUE`; heldout requires
`--final-acceptance`. Existing terminal task identities must not be replayed.
The feature requires existing temporal/external-provider flags alongside
`--adaptive-planning`; request-level external permission still applies.

The branch is ready for integration review with default-off behavior. Remaining
work is reliability improvement and a newly approved, correctly specified C
comparison; this delivery does not claim universal natural-language support.
