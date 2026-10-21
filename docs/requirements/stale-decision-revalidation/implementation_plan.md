# Implementation plan

1. Reuse ProductRepository immutable source versions, DecisionReconciler,
   SemanticDecisionMaterializer and semantic approval events/receipts.
2. Add a product revalidation projection and snapshot-bound approval route.
   Carry only an audit target in the existing HUMAN_APPROVAL metadata.
3. Add Attention detail/selection and reuse the candidate review form. Display
   historical stale evidence separately from verified recovery results.
4. Cover durable HTTP boundaries, source changes during review and no-candidate
   cases; include in offline CI. Exercise actual browser and save screenshots.
5. Independent acceptance review, fix findings, commit/push isolated branch;
   optionally Draft PR. Never merge master.
