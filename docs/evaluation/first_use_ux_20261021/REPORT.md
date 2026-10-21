# First-use UX fixes — 2026-10-21

Scope: the three confirmed first-use issues from the local 2026-10-21 acceptance
report. Baseline: latest remote master `ca2e775ce17581f8b69b2e1f9daf813a1230fb1c`.
This is bounded UX verification, not full product or live-model acceptance.

## Before / after

| Issue | Before | After |
|---|---|---|
| Review relation | Required Canonical relation input although materialization rejects human relation rewrites. A missing relation-scoped key also exposed an already-resolved subject. | Remove relation input, disable unresolved-relation approval, explain subject/time-only corrections, original-source inspection, re-import for another extraction and reviewer/reason-bound rejection. Project unresolved subject from entity resolution. Authorization is unchanged. |
| No Provider | Import and extract remained enabled despite unavailable extraction. | Save source for later extraction. Detail confirms local persistence while retaining FAILED and PROVIDER_UNAVAILABLE. Ready Provider keeps Import and extract with source-content disclosure. |
| Topbar | Provider text forced horizontal page overflow: at 900px, scroll/client width 1417/885; at 1440px, 1518/1425. | Wrap topbar and Provider text, allow flex children to shrink, retain workspace selection and navigation. |

All linked images are actual Chrome screenshots with synthetic data:

| Before | After |
|---|---|
| [Sources / 900px](before-sources-900.png) | [Sources / 900px](after-sources-900.png) |
| [Unresolved relation](before-unresolved-review-1440.png) | [Unresolved relation](after-unresolved-review-1440.png) |

Also: [390px Sources](after-sources-390.png),
[saved without Provider](after-no-provider-saved.png), [768px Ask](after-ask-768.png).

## Browser verification

Headed Chrome through Playwright CLI used current production HTTP/UI with either
no extractor or an injected local scripted extraction adapter. No external or
paid Provider was invoked. The adapter is test-only, not a new product mode.

- Submitting without a Provider preserved source text and a verified evidence
  span. Detail displayed Source saved, FAILED and PROVIDER_UNAVAILABLE together.
- The unresolved-relation page had zero relation inputs, zero already-resolved
  subject inputs and disabled approval. Open original source showed the exact
  original text. Reject succeeded with reviewer and reason while effective time
  was empty; Decision Memory remained empty.
- Sources and Ask were navigated at each width below. Provider text remained
  within the topbar, workspace selection was visible and navigation did not
  overlap main content. Narrow navigation retains its existing local scroll.
  Screenshots were inspected; source assertions were not used as visual proof.

| Viewport | Sources scroll/client width | Ask scroll/client width |
|---|---|---|
| 390 | 375/375 | 375/375 |
| 500 | 485/485 | 485/485 |
| 768 | 753/753 | 753/753 |
| 900 | 885/885 | 885/885 |
| 1440 | 1425/1425 | 1425/1425 |

Client width excludes Chrome's vertical scrollbar. The full screenshot matrix
and layout measurements remain locally under `output/playwright/` and
`work/first-use/layout-results.txt`.

## Tests and independent review

- `node --check src/linkloom/ui/static/product.js`: PASS.
- `node tests/ui/first_use_dom_test.cjs`: PASS; included in offline PR CI.
- `node tests/ui/unified_ask_dom_test.cjs`: PASS.
- `pytest tests/integration/test_product_application_flow.py tests/integration/test_product_segment_recovery.py tests/unit/test_semantic_ingestion_materialization.py tests/unit/test_ui_localization.py -q`: **65 passed, 1 failed**.
- After the subject projection correction, new HTTP cases + unresolved-time HTTP
  case + all materialization tests: **36 passed**, `work/first-use/focused.xml`.
- `git diff --check`: PASS.

Independent Reviewer initially requested the subject projection correction,
then returned **PASS** after the fix and regression. Review covered UI/backend
capability claims, immutable extraction, Review authorization, saving/error
semantics, actual layout screenshots and Ask DOM regressions. Hosted CI results
are reported separately in the Draft PR checks.

## Limits and blocked scenarios

The broad local run reproduced the previously reported
`test_temporal_http_provider_unknown_keeps_read_source_without_verified_final[1]`
three-second HTTP polling timeout. It is not counted as passed or attributed
to these changes. The original acceptance report records the same case;
investigation and P1 code remain untouched.

The in-app browser initially timed out: **BLOCKED_ENV for that route**. An
initial Playwright flow also raced initial page loading; it succeeded after
waiting for initialization. Neither failed attempt counts as acceptance.
Independent Chrome completed the three requested UX checks, so those checks
are no longer environment blocked. Initial no-workspace pages retain the
existing handled 409 from the review-count request.

Live extraction quality, full current/historical Ask, source-update/stale
revalidation and PARTIAL/UNKNOWN/BLOCKED investigation browser scenarios were
not rerun. Earlier incomplete E2E scenarios remain incomplete; this report
does not upgrade them to passed.

No real vault, PR #6 branch, task-investigation implementation, frozen evidence
or in-progress P1 repair was modified. Full local browser scripts and receipts
are retained under `work/first-use/`.
