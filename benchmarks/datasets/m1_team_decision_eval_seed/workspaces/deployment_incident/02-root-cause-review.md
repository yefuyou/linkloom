---
date: 2026-03-04
status: reviewed
authority: incident_review
---
# Deployment Incident — Root Cause Review

## Root Cause

The timeout recovery path replayed a request without carrying its idempotency
key. The replay therefore passed the duplicate guard as a new request and could
produce a duplicate action. Provider latency contributed to the timeout but
was not the primary root cause.

## Evidence

The review matched the duplicate actions to requests whose original attempt
had a key but whose recovery attempt did not. The trace comparison was completed
for the synthetic incident sample.

## Containment

The review recommended a bounded replay guard and end-to-end idempotency-key
propagation. Production verification was left to the status update.
