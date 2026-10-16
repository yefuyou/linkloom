# Test-only Runner boundary fixes

No production module is changed. The new runner uses actual RuntimeEngine, Scanner identity, SQLite checkpoints, tool ledger, grounding helpers and backend calls. Its controller receives only a public input projection; Gold and expected outcome are excluded. The prepared validation Final has no execution CLI and remains NOT_RUN.

| Boundary | Required observed witness |
|---|---|
| Normal | Completed grounded Final; both hops searched/read; next-hop dependencies derived from prior verified reads |
| Partial | Missing-hop Search actually executes; grounded rationale retained; no fabricated completed decision |
| Conflict | Contradictory verified values from two distinct source paths; conflicting_evidence; no completed decision |
| Illegal arguments | Actual $.limit rejection before Search/Read executors; bounded second model response counted |
| Final correction | One final_rejected record followed by one counted valid Final; direct and HTTP paths |
| Cold resume | Crash after SQLite response_durable; a new Engine resumes; no duplicate Search executor |
| Provider uncertain | One synthetic provider invocation; unknown_provider_outcome; no retry or tool executor |
| Workspace | Foreign vault-root identity rejected before executors; direct and HTTP paths |
| Freshness | Mutate exactly the verified source, record before/after SHA, reread stale identity, observe LoaderError |
| Budget | Admitted TaskPlan with provider limit 1; Search executes, then explicit budget failure and no Final |

The independent Core fixture also preserves D06/F07 goal-mismatch negative tests. It verifies a forged dependent entity cannot reach the retrieval backend. Counting the adapter entry was misleading because semantic validation happens inside it; the new counter observes the backend boundary after validation.

Final correction previously processed an old read observation twice; the controller now reconstructs a correction from stored source evidence and deduplicates grounded claims. The original synthetic conflict used two paragraphs of one file; production correctly requires independent source paths. The new Core fixture supplies separate sources. Neither fix changes the production validators.

Preflight additionally rejects same-source conflict claims, impossible fault budgets, non-completable cold-resume/final-correction targets, duplicate IDs/SHA, unsupported relation paths, prefilled dependencies, foreign source identity, unsupported Gold and mechanical cross-partition copies. Paragraph grouping is canonicalized independently of blank padding and order. This found three weak draft cases and a Freshness budget that would exhaust before its target boundary. Draft changes occurred before freeze preparation, without executing the new Final or using its answers for production optimization.

The provider used here is an offline ModelResponse adapter. Provider request counts in its traces are synthetic invocation counts, not real Gemini requests. These tests do not establish paid Provider reservation-ledger behavior or cross-model reliability.
