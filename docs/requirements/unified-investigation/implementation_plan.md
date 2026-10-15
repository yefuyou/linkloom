# Implementation plan: Unified multi-hop investigation integration

1. Freeze the route/result contract in `UnifiedAskService`: recognize only the
   existing `plan_relation_investigation` output, retain query/workspace/date
   identity, and classify unsupported relation wording without guessing.
2. Thread the plan and a dedicated 12-round limit through
   `ProductInvestigationService` → `RuntimeRunBackend` → explicit `RunRequest`;
   use the existing `RuntimeEngine`, Tool Ledger, grounding, and Reviewer.
   Preserve Temporal=8, retrieval identity=12, tools=20, and deadline=5000 ms.
3. Project the internal `_source_relations` from the accepted Runtime artifact
   into the product field `source_investigation` and verified reads. Preserve
   source/version/quote identity, null the main decision for partial/conflict,
   and surface Provider uncertainty without a final.
4. Repair conflict classification for `reports_to`/`direct_leader_of` and bind
   TaskPlan conflicts to the verified parent owner/project, reusing owner-source
   and date/project filters; only same subject/relation/time incompatible reads
   are conflicting. Add focused coverage for HOLD-G and unrelated evidence.
5. Extend answer-first presentation and polling guards for complete, partial,
   conflict, unsupported, and `UNCERTAIN`, including workspace races and
   evidence identity. Do not alter Temporal, Discovery, Feedback, or legacy UI.
6. Run the fixed Case1–Case10 product/HTTP/browser acceptance and focused
   regressions. Re-run the old frozen heldout only as exposed-set regression,
   retain its first 8/10 report, record zero Provider/vault access, and obtain
   independent review before any commit.
