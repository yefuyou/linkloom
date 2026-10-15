# LinkLoom Portfolio Notes

## Resume-ready bullets

- Built a local-first decision-recovery Agent that searches scattered project
  records and returns a strict, evidence-grounded decision brief covering the
  final choice, rationale, alternatives, actions, unresolved items, and
  uncertainty.
- Designed the durable Agent boundary around one auditable action per turn,
  read-only tools, bounded budgets, strict `TeamDecisionResult` validation, and
  claim-level evidence references so unsupported claims fail closed instead of
  becoming UI copy.
- Shipped a Product UI that makes the final decision legible in seconds and
  links every material claim to verified source excerpts, while separating
  insufficient evidence from operational failure and hiding Agent trace behind
  provenance.
- Created frozen synthetic evaluation cases and first-result real-provider
  harnesses with Gold withheld until terminal sealing, source immutability and
  credential scans; the evidence surfaced both a grounded DeepSeek success and
  a repeatable multi-tool-call interoperability blocker without resampling.

## Architecture diagram content

```text
Project lead / PM / team member
              │
              │ decision question
              ▼
      Product UI / HTTP boundary
              │
              │ provider-neutral run request
              ▼
       Durable Runtime coordinator
              │
              ├── bounded model turn ──► Gemini / DeepSeek adapter
              │                              │
              │                              └── one ToolCall or Final
              │
              ├── read-only ToolRuntime
              │       ├── search_notes
              │       └── read_verified_note
              │
              ├── checkpoints + trace + usage
              │
              └── strict TeamDecisionResult
                         │
                         ├── claim-level evidence validation
                         └── provider-neutral UI projection
                                      │
                                      ▼
                         Decision Brief + Evidence Inspector
```

## Story to tell

The hard part was not producing fluent summaries. It was preserving the line
between what the team actually decided, what the records merely compared, and
what the documents never assigned. LinkLoom therefore treats evidence and
uncertainty as product structure, not decorative citations. The real-provider
evaluation also demonstrates why infrastructure and semantics must be scored
separately: a model can fail before a business answer exists, and that outcome
must not be mislabeled as bad reasoning.
