# Evaluation qualification implementation

Approved scope: evaluation infrastructure only; no production or frozen-history edits, no real Provider request and no new Final execution.

1. Audit all twenty exposed V3 cases against saved traces, admission rules and source evidence; preserve historical scores and hashes.
2. Implement fail-closed static preflight with production plan/claim validators, source spans, observable targets and cross-partition graph checks. Run it inside the builder before writing freeze-preparation output.
3. Exercise ten Runtime boundaries using source-only controllers and actual ledger/executor observations in a separate tracked synthetic Core Gate. Invalid tests must fail, rather than become skips.
4. Run every one of the frozen 249 test nodes at both exact commits in the same dependency environment. Keep raw pytest failures, unavailable historical inputs, environment problems and differential classifications separate.
5. Prepare sixteen new synthetic cases (six dev, ten final), check their eligibility statically and record author-visible Gold. Do not run them as Final.
6. Reproduce the Core Gate from an index-only clean export and a fresh pinned virtual environment. Obtain independent review, then mark the prepared data READY_FOR_APPROVAL.
7. Commit and push only this branch; read back its remote SHA and stop.

Commands and actual evidence are recorded in DELIVERY.md and the machine-readable reports. A passing synthetic gate is not evidence of real-model reliability or a green historical 249-test suite.
