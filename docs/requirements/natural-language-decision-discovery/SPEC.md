# Natural-Language Decision Discovery

Approved boundary: the user's overnight mission on 2026-10-15; base
`b766f055bde89fa6202c480339eda58f46108fe7`.

Ask accepts a workspace and natural-language question without a manual slot.
Local discovery returns MATCHED, AMBIGUOUS, NO_MATCH, or EXPLICIT. Only slots
with currently recorded review receipts and verified source evidence participate.
Discovery narrows by exact normalized subject/history value mentions, configured
relation aliases, and explainable relation words (including supplier/vendor/供应商).
All recognized constraints must agree on one slot; ranking alone never selects it.
Multiple slots require selection; unsupported wording may require explicit scope.

Architecture: `UnifiedAskService -> scope_discovery -> recover_decision` for the
trusted index, then the existing deterministic or temporal Agent path. Discovery
owns matching and date interpretation only. Store, Runtime, Validator, Reviewer,
valid-time rules, evidence and authorization remain authoritative and unchanged.
Exact ISO or full Chinese dates can supply as_of; relative dates, ranges and
conflicting dates require clarification. An explicit date resolves relative
wording. Existing valid explicit slots and both legacy APIs remain compatible.

UI: workspace/question/Ask are the default. Slot/date live under advanced options.
At most five understandable candidates can resume the retained question. Results
show the discovery basis, execution path, verified state, unknowns, receipts,
sources and Timeline. External provider permission remains per request.

Acceptance: two synthetic projects, reviewed history and an unapproved proposal;
unique/ambiguous/no match; zero model turns on current and historical queries;
FakeTransport investigation with accepted Validator/Reviewer and UNKNOWN cause;
proposal exclusion; workspace isolation and stale-response rejection; UNCERTAIN
with no Final; explicit/legacy compatibility. Run focused offline regressions and
at least three scenarios in a real browser. Missing browser evidence means the
browser gate is incomplete.

Implementation plan: (1) failing discovery/date contracts and local matcher,
(2) service integration and HTTP acceptance using the existing synthetic fixture,
(3) progressive UI and race checks, (4) browser demo and independent review,
(5) final diff/sensitive-data check, commit and push the requested branch.

Non-goals: multi-slot investigation, arbitrary natural-language understanding,
new model routing/retriever/store/runtime, provider diagnostics, paid calls,
real-vault edits, frozen experiment changes, release or merge.
