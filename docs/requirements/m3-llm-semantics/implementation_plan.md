# Implementation plan

1. Read-only provider/model preflight; create fresh Dev sources and independent
   labels before implementation. Record scope and source of truth.
2. Write failing contract tests, then implement snapshot/proposal models and
   exact evidence/authority validation. Include true-quote/wrong-semantics probe.
3. Implement one-turn structured proposer with FakeModel, bounds and failure
   provenance. Reuse existing provider seam without editing it.
4. Add isolated input-only diagnostic driver and separate evaluator. Exercise
   new Dev, reuse suitable M3 Dev/scorer, and compare exposed H cases to regex.
5. After offline verification, run at most three authorized real Gemini Smoke
   requests on a predeclared synthetic batch. Freeze prompt/input before calls;
   retain actual failures, usage and cost, without iterative live tuning.
6. Independent read-only review, focused/affected regression, architecture
   checkpoint and P4 minimum future-contract recommendation. Scope-check diff,
   commit, normal non-force push, read back the requested remote SHA, stop.
