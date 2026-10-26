# Chromium interaction evidence

Final run: PASS; Chromium 153.0.8010.0. Desktop 1440×1000 and mobile 390×844.
33 screenshot steps, 132 local HTTP responses, zero JavaScript page errors; no external Provider.

| Step | Operation / state | Health | Retained screenshot |
|---|---|---|---|
| 1 | Sources list and local import setup (`01-sources-en`) | PASS | [after-sources-en.png](after-sources-en.png) |
| 2 | Chinese source labels and unchanged source names (`02-sources-zh`) | PASS | [after-sources-zh.png](after-sources-zh.png) |
| 3 | Language changes preserve the open workspace form and user text (`03-workspace-form`) | PASS | Reproducible with browser script |
| 4 | Task answer, state, source records and unresolved checks (`04-task-partial-zh`) | PASS | [after-task-zh.png](after-task-zh.png) |
| 5 | Expanded exact quote, field roles and source version (`05-field-evidence`) | PASS | Reproducible with browser script |
| 6 | English task result with unapproved model suggestions (`06-task-partial-en`) | PASS | [after-task-en.png](after-task-en.png) |
| 7 | 390px task result and source cards (`07-task-mobile-zh`) | PASS | [after-task-mobile-zh.png](after-task-mobile-zh.png) |
| 8 | Open source and switch interface language without returning to candidate view (`08-original-source`) | PASS | Reproducible with browser script |
| 9 | Reopening updated-source results hides old evidence (`09-stale-blocked`) | PASS | [after-blocked-zh.png](after-blocked-zh.png) |
| 10 | New workspace has no previous task pointer or evidence (`10-workspace-isolation`) | PASS | Reproducible with browser script |
| 11 | Task answer, state, source records and unresolved checks (`04-task-unknown-zh`) | PASS | [after-unknown-zh.png](after-unknown-zh.png) |
| 12 | Task answer, state, source records and unresolved checks (`04-task-conflict-zh`) | PASS | Reproducible with browser script |
| 13 | Task answer, state, source records and unresolved checks (`04-task-model-conflict-zh`) | PASS | Reproducible with browser script |
| 14 | Task answer, state, source records and unresolved checks (`04-task-p4-zh`) | PASS | Reproducible with browser script |
| 15 | Pending candidates and immediate source-changed warning (`11-review-attention`) | PASS | Reproducible with browser script |
| 16 | Original approval, current source and separate new candidate (`12-source-comparison`) | PASS | Reproducible with browser script |
| 17 | Chinese review and explicit approval of the new candidate (`13-review-zh`) | PASS | [after-review-zh.png](after-review-zh.png) |
| 18 | Approved result, dated history and retained original approval (`14-decision-history-zh`) | PASS | [after-decisions-zh.png](after-decisions-zh.png) |
| 19 | Language switch preserves the queried historical date and result scope (`14-historical-language`) | PASS | Reproducible with browser script |
| 20 | Reviewed supplier lookup with evidence (`15-ask-reviewed`) | PASS | [after-ask-en.png](after-ask-en.png) |
| 21 | 390px reviewed answer and composer (`16-ask-mobile`) | PASS | Reproducible with browser script |
| 22 | No stale decision remains after separate approval (`17-review-complete-zh`) | PASS | Reproducible with browser script |
| 23 | Fresh application requests a workspace (`18-empty-workspace`) | PASS | Reproducible with browser script |
| 24 | Chinese source empty state and save-only import (`19-empty-sources-zh`) | PASS | Reproducible with browser script |
| 25 | Empty review queue, supporting proposals and attention state (`20-empty-review-zh`) | PASS | Reproducible with browser script |
| 26 | Empty Decision Memory has a clear next step (`21-empty-decisions-zh`) | PASS | Reproducible with browser script |
| 27 | Empty-source investigation gives an actionable prerequisite (`21-empty-investigation`) | PASS | Reproducible with browser script |
| 28 | Source saves locally while extraction remains unavailable (`22-saved-source-zh`) | PASS | Reproducible with browser script |
| 29 | Immediate unavailable reason with setup instructions expandable (`22-task-unavailable`) | PASS | Reproducible with browser script |
| 30 | Legacy replay uses a single heading and concise input guidance (`23-legacy-start-zh`) | PASS | Reproducible with browser script |
| 31 | Legacy offline replay result and source inspector (`24-legacy-answer-zh`) | PASS | Reproducible with browser script |
| 32 | Legacy replay mobile layout (`25-legacy-mobile-zh`) | PASS | Reproducible with browser script |
| 33 | Operational failure remains separate from insufficient evidence (`26-network-error`) | PASS | Reproducible with browser script |
