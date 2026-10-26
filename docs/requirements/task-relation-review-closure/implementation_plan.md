# Implementation plan

1. Coordinator/Planner reads original evidence, reproduces P1 in Chromium, commissions read-only trust-contract analysis, publishes GAP_ANALYSIS/SPEC before business code.
2. Backend Worker first adds failing durability/permission/idempotence/Workspace/proof-separation contracts, then new task_review service/store/API/launch integration. Own new ui/task_review*.py, server.py, __main__.py, narrowly required investigation.py and new Python tests. No static/core-proof/currentness edits.
3. UI Worker owns separate static/product-task-review.js, one narrow product.js hook and product.html include, new focused DOM test. Keep renderer/CSS/P2 unchanged. Inspect latest PR25 immediately before edits and demonstrate compatible composition or defer overlaps.
4. Coordinator captures actual browser workflows, baseline UNKNOWN and positive conditional source cases with only offline model transport scripted; no preseeded relation proofs. Only real UI operations create receipts and request finite proof captures.
5. Relevant existing proof/store/query, Product HTTP/DOM + new contract tests; no Provider/frozen results. Recheck PR24/25 and independent read-only code/evidence review.
6. Worker fixes actionable in-scope defects; repeat affected checks after changes. Report exact APIs/DB/UI delta, limitations, evidence and final permitted status. No publish/commit.

Role separation: Planner docs, backend/UI Workers disjoint files, independent Reviewer; Coordinator integrates evidence. The user's requested bounded feature is implementation authorization; no stronger authority or contract expansion is inferred.
Engineering reflection: independent human statement, source proof, and business authority have different owners. Keep that separation in persistence and UI.

