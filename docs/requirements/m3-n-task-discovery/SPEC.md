# M3-N: cross-message Task Discovery context

User-authorized baseline: `e75728796d9d280b8ae6c0d8355598cc32d9edbf`.
Branch: `codex/linkloom-m3-n-task-discovery`. Only offline work is authorized.

## Boundary and data flow

Extend the sole ProviderNeutralSemanticExtractor with an optional, immutable,
strongly typed TaskDiscoveryContext containing an immutable RawArtifact and
deriving its message metadata with the unchanged canonical parser.
TaskObservationDiscoveryService can explicitly enable the new versioned task
profile. The default remains the historical request contract, including ordinary
Decision/Fact extraction, historical replay and the frozen M3-L Host. No product
or LIVE configuration is switched in this milestone.

The new profile presents one unchanged exact source window plus a structural
message map (speaker, speech time, body offsets), with independently versioned
task instructions. It asks the model to inspect every message, include explicit
additional work, distinguish unrelated tasks, and account for quotations,
conditions and actual versus discussed cancellation. An explicitly proposed
continuation may include separately quoted description parts from multiple
messages. A group is only MODEL_PROPOSED, never business identity.

The shared schema, parser, task projection, M1 grounding and conservative veto
stay unchanged. The Host still verifies every description and optional field
against its own canonical message. It does not generate missing descriptions,
inherit owner/date/project/vendor, grant source access or infer authority.
New context must belong entirely to the supplied source window; unsupported or
mismatched context fails before request authorization/transmission.

## Fixed acceptance

1. Freeze new public synthetic development and independently designed holdout
   data before production edits. Record SHA-256, gold purpose and limitations.
2. Record historical raw request/response/projection evidence and old scores;
   reproduce the missing continuation without modifying frozen artifacts.
3. The optional context is strongly typed, bounded, source-bound, versioned and
   fingerprinted. Default prompts/config/request IDs remain byte-equivalent.
4. New task prompt explicitly covers message continuation/additions, distinct
   work, missing fields, quotation, cancellation, condition and language mixing.
   Only a model output may propose task content. No keyword-based generation.
5. Scripted cross-message proposals keep separate verified evidence and local
   fields. Malformed, invented, cancelled, conditional, revoked/stale/foreign
   input remains rejected/partial under existing policy; call limits unchanged.
6. Report candidate Recall/Precision, erroneous grouping, false rejection,
   field accuracy and evidence validity for offline contracts, including Before
   and After with the SAME scripted responses. New real-model semantic quality
   remains NOT_EVALUATED; old raw output replay cannot improve recall.
7. Run affected F/shared extraction/source regressions and M3-L/M3-M critical
   offline budgets/HTTP/product-chain checks; report baseline failures.
8. Independent read-only review, in-scope P1/P2 fixes, commit, normal push and
   remote SHA readback. No PR, merge, real Provider, real-vault operation,
   shared runtime redesign, new extractor, frozen Canary/ledger/Gold/score edit.

## Implementation order

Diagnose and freeze data -> record baseline contract outputs -> failing typed
context/request/projection checks -> minimal shared input and F opt-in -> run
new contracts and unchanged replay -> regressions -> independent review/fixes
-> inspect protected diff -> commit/push/readback.

The explicit profile is the smallest compatibility-preserving entry point.
Enabling it in a real deployment needs new independently authorized quality
evidence; this milestone does not equate a better-specified prompt with better
model output.
