# M3-L delivery: Product LIVE Canary wiring and offline admission

Base: remote `df95bfb15b4af462fec062c218d04f0d84774e00`.
Branch: `codex/linkloom-m3-live-canary`, separate worktree. Implementation and
offline admission only. No real Provider, model probe, countTokens or Vault
operation was performed. Real-model semantics and real business completion are
**NOT_EVALUATED**. No PR, merge, cleanup, dependency install or historical Gold
change is part of this delivery.

## Execution and authority

Normal `/api/product/ask` -> UnifiedAsk -> repository source snapshot -> existing
F Discovery -> G Planning/at most one Replan -> existing Runtime/ModelLoop ->
ToolRuntime/Tool Ledger -> Semantic tool -> E Working Context/original recovery
-> Grounding/Reviewer -> product projection. F/G remain audited preparation,
not invented Tool Ledger actions. No new planner, executor, memory or database
system was introduced. The existing synthetic Product SQLite catalog is reused.

The default CLI remains closed. The optional `--allow-canary-live` and local
`--canary-live-grant` build a sealed Host composition. HTTP cannot supply that
grant, adapter, transport, model, tools or budget. This mode cannot be combined
with ordinary external-provider/temporal/adaptive flags or an arbitrary database.
It creates only three dedicated public synthetic workspaces. The exact common
question, source bytes, source IDs and current versions must match. Normal
deterministic lookup still uses Fast Path without a Provider call. Other Agent
work is blocked by the Canary service.

F/G/Semantic and the outer adapter are owned by the Host factory. LIVE is a
separate execution kind from Gemini provider and model identity. Known LIVE
adapters cannot be relabelled FAKE/REPLAY. Arbitrary Python code injected by a
privileged developer is not a sandbox: offline guarantees also require the
independent network-denial test boundary. This delivery tests the real Gemini
adapter with a mocked SDK; it does not infer networking properties from a label.

The SDK is constructed lazily with one attempt, a fixed Developer API endpoint,
130s timeout, automatic tool execution disabled, LOW thinking, fixed per-stage
tool allowlists and 48 KiB wire-payload cap. A wire hook rejects a second send.
There is no model probe or online tokenizer. Existing outer repair turns use
the same budget and are not an additional allowance. Generic Runtime planning
remains disabled for the task workflow.

`EVIDENCE_VALID` means source-bound evidence validation only. It never becomes
`SEMANTIC_SUPPORTED`; unconfirmed SAME_TASK never unlocks dependent revision or
date work. Model output is not business authorization. Reviewer acceptance is
receipt/source integrity, not semantic truth. No formal conclusion or business
task write is published.

## Frozen inputs, scoring and budget

Active package: `canary-v2/`, manifest SHA-256:
`72d8d7b3cce4180d57bde436614d2050f0822f0c4ffe3dc6291173f86f898cf8`.
The loader pins this digest and checks every artifact. A/B/C/D and the common
question are preserved. D is three separately parsed messages on October 5,
with independent speaker, timestamp, message ID and source offsets.

`canary-v1/` remains byte-preserved. v2 strengthens the independent rubric with
0/1/2 scoring anchors and all-case publication constraints; source/case/runtime
configuration bytes are unchanged. The campaign ID stays `m3-product-canary-v1`:
changing the manifest cannot reopen an existing campaign budget.

The rubric separately records continuity coverage, exact quotations/dates,
conflicting evidence, unsupported confirmation and excess/missing investigation.
Its categories are CORRECT_PARTIAL, WRONG_ANSWER, CONTRACT_FAILURE, INFRA_FAILURE.
Correct refusal alone is not semantic success; empty UNKNOWN cannot earn a score.
BUSINESS_COMPLETE is unsupported by the current semantic authority path.
Rubric/Gold metadata never enters the inference context. Offline scorecards mark
all semantic dimensions NOT_EVALUATED rather than awarding scripted answers.

| Case | Sources | F | G incl. replan | Semantic | Runtime incl. repair | Total cap |
|---|---|---:|---:|---:|---:|---:|
| C1 | A+B | 2 | 2 | 1 | 6 | 11 |
| C2 | A+C | 2 | 2 | 1 | 6 | 11 |
| C3 | A+C+D | 3 | 2 | 1 | 6 | 12 |
| Campaign | public synthetic only | 7 | 6 | 3 | 18 | 34 |

The single append-only Provider accounting ledger uses an OS/process lock,
exclusive initialization marker and fsync before send. Each reservation includes
request identity, exact model/wire request, source IDs/versions/hash, case, stage,
execution identity and a $0.079872 reservation (65,536 input and 8,192 billable
output tokens). Stage/case/global count, token and cost limits apply together.
Application cost cap is $3; this is **not a cloud-provider final-bill hard cap**.
The pinned accounting estimate uses $0.75/M input and $3.75/M output including
thinking; price validation is required again before a real run.

Each case's first admitted workspace/source ID/version/content hash map is also
persisted as a `sources_bound` ledger event. Later Hosts must match it exactly;
the same text set cannot be reassigned to different source IDs. An old ledger
with requests but no binding is stopped instead of retroactively reauthorized.
Source-validation failure during the initial F/G preparation also stops the
whole campaign before any subsequent case can send a request.

Production state has one fixed location under `~/.linkloom/canary/` and the fixed
campaign ID. Neither CLI worktree/database paths nor HTTP can reset it. Marker,
manifest/config mismatch, corrupt/truncated records or crash-left pending sends
fail closed. Restart does not retry preparation or a pending request. It retains
full reservation when the outcome/usage is unknown. Actual usage, thinking and
unknown fields are retained; estimated cost is null when it cannot be known.
The total deadline is 1800s, each case 600s; admission needs at least 130s left.

First timeout, ambiguous response/send, missing/invalid usage, expired grant or
changed/revoked sources stops the campaign. Source/schema/citation failures keep
diagnostics and the existing publication gates. A pending request is never
automatically reconciled or replayed. Host state is trusted local storage; this
does not protect against an administrator deleting all state or modifying code.

## Offline evidence and reproduction

Use the existing repository dependencies, from this worktree:

```powershell
$env:PYTHONPATH = 'src'
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:LINKLOOM_RETRIEVAL_MODE = 'bm25'
$env:HF_HUB_OFFLINE = '1'
$env:TRANSFORMERS_OFFLINE = '1'
python scripts/run_m3l_offline_acceptance.py --output work/my-new-m3l-run
```

The output directory must be new. The runner mocks SDK construction and installs
the same network-denial boundary used by the test suite. Synthetic execution
paths remain in work; no existing files are removed. `evidence/accepted-reviewed/` retains
the final three Product results, Runtime audit ZIPs, scorecards, shared ledger,
budget snapshot and summary. The ZIPs contain F/G requests/responses, originals,
Checkpoint, Tool Ledger, E recovery and actual Reviewer artifacts.

All three cases yield PARTIAL with Reviewer receipt acceptance and the unresolved
revision premise visible. Mocked SDK sends are 8/8/9, 25 total. The simulation's
accounting estimate is $0.0015 and is not a real invoice: actual LIVE/paid sends
and actual model cost are zero. Real semantic and business scores remain pending.

Tests and failures are indexed in `evidence/checks-final.json` and the independent
review is in `REVIEW.md`. Test counts overlap and must not be summed as unique
coverage. Final acceptance is limited to offline Host/transport composition.

Recorded checks before the final source-review repairs: 312 related offline
regressions passed; 61 final focused checks passed; 17 localhost HTTP regressions
passed, then 15 resumed focused/HTTP checks and 18 stage-output checks passed.
The source repairs passed 28 targeted checks. A later concurrent run had 65
passes and one old 20s polling-window failure (INVESTIGATING); the subsequent
serial main-chain regression passed 19 checks, and initial-snapshot/outer checks
passed 3 checks. Final three-case evidence includes durable C1/C2/C3 bindings.
Older passing XMLs are not substituted for testing those repairs. DOM and diff checks
also passed. Actual usage must fit the stage's sent output cap, including
thinking, in addition to the shared maximum reservation.

Retained development failures: missing new module during the initial RED test;
sandbox default Temp permission; localhost WinError 10013 (resolved by a bounded
localhost test exception while external network remained denied); mock source
envelope mismatch; mock use of Provider IDs instead of Runtime receipt IDs;
mock UNKNOWN lacking missing-evidence explanation; a freeze file being read
during candidate construction; old refusal-message regex; and a misplaced test
assertion expecting unknown cost after a fully metered source revocation.
These are not silently reported as model quality failures. Python's installed
pyreadline destructor emits WinError after some runs; successful pytest exit
codes and XML results are recorded separately. Review was interrupted once by
account usage limits and resumed after the user's continuation.
Independent review subsequently reproduced three source-boundary defects
(initial preparation not stopping the campaign, missing durable source-version
binding and swapped source ID/text pairs). Three new regressions failed before
the repairs; those RED results are retained beside the final rerun.

## Before any real LIVE execution

Require a separate explicit human grant for this exact manifest, model, public
sources, 34-request/$3 maximum and expiry. No executable grant or credentials are
included. Reconfirm current price, selected account/project, endpoint/SDK policy,
and availability without an automatic probe. Confirm the persistent campaign
state is admissible; do not erase or relocate it to recover budget. A pending,
expired or stopped campaign requires a new reviewed decision, not an automatic
restart. Plan independent human semantic scoring and preserve the difference
between source facts, proposed relations and authorized business conclusions.

No CI, deployment or real browser visual acceptance is claimed. Real Provider
compatibility, model semantics and useful business completion remain unevaluated.
F/G process recovery and semantic confirmation remain unfinished capabilities.
This delivery stops here; no next-round Provider test is started.

Reflection: the new work closes transport/accounting admission around the
existing loop. The offline evidence proves bounded execution and safe stopping;
it cannot establish semantic intelligence or confer business authority.
