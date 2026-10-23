# Frozen Canary v2 runbook

Status: DEV_DIAGNOSTIC / INDEPENDENT_REVIEW_NOT_VERIFIED.

Use `PYTHONPATH=src;.` on Windows. All commands use `python -m scripts.<name>`.
Every output path must be fresh. No command resumes or retries a paid run.

1. Commit the repair candidate and record its exact SHA. Keep tracked files clean.
2. Run `run_canary_checks --output work/frozen-checks-01`. This runs quote,
   runner/history and source/UNKNOWN/P4 contracts, recording source/runtime-bound
   JUnit evidence. A failing or skipped check cannot authorize paid execution.
3. Run `analyze_canary_v2 --run <historical-live> --replay-inputs <AMI-inputs>
   --output work/frozen-replay-01`. It requires sealed historical outputs and
   byte-equivalent null-profile requests. It never maps old outputs to v3.
4. Run `run_ami_canary_v2 --inputs <AMI-inputs> --output work/frozen-plan-v3-01`
   and optionally `--profile null` to a different output. Neither constructs SDK.
5. Run `prepare_canary_gate --quote work/frozen-checks-01/quote.xml
   --runner work/frozen-checks-01/runner.xml --safety work/frozen-checks-01/safety.xml
   --replay work/frozen-replay-01/replay_receipt.json --output work/launch-gate-01.json`.
6. Only after all gates pass, run `run_ami_canary_v2 --inputs <AMI-inputs>
   --output work/live-v2-01 --execute --gate work/launch-gate-01.json`.
   The user already authorized existing credit; never inspect Billing Console.
7. Seal first, then run `analyze_canary_v2 --run work/live-v2-01
   --output work/live-analysis-v2-01`. Post-seal evaluator-only human annotations
   may inform case diagnostics; they must never enter requests.

The live contract is v3, Gemini 3.8 Flash, low thinking, 4,096 output tokens,
no temperature, no Compactor, one F/SDK attempt. Two complete meetings mean 28
requests; conservative reservation is USD 7 at USD .25 each, with total limits
30 requests / USD 8. Reservations are never refunded automatically. Public
standard prices checked on 2026-10-23 are USD .75 input / 3.75 output including
thinking per million tokens:
https://ai.google.dev/gemini-api/docs/pricing . The reservation uses higher
USD 4 / 20 bounds and is distinct from both a list-price estimate and billing.

Session outcomes remain separate from transport outcomes. The main thread owns
immutable terminal receipts. A Session timeout closes its cutoff as UNKNOWN;
late SDK/normalized receipts use a separate, unsealed sibling directory with
run/request identity and original terminal hash. They do not trigger a retry or
rewrite UNKNOWN. Missing usage never proves no charge. If safety/budget fails,
stop and report unattempted requests N/E.

The old `analyze_continuation.py` / `finalize_evidence.py` and their embedded
hard-coded zero remain historical bytes only. Use `analyze_sprint_trust_v2` to
derive all historical attempts and provenance gaps. No historical script is
imported or executed by the v2 runner or analyzers.
