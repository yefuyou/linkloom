# Google Cloud credit sprint: immutable evaluation evidence

[Report](REPORT.md) · [File hashes and omissions](EVIDENCE_MANIFEST.json) · [Evidence archive](evidence.zip)

This commit archives the completed 2026-10-23 evaluation. The report describes
the state at evaluation completion, before this archival commit. It adds no
production changes or README scores. It does not merge the evaluated candidate.

The source snapshots are master `46b819fa39ace26de2c5470f7438b56df523c560`
and candidate `d0c64361ce0613d021d5d950954f7b0da6d2fabe`. All new generation
uses Gemini 3.8 Flash or independently checked Gemini 4 Argon. Development cases
remain `DEV_DIAGNOSTIC`; stronger-model comparisons remain `MACHINE_SILVER`.

The archive preserves included files byte for byte, including failed attempts,
raw model requests/responses, source snapshots, proposals, validation and
Reviewer receipts, budgets, versions, scripts, tests and derived tables.
Billing pages/screenshots, local databases, caches and expanded duplicate source
trees remain local. Source ZIPs are included. The original full local manifest
is historical; `EVIDENCE_MANIFEST.json` describes this public package exactly.
Historical machine paths in receipts are recorded provenance, not required
installation paths.

Verify the archive and recompute A–E from saved receipts, without credentials,
SDK installation, or model calls. Choose a new output directory:

```powershell
python docs/evaluation/cloud-credit-sprint-20261023/verify_evidence.py --output work/cloud-credit-replay
```

The replay preserves the original folder layout under its output directory.
It reports 57 generation requests, 56 successful responses, USD 14.40 reserved
and USD 0.38359475 estimated from successful Token receipts. Actual billing and
promotional deduction remain unknown. No further paid run is scheduled;
historical live scripts retain their one-use directories, limits and expiry.

Public-source attribution: AMI Consortium, AMI Manual Annotations release 1.7,
CC BY 4.0. The archived `upstream_metadata.json` includes the release README,
license text and upstream metadata; query-to-dialogue-act provenance is retained.
These human labels qualify retrieval, not exhaustive task extraction metrics.
