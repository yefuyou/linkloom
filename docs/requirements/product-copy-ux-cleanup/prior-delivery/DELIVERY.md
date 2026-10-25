# Product copy and UI hierarchy delivery

**Implementation and offline verification complete; real-browser visual acceptance incomplete.**

Base at task start: `7720390a796662ff42c27b5eef6f8685e5155802`.
Branch: `codex/product-copy-ux-cleanup`. Independent worktree, no master/other PR
changes, no real Provider calls, keys or private data. Draft PR delivery only.

## Audit and changes

The source/template audit covered **37 page/state groups** and recorded **100
findings**. The counted units are baseline template locations/dispositions, with
same-location English/Chinese merged. Shared expressions count once; normal
translation entries are not multiplied to inflate cleanup totals.

| Disposition | Findings |
|---|---:|
| Delete | 20 |
| Merge | 17 |
| Rewrite | 55 |
| Move | 8 |
| Total | 100 |

See [COPY_AUDIT.md](COPY_AUDIT.md) for the state matrix, baseline file/line,
original copy, issue type, reason, disposition and authority impact.
[AUDIT_DATA.json](AUDIT_DATA.json) retains the full original/replacement fragments
and machine-checkable counts. [COPY_GUIDELINES.md](COPY_GUIDELINES.md) defines the
bilingual vocabulary and placement rules.

- `heading()` now takes one title and optional supporting text. Normal Sources,
  Review, Decision Memory and Ask have one primary heading. Removed decorative
  eyebrows and repeated subtitles rather than hiding their text.
- Task results lead with the actual observation count, partial/insufficient
  status, unconfirmed task relationships/effective plan and visible offline mode.
  Source cards precede outstanding questions and next actions. Unknown fields
  stay per-record; dates retain their original wording and unresolved roles.
- AI proposal verdicts stay explicitly attributed. Invalid evidence checks are
  labeled as failures, rather than presented as relationship support. Existing
  server acceptance and date-comparison gates are unchanged.
- Setup flags, token/request limits, raw enums, version identities, receipts and
  full traces use native disclosures. Cost limits and source-snapshot permission
  stay outside those disclosures, before submission.
- Non-Ask screens, navigation and common statuses now have literal-only bilingual
  copy. Interface language changes preserve entered text; Ask keeps its separate
  answer-language option. Source text, source names, model explanations and audit
  IDs are never translated or rewritten.
- Legacy Ask loses decorative title tiers and the repeated 01/02/03 explanation;
  removed its unused CSS and compacted desktop/phone spacing. Current README
  introductions and demo controls use matching product terms. Historical images,
  experiment reports, Gold, ledgers and receipts were not edited.

## Trust and safety placement

| Information | Current location |
|---|---|
| Source text sent to external AI and possible fees | Beside import/update; visible before submit |
| Per-question source permission and bounded model cost | Unchecked checkbox in Ask; unchanged request flag/reset |
| Numeric cost/time limits, when configured | Above Ask submit; not folded |
| Exact authorized source versions | Expandable list beside the permission context |
| Unresolved relationship blocks approval | Visible review guidance and disabled approve action |
| Source/access/version change blocks old task results | Visible stopped state; old evidence remains hidden |
| Historical decision validity | Next to the date query and in historical results |
| AI proposal vs approved decision | Result status plus attributed proposal labels; no new approval action |
| Unknown fields and date role | Each record's fields and date wording |
| Incomplete discovery coverage | Visible outstanding-questions notice |
| Offline execution | Visible task result label and Ask availability notice |
| Original quotes, counter-evidence, receipts and raw states | Available in source/field/investigation/approval details |

No F/G, Runtime, Reviewer, P4, relation verdict, domain authorization, production
Python projection or business decision condition changed. Extra UI request-token
checks prevent a stale source/list response repainting after workspace/navigation
changes during localization.

## Verification results

| Check | Result | Evidence |
|---|---|---|
| Focused Python product/UI/HTTP suite | **121 passed**, no failures/skips; 1 existing JUnit record_property warning | focused-tests.xml |
| Final localization + task projection/P4 + task HTTP assets | **21 passed**, no failures/skips | final-static-http-tests.xml |
| Existing DOM suites | **4/4 scripts passed** | verification.log |
| Added bilingual copy DOM suite | **1/1 script passed** | verification.log |
| JS syntax, changed production files and browser helpers | Passed | verification.log |
| Before/after template snapshots, same public fixture | **8 captured**; HTML parser check passed | template-snapshots/SUMMARY.json |
| Git whitespace and change-scope check | Passed | verification.log / PR diff |
| Windows-only transport test on this Linux host | **Collection blocked**, unchanged source imports `msvcrt`; not skipped or weakened | below |
| Chromium / Playwright actual interactions | **Not executed**: launch failed before page creation | browser-attempt/BROWSER_RESULT.json |
| Desktop/phone screenshots | **0 captured** | browser limitation below |
| Full repository test suite / hosted CI | Not executed locally; Draft PR CI is separate | PR checks |

The 21 final Python tests overlap the 121-test suite; these are **not 142 distinct
tests**. DOM script outputs are rendering/interaction contracts in a VM, not real
browser evidence. CSS spacing was reviewed in source; visual wrapping remains
unverified.

The initial focused command included
`tests/integration/test_product_transport_http.py`, whose existing dependency
`tests/smoke/run_temporal_product_http.py` imports Windows-only `msvcrt`. Collection
stopped before tests ran. The subsequent 121-test command explicitly selected the
other related modules. Neither file was changed; no platform failure was hidden
with a skip or altered assertion.

The existing DOM assertions were updated only where product text/order changed.
Approval blocking, reviewer identity/reason requirements, workspace isolation,
source invalidation, unknown-vs-negative results, field isolation, escaping,
proposal status, currentness and date-comparison conditions remain checked. New
contracts cover bilingual headings, current/history labels, unchanged source data,
visible costs, unchecked external permission, real record counts, incomplete
coverage, invalid proposal evidence and result payload immutability. The added
copy suite also runs in the existing Offline PR checks workflow.

## Browser limitation and reproduction

No Chromium executable exists on this host. The official Playwright install
attempt returned an invalid/truncated archive and failed. The cloud browser could
not reach the local synthetic host (`ERR_CONNECTION_REFUSED`). No browser test,
visual audit, screenshot, accessibility conformance or mobile layout pass is claimed.

On a machine with Chromium and the project's offline test dependencies:

```bash
python -m pip install -e '.[dev]'
# Install Playwright in the Node environment used for this command, then Chromium.
npx playwright install chromium
LINKLOOM_TEST_PYTHON=python node tests/ui/product_copy_browser.cjs work/copy-browser-acceptance
```

The real-browser runner uses fresh local synthetic hosts and the existing
partial/unknown/conflict/model-conflict/date-comparison fixtures. It clicks the
four main screens, switches the interface and answer languages, captures desktop
and 390px phone states, opens original field/source evidence, reopens saved task
results, updates the synthetic source, verifies old evidence is blocked, and
switches workspaces to check isolation. It records actual HTTP statuses and
console errors. These are **planned executable checks, not completed operations**.
`tests/ui/stale_revalidation_browser.cjs` separately covers explicit revalidation
approval using the same synthetic workflow.

To compare against the base in a real browser, extract the base static files via
`git show 7720390:src/linkloom/ui/static/FILE` into a separate temporary directory,
start `python -m tests.task_answer_browser_server --case partial --root NEW_DIR
--port 8771 --static-root BASE_STATIC_DIR`, and run the same source/task screens.
Use fresh directories; do not reuse a private or existing database.

The deterministic alternative already captured here can be regenerated with:

```bash
node scripts/capture_product_copy.cjs work/copy-template-snapshots
```

These HTML snapshots prove template hierarchy and literal copy changes. They are
explicitly labeled **not browser acceptance**.

## Remaining work

1. Run and visually inspect the real desktop/phone acceptance before merging.
   Font fallback, wrapping, focus behavior and spacing still need this check.
2. Run the existing Windows-only transport test on Windows and inspect hosted CI.
3. Free-form original source/model explanations remain in their original language;
   localizing their meaning would require a separate product decision. Task
   relationship approval remains unavailable, as before this cleanup.

The current delivery should stay **Draft** until the browser gap is resolved.
