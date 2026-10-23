# SPEC: AMI first real-provider semantic canary

Boundary approved by the user's 2026-10-23 request. The user subsequently
confirmed credit eligibility and instructed execution without more credit
inspection. Credit attribution is user-confirmed, not independently verified.

Reuse the unchanged AMI adapter, F extractor, Gemini 3.8 Flash adapter,
TaskObservationDiscoveryService and TaskProviderSession. Use complete ES2002a
and ES2005c canonical transcripts (8 + 20 mechanical chunks), with no summaries,
queries, qrels, retrieval or Gold in the model process. Native speakers, relative
seconds and offsets stay in transcript headers/sidecars; do not fabricate dates.

One ordinary document RawArtifact per existing chunk. Use the general F prompt
to retain all claim types, default Discovery profile (task-only projection),
4,096 output tokens, low thinking, one SDK/F attempt. Every three chunks form
one existing bounded Discovery batch. A single global existing Provider session
covers both meetings: 28 calls, USD 0.10 reserved per call, USD 2.80 total,
1,400,000 input / 114,688 output tokens, 2,700 seconds, 90 seconds per call.
Stop the entire run on Provider uncertainty/failure; never automatically resume.

Acceptance:

- Re-prepare official archive, revalidate 51 eligible / five N/E, reproduce
  sealed retrieval metrics and test input isolation; preserve frozen evidence.
- Save exact requests, SDK raw responses, normalized responses, F claims,
  Discovery observations/failures, native references and budgets. Seal before
  opening evaluator-only labels. No production code, Guard, P4, Reviewer,
  Compactor, Gold, source notes or authoritative Product memory changes.
- Reserve before send; combined estimate and reservation <= USD 3. Report
  provider-reported tokens, thought tokens and estimated list-price costs
  separately from billing amounts (not available from model responses).
- Official human Gold supports summary-to-DA association retrieval. Extraction
  Precision/Recall require exact task alignment; otherwise report diagnostic
  matching with explicit exclusions, not invented exhaustive negatives.
- Trace model omissions separately from F parsing/Discovery losses. Product
  E2E is NOT_RUN; no conclusions about Product retention without executing it.
- At most this one two-meeting canary. Normal push and independent Draft PR only
  from an initially clean isolated worktree, with a scoped reviewed diff. No merge.

Checks: existing AMI tests; existing Provider budget/discovery/extraction
contracts; fresh retrieval replay; no-network request preparation; independent
review of the runner and reported evidence. This is a previously exposed,
exploratory two-meeting canary, not a blind generalization result.
