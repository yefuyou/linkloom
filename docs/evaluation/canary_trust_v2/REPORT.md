# Canary trust repair and complete AMI diagnostic, 2026-10-23

**DEV_DIAGNOSTIC / INDEPENDENT_REVIEW_NOT_VERIFIED**

Both complete meetings finished: ES2002a 8/8 chunks, ES2005c 20/20 chunks.
There were 28 attributable successful Provider receipts, no retry, no failure,
no UNKNOWN, and no unattempted planned request. USD 7 remained reserved; the
receipt-based standard-price estimate is **USD 0.14216775**. Actual billing and
promotional-credit attribution remain unverified. Billing Console was not used.

## Identity and launch evidence

- Remote master observed at start:
  `71ccecf937ffc0deb127e252eeff56df00dd4723`.
- Local master at start: `c2b4e7721e4e8b17c7ffdbd3e2035e9db42b4dd2`.
- Historical A–E evidence: `f26191487f459d7af7016898422b9bbcfa1a5cee`.
- Frozen repair/runtime/analyzer source:
  `1ade9b2b359b0249ed21678ad09e6aa4d5e82c83`.
- The candidate has a later documentation-only correction distinguishing the
  old null-profile AMI run from sprint B. The live code remains the frozen SHA.
- Fresh execution Worktree: `work/canary-trust-execution-1ade`.
- 181 frozen launch checks passed: quote scopes 20, Runner/history 27,
  source authorization/UNKNOWN/P4 and related contracts 134. No skips.
- Frozen null-profile replay consumed all 28 original normalized outputs with
  byte-equivalent original requests, using the normal F → Discovery chain.
- Gemini `gemini-3.8-flash`; SDK `google-genai 2.22.0`; 4,096 output tokens;
  low thinking; temperature absent; one F and SDK attempt. No Compactor,
  strong-model comparison, extra probe or meaningless quota-filling request.
- Every request has a persisted run ID, source hashes, request/config hash,
  frozen code manifest, runtime identity and reservation before transport.

The first attempt to start the process in the earlier Worktree failed at Python
import on a conflict marker, before SDK construction or Provider attempts. That
directory also contained unrelated staged changes. Its contents were preserved;
the exact committed repair was checked out into a new clean Worktree, and all
181 launch checks plus the complete frozen replay were regenerated there.
This bootstrap failure is not silently counted as a paid request or UNKNOWN.
No already-sent request was replayed. Local forensic snapshots remain in the
earlier Worktree's `work/source-drift-01/`.

Additional broad Core checks initially passed 226 cases with 77 environment
failures (sandbox localhost denial/temp ACL and temp-root rules). Those 77
passed under normal local permissions with a synthetic temp root. These
separate runs are supplemental evidence, not a single frozen CI qualification;
the 181 checks in the clean execution Worktree are the launch evidence.
Remote CI and independent semantic review are not claimed verified.

## Historical provenance and attempt correction

The read-only historical ZIP SHA-256 is
`61d2c582df96d1e0f399ac06128f4cfd8e231c2e9ce0bf5440cc9e707711d5ac`.
The new analyzer derives the following from the 58 individual reservations,
their SDK/wire members and original Session receipts:

| Phase | Reservations | SENT_SUCCEEDED | SENT_FAILED | NOT_SENT | OUTCOME_UNKNOWN |
|---|---:|---:|---:|---:|---:|
| A | 16 | 16 | 0 | 0 | 0 |
| B | 21 | 20 | 0 | 0 | 1 |
| C | 10 | 10 | 0 | 0 | 0 |
| D | 6 | 5 | 0 | 1 | 0 |
| E | 5 | 5 | 0 | 0 | 0 |
| Total | 58 | 56 | 0 | 1 | 1 |

B's `ConnectError`/TLS failure has no HTTP or attributable Provider response.
Its old Session classification remains recorded, but the v2 transport outcome
is UNKNOWN. D's second reservation has positive parameter-mapping replay proof
(`temperature_unsupported_for_gemini_3`) and no second wire request, so v2 marks
NOT_SENT while retaining the old UNCERTAIN ledger. All USD 14.40 historical
reservations remain counted; the historical successful-receipt cost estimate
is USD 0.38359475, not proof of the unknown attempt's billing.

Six early Runner declarations refer to the same original source path,
`work/cloud-credit-sprint-20261023/campaign_support.py`. The only archived source
at that path is final SHA-256
`42c43010af4824b5f369163996b4a83ac0e6f5a41cfe2728599915e0e1f7ff65`.
It is not a substitute for any of the following unavailable versions:

| Declaration under `work/` | Declared source SHA-256 | Available source | Status |
|---|---|---|---|
| `ce-b-20261023/authorization.json` | `a2b78510ca9088afc9356be3bd9a63b07d6b47f7a1d0605997078cc7659db352` | final file above; hash differs | NOT_VERIFIED |
| `ce-b-20261023-tls-recheck/authorization.json` | `4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4` | final file above; hash differs | NOT_VERIFIED |
| `ce-b-20261023-unattempted/authorization.json` | `4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4` | final file above; hash differs | NOT_VERIFIED |
| `ce-c-20261023/authorization.json` | `4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4` | final file above; hash differs | NOT_VERIFIED |
| `ce-c-20261023-resume/authorization.json` | `4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4` | final file above; hash differs | NOT_VERIFIED |
| `ce-d-20261023/authorization.json` | `4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4` | final file above; hash differs | NOT_VERIFIED |

The initial D adapter also declares
`9af19e3ba06813d2318bb11bba4c72024cf1ca470e600a7b5ca16bcc2bc8cde6`,
while the archived final `independent_model.py` is
`23e5069706216f40bb079f4eb945be344389121da0014e4ec1ff77bafc8feceb`.
This additional gap remains NOT_VERIFIED. Exact member paths and hashes are in
[HISTORICAL_AUDIT.json](HISTORICAL_AUDIT.json).

The historical `analyze_continuation.py:148` and `finalize_evidence.py:48`
hard-coded `unresolved_provider_outcomes=0`. Their original bytes and derived
reports remain preserved. V2 never imports or executes those scripts and
derives the unresolved count as 1. No historical report, old ledger, original
evaluation output or README score was edited.

## F proposals and Discovery outcomes

These are distinct contracts and populations, not a combined score:

| Population / contract | Raw F task proposals | Raw F DECISION | Discovery |
|---|---:|---:|---|
| Old complete AMI, `task_profile=null` | 5 PLAN | 5 | 0 valid / 0 partial; NON_ASSERTED_TASK 4, exact grounding 1 |
| Same old outputs replayed at repair SHA, still null | same 5 | same 5 | 0 valid / 4 partial; exact grounding 1 |
| New complete AMI, TASK_RECALL_V3 | 5 (ACTION 4, PLAN 1) | 0; decision coverage N/E | 0 valid / 3 partial; exact grounding 2 |

The old null-profile F produced 75 raw claims, including 23 PROPOSAL claims;
the new v3 F is task-only and produced five raw claims. All five new raw claims
survived F parsing; no F parse failure or raw-to-F loss occurred. Both fresh
null and v3 request plans are archived. Old outputs were never relabeled v3.

For completeness, the archived A–E sprint's raw F counts are separately
preserved in [HISTORICAL_F_COUNTS.json](HISTORICAL_F_COUNTS.json): A has 3 F
receipts / 6 tasks / 0 DECISION; B 14 / 13 / 0; D machine silver 2 / 3 / 0.
Those populations mix synthetic, Product and AMI inputs and models; they are
not the old complete two-meeting null-profile run. C and E are not F extraction
benchmarks. The historical B AMI prefix retained one industrial-design task
and rejected its marketing proposal; unfinished ES2002b remains N/E.

## Case diagnosis: model omissions versus program losses

| Case | Old complete null-profile AMI | Frozen replay / new v3 evidence | Remaining limitation |
|---|---|---|---|
| Clay model | F found the plan; Discovery falsely vetoed it after `'Kay` opened a quote to end of chunk | Frozen replay admits partial; new raw quote at ES2005c chunk 17 / request 26 also becomes partial | Raw owner `you guys` is rejected as an unsupported role binding; owner stays UNKNOWN |
| Industrial design | Not emitted as a task | New ES2002a chunk 6 / request 7 raw ACTION passes; source role `industrial designer` retained | Deadline/project/vendor remain UNKNOWN; no person is invented from the role |
| Marketing | Not emitted as a task in this null-profile run (separate historical sprint B did emit it) | New request 7 emits it, but task/role quotes contain literal `\\n` and omit native turn headers | Correct exact-grounding rejection; model quote formatting and cross-turn witness are the remaining loss, not apostrophe masking |
| Interface design | No task proposal; official linked DA is the fragment `Um for user interface , technical functions ,` | No new raw task proposal despite full transcript input | Model has not recovered a task from the surrounding fragmented turn; no downstream program candidate existed to accept |
| Speech recognition | General F emitted FACT/PROPOSAL material, not the explicit decision | New v3 excludes general decision extraction by contract | New decision recall is N/E; cannot call zero DECISION a v3 regression or silently reuse a general F denominator |
| Public survey | Old general F includes a design/public-choice decision | New request 19 emits an ACTION plus cross-turn assent, but the raw quote uses literal `\\n` throughout | Correct exact-grounding rejection; no normalization or fabricated source stitching was applied |
| Record attendance | Not an old task proposal | New request 1 quotes `record who's here` exactly and retains partial | Raw speaker-owner `B` is not supported by the ordinary document's canonical speaker contract and is cleared; non-exhaustive Gold cannot label this extra administrative action a false positive |

The old general-F finance discussion, next-meeting timing, technical presentation
and remote-control reuse proposals are not automatically labeled false negatives
under a different task-only profile. No exhaustive human task/owner/date labeling
was created. The UI and speech cases above are diagnostic non-coverage findings,
not formal FN counts.

New accepted observations have three distinct existing v2 identities. No task
identity implementation was changed for B01. All retained items are supporting,
UNCONFIRMED partial observations; no business task was approved or published.
No new accepted wrong-role or date binding was observed in these three items.
The industrial role is literal source text; the two unsupported owner proposals
were cleared. All raw and accepted deadline/event-date fields remain null, so no
meeting date or relative time was fabricated. This narrow inspection does not
establish a general false-positive rate.

## Keep the denominators separate

| Check | Result | Interpretation |
|---|---|---|
| Old complete null-profile task quote + description validation | 4/5 | Mechanical exact/unique source checks |
| New v3 task quote + description validation | 3/5 | Mechanical checks on different proposals, not semantic accuracy |
| Historical sprint C quote structure | 19/20 | Preserved historical quote validation, not relation correctness |
| Historical D final-view/verdict agreement with Flash | 4/4 | Machine-silver consistency only; not Gold or business approval |
| New independent verdict consistency | N/E | No independent verdict labels |
| Formal semantic Precision / Recall / F1 | N/E | AMI summary-to-dialogue-act links are not exhaustive semantic task labels |
| Business approval correctness | N/E | No independent business qualification; nothing approved |

Four false NON_ASSERTED_TASK vetoes disappear when the *same old null-profile
outputs* are replayed. That isolates the code repair from model variability.
Paired nested real quotations and unclosed quotation refusals remain protected
by the frozen tests; neither source authorization nor UNKNOWN/P4 is relaxed.

## Cost, integrity and delivery

The new run reports 175,017 input tokens, 2,908 candidate output tokens,
177,925 total tokens across 28 successful receipts. All 28 thought-count fields
remain null (not reported); total minus input and candidate output is zero.
The estimate uses USD .75 / 3.75 per million input / output tokens,
checked against [official pricing](https://ai.google.dev/gemini-api/docs/pricing)
on 2026-10-23. USD 4 / 20 conservative rates and at least USD .25 per request
governed the USD 7 reservation. No missing receipt was interpreted as free.
The old complete AMI run separately had 28 receipts, 156,052 tokens and an
estimated USD 0.142125. These are not billing-console or credit-deduction values.

The new sealed raw run, request identities, budgets, wire/SDK receipts, F and
Discovery output, frozen source bytes, full transcript inputs, null replay,
launch checks and analyzer sources are in `evidence.zip`. The manifest permits
offline byte verification without SDK or network. Historical missing sources
remain missing; the new run does not retrospectively authenticate them.

Repair candidate: Draft PR #20. New results use a separate branch/stacked Draft
PR. Neither is merged. Milestone implementation, specified launch tests, complete
two-meeting diagnostic and evidence checks are satisfied; independent semantic
qualification and formal metrics remain unverified. The next useful step is a
separately scoped exact cross-turn quote/role witness investigation, not more
requests in this batch or relaxation of the Grounding guard.
