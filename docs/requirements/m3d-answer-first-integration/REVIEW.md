# Independent integration review

Reviewer: separate `gpt-5.6-luna` agent, reasoning `max`, read-only.
Boundary: [SPEC](SPEC.md), original Answer-first SPEC and M3-D combined SPEC.
Worker evidence: [DELIVERY](DELIVERY.md) and its fresh campaign receipts.

## Final verdict

**PASS — P1=0, P2=0.** The initially open P2 is closed; no waiver was used.
This is local synthetic integration acceptance, not deployment or remote CI.

## Initial finding and closure

The initial independent review identified one P2 in `product.js`'s `reopen-task`
event path: while saved-result HTTP revalidation was pending, old source quotes,
owner/deadline and model proposals remained visible. The original A/C screenshots
proved the final BLOCKED view but did not cover that waiting window.

The Worker reproduced it before modifying production code:
[DOM RED](evidence/pending-red.txt),
[real Chrome RED](evidence/pending-browser-red.json),
[screenshot](evidence/pending-red-A.png).

The minimal correction is only in `product.js:816–823`:

```js
if (runId) {
  invalidateAsk();
  await pollAsk(runId, workspaceId, state.agentViewToken);
}
```

The Reviewer checked that this reuses the existing state owner and token logic:
old result/state are cleared before fetching; old in-flight responses become
invalid; language changes cannot restore old state; the Workspace-bound pointer
is retained. No F/G/Runtime or authorization contract changed.

Final verification independently inspected:

- [DOM GREEN](evidence/pending-green.txt), independently rerun successfully;
- both Product JS syntax checks, independently rerun successfully;
- [Chrome GREEN](evidence/pending-browser-green.json): A source update and C
  evidence revocation both have `pending_text=""`; after real HTTP release,
  both return BLOCKED with empty evidence and no task_review; 2/2 pass,
  page_errors empty, paid_requests=0;
- recorded final Product JS SHA256 equals the current file:
  `b0c5c652a58a800d5e08846c49c1675ab55d51b933a8c8825c48a5e10af585ef`;
- [integration checks](evidence/integration-checks.json) and actual diff confirm
  one compatibility patch and `core_m3d_files_changed=false`.

## Independent acceptance evidence

The Reviewer inspected code, tests, critical HTTP receipts and screenshots:

- A preserves F v3 owner/date and source version/content SHA/provenance/offsets.
  Updated sources block both hot/cold reads; cross-Workspace access returns 409.
- C preserves UNKNOWN, a blocked dependent step and completed independent reads.
  The Provider-free browser's budget journal remains unchanged. Revocation blocks
  hot/cold reads and releases no old evidence.
- Model CONFLICT has EVIDENCE_VALID citations but remains non-publishable,
  PENDING_SEMANTIC_REVIEW; relation/revision reads remain blocked. The UI does
  not turn citation validity into semantic or business approval.
- P4's 4-day comparison retains its currentness limit. The screenshot distinguishes
  source date boundaries from formal approval; no current formal plan is inferred.
- Sampled observation content SHA256, source version, quote hash and offsets are
  independently reproducible from the stored source response.
- 162 unique passing tests, 8 main Chromium scenarios, zero page exceptions and
  three expected permission-refusal 403 console events are recorded. The final
  event-handler patch adds the two targeted browser scenarios above; rerunning
  unchanged backend contracts was unnecessary.

## Limits and safety

All fixtures are public synthetic. No real vault mutation or new paid Provider
request occurred. Model semantics use scripted transport, so this review does
not claim real-model quality. Reviewer acceptance remains execution/evidence
acceptance; the UI supplies no business relationship approval authority.
The original UI's historical PASS is not used as this integration's PASS.

## Reviewer handoff

The final independent response explicitly authorized recording
`P1=0 / P2=0 / acceptance PASS`. Git publication remains the main coordinator's
requested Draft stacked PR operation; the Reviewer did not edit or publish code.
