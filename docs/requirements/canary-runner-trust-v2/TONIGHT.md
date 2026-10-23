# Authorized PR18 Canary execution

The user's 2026-10-23 instruction authorizes the actual bounded Canary, not
another feature milestone. Reuse this runner checkout. Production files are
exactly PR18 `ba51b3586135f1ef52a32b5e9945d14b058bac38`; the runner records
its separate integration SHA. No #19 changes or production edits are included.

For the first directly comparable live round, explicitly use `--profile null`:
PR16's 28 sealed inputs use the general F contract. Do not change this Prompt
into task-recall/v3 during the comparison. The existing historical null-profile
offline replay remains mandatory; the launch guard additionally compares all
prepared ModelTurnRequests with the sealed original requests. The only runner
launch change is allowing that authorized profile under the same frozen gate,
source hash, clean commit, request and budget checks. v3 is still separately
identified and is not evaluated by this round.

Use the existing stricter 30-reservation / USD 8 cap (user ceiling 32 including
Smoke), 1,400,000 input and 122,880 output tokens, 90-second request timeout,
one SDK/F attempt and no automatic retries. Planned 28 requests reserve USD 7;
no separate repetitive Smoke is necessary. First real chunk is the bounded
transport check. On failure or UNKNOWN, stop and retain unattempted work N/E.

The gate includes PR18 quote/nesting tests, B01 work identity, existing source
permissions, UNKNOWN, P4 and Reviewer contracts. Replay the same 28 saved model
outputs on the new production code before starting, and separately report the
known meeting-time PLAN false positive. PARTIAL count is not task recall.

Persist original SDK/wire and normalized outputs, compiled source snapshot,
source hashes, each reservation/terminal receipt, failures and cost estimates.
Do not alter historical evidence or scores. Analyze with both fixed old outputs
and fresh outputs to separate code repair from model variance. Evidence may be
committed/pushed with a Draft PR; no master merge or Billing Console access.
