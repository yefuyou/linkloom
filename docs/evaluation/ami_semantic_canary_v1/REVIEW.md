# Independent read-only review

## Historical AMI baseline

Reviewer: ami_baseline_audit (gpt-5.6-luna, max). Result: PASS, no P1/P2 blockers. No tests, Provider calls or file mutations by reviewer.

Confirmed prepare validates 56/51/5 and native projection/hash; AMI adapter retains IDs/speakers/hrefs/word spans/member hashes. Predictor whitelist permits only manifest/queries/passages. Scorer checks prediction/reference seals before evaluator-only Gold. Existing BM25/cached Dense/Hybrid/ContextAssembler are reused. Macro uses eligible queries; micro uses100 query-target pairs (97 distinct DAs). N/E excluded. Public retrieval scores do not establish F/Action/Decision/Owner/Deadline/Product semantic accuracy.

## Canary runner and report

Reviewer: canary_final_review (gpt-5.6-luna, max). Result: PASS, no P1/P2 blockers. Read-only; no Provider calls or file mutations.

Confirmed fixed ES2002a/ES2005c and gemini-3.8-flash,28 requests with USD0.10/2.80 reservation before send. All28 actual requests equal plan; SDK model versions and F guard receipts agree; no Gemini2.5. Sources contain8+20 chunks with native references. Model input excludes Gold/query/qrels. Report matches28calls,156052tokens,USD0.142125 estimate, actual billing null, formal F metrics NOT_EVALUATED and Product NOT_RUN. Staged scope excludes production source and frozenGold; seal verified.

F omissions and Discovery quotation-scope loss are reported diagnostics, not concealed as engineering regressions or formal semantic scores.
