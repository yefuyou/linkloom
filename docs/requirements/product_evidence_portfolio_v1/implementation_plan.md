# Product Evidence & Portfolio V1 — Implementation Plan

1. Audit and selectively commit Product UI V1.
2. Add offline tests for an exact two-case, Gold-free DeepSeek follow-up
   registry and seal-before-Gold evaluation boundary.
3. Implement the smallest test-only runner by reusing the accepted DeepSeek
   client guard, provider adapter, Runtime, artifact, and evidence helpers.
4. Run the offline harness tests and relevant strict-contract regressions.
5. Execute `aer-002` once; preserve its first terminal result.
6. Execute `iti-005` once; preserve its first terminal result.
7. Inspect only sealed artifacts and frozen Gold; produce a product-oriented
   evaluation matrix with evidence paths.
8. Project both real results through the Product UI boundary. Change UI only
   if a real result exposes a blocker; use Gemini for any visual judgment.
9. Rewrite English and Chinese READMEs, add portfolio/resume notes, and verify
   links, commands, claims, and screenshot references.
10. Run scoped regression, independent review, selective commit, and stop.

Implementation remains in test and documentation layers unless a verified UI
projection defect requires a narrow product fix.
