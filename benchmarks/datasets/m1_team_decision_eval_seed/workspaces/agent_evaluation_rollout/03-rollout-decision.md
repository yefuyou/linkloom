---
date: 2026-03-21
status: approved
authority: decision_record
---
# Agent Evaluation — Rollout Decision

## Decision

The evaluation rollout will gate release on deterministic metrics for decision
accuracy, rejected-alternative accuracy, unresolved-item recall, and evidence
validity. LLM-judge output is diagnostic only and must not gate release.

## Scope

The first rollout covers the synthetic decision, rejection, unresolved-item,
and evidence cases. A multilingual slice is tracked separately and is not
silently treated as complete.

## Unapproved Threshold

The proposed 0.80 next-action threshold was not approved in this decision. It
requires a later sign-off before it can become a release rule.

## Approval

The evaluation group approved this boundary on 2026-03-21.
