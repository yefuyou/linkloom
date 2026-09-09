---
date: 2026-03-05
status: approved
authority: decision_record
---
# Deployment Incident — Mitigation Decision

## Decision

The team approved propagating an idempotency key through the timeout recovery
path and adding a bounded replay guard. This is the selected mitigation for the
synthetic service.

## Rejected Alternative

An active-active deployment was rejected for this incident slice. It would add
another route for duplicated side effects and operational coordination before
the idempotency defect was fixed.

## Verification Boundary

The decision record approves the mitigation design. It does not claim that the
production rollout or load-test verification is complete.

## Approval

The incident review group approved this mitigation on 2026-03-05.
