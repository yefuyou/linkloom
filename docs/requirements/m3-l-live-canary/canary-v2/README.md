# M3-L synthetic Canary v2 freeze

This directory is a byte-frozen, synthetic input package for offline Canary
checks. It contains no private vault data, provider response, credential, or
network instruction.

`sources.json` is the inference input. Its four source texts use the existing
`timestamped-text/v1` parser format: one ASCII `:` after each speaker label.
The three messages in D are intentionally independent messages so each gets a
separate timestamp, speaker identity, message ID, and exact evidence offset.

`cases.json` selects the source sets A+B, A+C, and A+C+D. `runtime_config.json`
freezes the model and request/cost limits. `rubric.json` is evaluation metadata
and must remain separate from model input; it contains the only scoring
categories and dimensions for this campaign.

`rubric.json` adds an all-case hard failure for publishing a confirmed
`SAME_TASK`, final/effective deadline, or `BUSINESS_COMPLETE` claim without a
trusted receipt. Every dimension has fixed 0/1/2 anchors; empty or `UNKNOWN`
scores are forbidden, and category assignment is deterministic with explicit
precedence.

`freeze_manifest.json` records SHA256 values for every frozen artifact in this
directory except itself. The loader pins the manifest's own SHA256 in
`src/linkloom/ui/canary_spec.py`, then verifies every listed artifact before
returning it. The v1 directory remains the historical freeze; v2 reuses its
source, case, and runtime bytes and changes only this rubric, README, and
manifest version.
