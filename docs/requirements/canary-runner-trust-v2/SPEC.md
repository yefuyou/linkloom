# Canary Runner trust v2

Approved boundary: user's 2026-10-23 Canary Runner request. Baseline fetched
from origin/master: `71ccecf937ffc0deb127e252eeff56df00dd4723`.
Local master at start: `c2b4e7721e4e8b17c7ffdbd3e2035e9db42b4dd2`.
Historical evidence commit: `f26191487f459d7af7016898422b9bbcfa1a5cee`.

## Architecture and scope

Keep historical evidence, output, ledgers and README results unchanged. Add a
versioned analyzer and runner. Reuse F, the source registry and ordinary Discovery;
fix only structural quote scopes for P2. Do not change task identity, P4, Gold,
Compactor or authoritative memory. The runner owns immutable request identities,
pre-send reservations and raw SDK receipts. The analyzer derives transport status
from evidence independently of Session business status. A missing response or
usage receipt never proves no send or no charge.

Classification: NOT_SENT requires positive pre-transport evidence;
SENT_SUCCEEDED requires an attributable successful Provider receipt;
SENT_FAILED requires an attributable Provider error receipt;
otherwise OUTCOME_UNKNOWN. Persist reservations as unknown until terminal evidence
exists. UNKNOWN is terminal and never replayed automatically.

Use complete ES2002a (8 chunks) and ES2005c (20), transcript-only requests. Preserve
null-profile historical replay and prepare TASK_RECALL_V3 as a distinct contract.
New live requests use v3, low thinking, 4096 output tokens, no temperature, one SDK
and F attempt, gemini-3.8-flash. Maximum 30 actual requests / USD 8 conservative
reservation, no retries or comparison experiments. Budget remains reserved for
unknown, failed or unreported-usage calls. Stop on any failure or safety violation.

## Acceptance

- Enumerate six early source hash gaps, preserving NOT_VERIFIED when unavailable.
- Test B TLS, D local SDK argument failure, success, Provider rejection, unknown,
  crash recovery, budget caps, source drift and absence of usage.
- Fix P2 apostrophes and paired nested quotations with negative-scope regression.
- Freeze committed repair SHA and code/config/runtime identities before requests.
- Complete offline normal F -> Discovery replay at frozen SHA; do not reinterpret
  old model output under v3. Source authorization, UNKNOWN and P4 tests pass.
- Persist exact request/source hashes and reservation before each paid call;
  separately save wire/SDK receipt, normalized response, F/Discovery outcomes.
- Report quote validation, verdict agreement and semantic qualification separately;
  formal semantic Precision/Recall/F1 and business approval accuracy remain N/E.
- Retain DEV_DIAGNOSTIC and INDEPENDENT_REVIEW_NOT_VERIFIED.

## Checks and delivery

Focused pytest contracts first, related source/Discovery/P4 suites, offline AMI
replay and no-network preparation, then repository offline suite. Live execution
requires all explicit launch conditions above. Repair candidate and result evidence
may be committed/pushed to independent branches and Draft PRs, never merged.
No real vault or Billing Console access. User confirmed existing credit scope.
