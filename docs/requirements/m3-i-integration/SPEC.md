# M3-I: product investigation integration

The user's 2026-10-19 request authorizes this boundary, commit and normal push
to a new branch after independent read-only review. No master merge or PR.

## Baseline and dependency order

Remote heads read with `git ls-remote origin --heads` on 2026-10-19:
E `f9b009a0bb60f60f4895a4fdf2df069340ef8e47`,
J `eadf5ab1df473846695f4e10424d2e6e6c4a11cd`,
K `dc00c46024e19085ca1ae0f21c6d0466d5e057ee`.
All share `31dde91f0e9b6015abc5754e995692a2f1db89ec`.
E already contains C/D. J contributes only 4b58dda + eadf5ab; K only
167a466 + dc00c46. Normal merges of J then K preserve their reviewed provenance.
No uncommitted main-directory files are inputs. Historical evidence is immutable.

## Architecture and ownership

Reuse `/api/product/ask`, UnifiedAskService, ProductInvestigationService and
RuntimeRunBackend. Extend that backend's workflow/projection for the existing
`task_relations` Runtime workflow. The Host captures current product sources,
maps repository-issued source ID/version to RawArtifact identity and exact spans,
calls existing F discovery and G planning, and binds supported ready steps to
existing semantic tools. G owns task meaning; the Host performs typed admission,
readiness and source checks. No parallel executor/model loop/database is added.

F/G are bounded preparation stages, separately audited from the Runtime tool
ledger. Their model requests, responses and usage must be retained. Runtime
remains the sole checkpoint, ToolRuntime, ledger, Grounding and Reviewer authority.
E reopens original source spans each turn; its summary is operational history.
Simple reviewed state questions retain the original deterministic route.

The minimal alternative is leaving F/G as CLI demos; rejected because it does
not provide the requested product path. Future pressure points are semantic
review authorization and durable product job discovery across server restarts.

## Scope and gates

Read only, bounded source inventory, explicitly injected FAKE/REPLAY adapters;
new paid Provider requests are disabled. Plans and observations remain
unconfirmed. Evidence-valid semantic proposals are not semantic truth. No
promotion to P4 or Decision Memory. A dependency needing semantic SUPPORTED
cannot be satisfied by an unreviewed proposal. Expose the gap and stop safely.
Unsupported/time-scoped operations must be explicit gaps, never silently widened.
Missing recall does not establish absence. Replanning may retain only Host
verified completed work, never promote proposal verdicts into receipts.

## Acceptance and checks

1. Multi-document original meeting/project sources and a natural question enter
   Unified Ask without task IDs, answer entities or a supplied tool order.
   F and G run on real code; Runtime tools, E recovery and Reviewer execute.
2. The scripted test adapter is labelled FakeModel integration, not autonomous
   semantic performance. Real-model semantics and real product business tasks
   are NOT_EVALUATED without new authorized Provider evidence.
3. Deterministic state lookup invokes no Agent adapters. Missing/conflicting
   evidence remains UNKNOWN/PARTIAL/BLOCKED with reasons and provenance.
4. Stale/removed sources are blocked before execution and publication, including
   inspection of a previously completed run. No stale citations are returned.
5. Resume uses the existing checkpoint and cannot rerun confirmed tool actions.
   E pressure cases recover originals or stop safely. Existing E regression
   tests supplement product tests, with their evidence layer identified.
6. Report stage reservations/usage, source identity mapping, tool trace, Reviewer,
   failures, regressions and incomplete capabilities. No frozen asset is edited.
7. Independent read-only review precedes final commit/push. No PR/master merge.

## Implementation sequence

Contract tests -> source/Host adapter -> existing backend workflow/projection ->
Unified Ask routing -> product regression/trace runner -> independent review ->
repair/verify -> commit/push/readback. This document records the approved user
boundary, not an additional approval gate.
