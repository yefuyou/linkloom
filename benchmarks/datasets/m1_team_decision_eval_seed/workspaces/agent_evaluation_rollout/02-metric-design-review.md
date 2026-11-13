---
date: 2026-03-19
status: reviewed
authority: metric_review
---
# Agent Evaluation — Metric Design Review

## Deterministic Metrics

Decision accuracy, rejected-alternative accuracy, unresolved-item recall, and
evidence validity can be checked against the frozen synthetic Gold records.
These metrics are reproducible enough to gate the first rollout.

## Judge Boundary

An LLM judge may provide diagnostic comments, but judge output is too variable
to serve as the release gate for this slice. Judge results must remain separate
from deterministic metrics.

## Threshold Question

The draft's 0.80 next-action threshold still required an explicit approval
decision. This review did not assign an owner or approve the threshold.

## Recommendation

Use deterministic metrics for release gating and keep judge feedback
diagnostic until a later review supplies reproducibility evidence.
