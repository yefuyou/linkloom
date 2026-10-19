# Baseline context audit

Audited baseline: `26392432dcef70692461e6f2f50bc1bd5528f69a`.
File/line locations below refer to that baseline unless labelled new.

## Where context is lost

| Layer | Limit and behavior | Model visibility / recovery |
|---|---|---|
| Retrieval candidate generation | `HybridRetriever.candidate_k=20`; RuntimeRetrievalBackend expands candidate documents, extracts up to 32 passages per document, then returns the requested `top_k`. `search_notes.limit` is 1..50. | Candidates outside this retrieval result never enter ContextAssembler or the ledger. Re-search is required; a checkpoint cannot recover an unobserved candidate. |
| ContextAssembler | 5 evidence, 3 memory, 2 experience, 12,000 characters, 3,000 estimated `ceil(len(text)/4)` tokens. First builds only the top 5 evidence candidates, then checks fit. | Oversized entries consume candidate slots even when omitted. RuntimeAgentAdapter filters search results to selected evidence IDs **before returning the tool result**. Omitted passages are absent from the run's evidence map and durable Tool Ledger. Source files may still exist, but replaying the ledger cannot recover these never-returned candidates. |
| Verified Evidence Context | At most 8 **ToolResult records**, not necessarily 8 passages; 24 KiB of serialized results. Verified reads first, one observed subgoal representative before other reads, then recent searches; deduplicates exact passage identities. | Selection drops only a model projection. Complete observed tool results remain in the ledger/checkpoint. The existing projection can be reconstructed, subject to current source verification at the source boundary. This projection itself checks provenance/scope, not a fresh disk read on every turn. |
| Tool History / grouped tool turns | 16 tool results and 128 KiB, chronological suffix without splitting a grouped proposal/result turn. Legacy `tool_history` is validated at construction and can reject oversize input before request trimming. | Old exchanges leave the model window, while model artifacts/ledger remain durable. History is an execution protocol; it is not a verified source catalog or semantic summary. |
| Final request budget | `_bounded_model_request` enforces 96 KiB on serialized `ModelTurnRequest.to_dict()` in ModelLoop, not the ModelTurnRequest constructor. Drops oldest grouped turns, then oldest legacy history; preserves current observation and verified evidence. Fails if these cannot fit. | Eviction affects the provider projection. The durable records remain. ModelLoop also reserves a 32 KiB provider user-input allocation with 2 KiB envelope allowance when sizing the evidence projection. These are byte limits, not a model-specific tokenizer/window guarantee. |
| Tool Ledger | Authorized pending transition, then normalized completed/failed terminal outcome; defensive copies and checkpoint projection. | Persistent observed actions/results, including failures. Our compaction does not edit or replace it. Existing execution transitions remain Runtime-owned. |
| Checkpoint / model artifacts | RuntimeState serializes tool ledger and model execution records; artifact references locate durable requests, responses and observations. | Enables execution recovery and reconstruction of observed results. It cannot turn stale source snapshots into current facts, nor recover candidates never returned by a tool. |

## Key existing call chains

- `agents/runtime_adapter.py:165 _search_notes` -> backend `search(top_k=limit)` ->
  `:423 _assemble_context` -> ContextAssembler -> selected-ID filter ->
  `_evidence_by_id` / search result -> ToolRuntime -> Tool Ledger / checkpoint.
- `runtime/model_loop.py _verified_evidence_context` -> ledger results -> verified
  projection -> ModelTurnRequest -> `_bounded_model_request` -> durable request
  artifact -> provider adapter. Tool-turn grouping and Provider continuation are
  kept separate from semantic evidence.
- `agents/runtime_adapter.py:609 _restore_evidence` -> current documents -> exact
  content/quote/identity checks -> run-local evidence catalog. It requires an
  existing observed ledger passage; it does not discover discarded search hits.

## New boundary

The default Assembler now fills slots only after fit, scans at most 256 ranked
candidates, estimates multilingual text and emits a selection/budget report.
Workspace/path checks and memory/experience provenance gates remain. Explicit
negative permission/currentness metadata also excludes evidence.

`ContextPolicy` and `RequestUsage` coordinate the assembled text with caller-known
model allocations. Atomic task groups and subgoal reports are optional explicit
inputs. Existing Runtime callers do not supply the new inputs; no automatic
cross-layer budget sharing, discovery of contradictions or compression is claimed.

`EvidenceRecovery` reopens source snapshots through an authoritative injected
loader and validates immutable locators. Its `context_ev_v1_*` IDs include exact
character offsets as well as source/version/hash/line coordinates. They are an
independent locator namespace; future Runtime wiring must persist/convert exact
Runtime passages to these locators rather than treating IDs as interchangeable.

`WorkingContext` persists operational state and raw ledger/checkpoint locators.
`compact_working_context` accepts a Compactor proposal, preserves protected state,
and verifies references using recovery. Rendering labels the entire state as an
untrusted operational hint. Business conclusions require `RecoveryResult.evidence`
(original quotes); neither a summary nor a serialized locator proves a fact.
