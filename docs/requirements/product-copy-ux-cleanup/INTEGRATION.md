# Same-PR integration

Original task baseline: `7720390a796662ff42c27b5eef6f8685e5155802`.
Existing #25: `3d4c05025c64aed0f51a15b6aa7d476febc972f8`.
Current master: `f2ce6b82d635a7b78b17a6d100556c354f7a461a`.

Continue the existing `codex/product-copy-ux-cleanup` and Draft PR #25. A normal
local merge preserves the existing #25 commit; another normal merge includes
current master. No rebase, reset, force push or master mutation.

Conflict resolution keeps the inspected answer-first UI and real-browser runner,
while retaining prior language preference/URL support, complete candidate-type
labels, saved-source display with original FAILED state, localized repair errors,
and the fifth DOM CI gate. Mainline M2 gates remain in the workflow.

All 222 non-static src files were independently compared byte-for-byte against
current master with no differences. The UI request data still sends canonical
relationship values; translated labels are presentation only. View/workspace/
historical-date guards and explicit external authorization stay intact.

Prior reports are copied byte-for-byte under prior-delivery. Original AUDIT_DATA,
XML receipts, static snapshots, capture script and browser-attempt receipt remain
in place. Their 37-group/100-finding counts are not combined with this round's
42-state/60-issue audit.

The integrated candidate passed 178 Python/HTTP cases, five DOM suites, JavaScript
syntax/whitespace checks, the offline HTTP cold-read runner and 33 real Chromium
steps. See evidence/integrated for new receipts. Original receipts were not edited.

For connector publication, the remote merge commit uses the original #25 and
current master as parents. Local/published commit metadata may differ; their
complete Git tree must match before updating the branch with an expected-head
check and force=false. The PR stays Draft and unmerged.
