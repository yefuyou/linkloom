# M1 implementation plan

1. Add contract tests and the small task proposal/observation validation module.
2. Extend extraction and source-candidate serialization compatibly; keep decision
   valid-time semantics and SUPPORTING_ONLY unchanged.
3. Add read-only source/project queries over persisted candidates with source
   currency checks, plus replay/version/idempotency integration tests.
4. Run focused offline regressions, export actual observations with original
   field evidence, then obtain independent acceptance review. Stop at M1.

Source ownership: existing edits are preserved. Pre-M1 versions of touched
ingestion files are retained under work/m1-task-observation/baseline for review.
Only synthetic temporary databases and new M1 evidence artifacts are used.
