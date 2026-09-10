# Golden 8 Formal Freeze

## Status

**FORMALLY FROZEN**

Golden 8 is the fixed, fast regression and acceptance subset for the first
Team Decision & Action vertical slice. This document freezes only evaluation
governance; it does not authorize or implement M1 production behavior.

## Freeze identity

- Freeze date: `2026-09-11`
- Parent dataset: [`dataset.jsonl`](dataset.jsonl)
- Parent dataset schema version: `team-decision-eval-case/v1`
- Canonical parent dataset representation: exact UTF-8 LF bytes stored in Git
- Canonical parent dataset SHA-256:
  `49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`
- Golden 8 case count: `8`

The canonical identity is the Git/LF byte sequence, not a Windows working-tree
hash. A CRLF checkout may produce a different raw file hash without indicating
content drift.

## Frozen case order and selection rationale

| Order | Case ID | Frozen coverage |
|---:|---|---|
| 1 | `mps-001` | direct decision recovery |
| 2 | `aer-002` | multi-note synthesis |
| 3 | `drm-003` | rejected alternative + rationale |
| 4 | `inc-004` | unresolved/action recovery |
| 5 | `ret-005` | stale/current temporal precedence |
| 6 | `iti-005` | proposed vs assigned / missing-field discipline |
| 7 | `drm-002` | planned/preparation vs completed/authorized |
| 8 | `aer-005` | insufficient evidence / calibrated uncertainty |

The selected IDs are represented machine-readably in
[`golden8_manifest.json`](golden8_manifest.json); no case contents or expected
answers are copied into that manifest.

## Source-of-truth relationship

```text
Accepted 30-case dataset
        ↓ select by IDs only
Golden 8 manifest
        ↓
fast regression / acceptance subset
```

`dataset.jsonl` remains the single Gold source of truth. The manifest holds
identity and ordered membership only; this document holds governance and freeze
policy. Neither duplicates expected answers or full case records.

## Freeze contract

1. Golden 8 selects cases only by case ID from the accepted 30-case Team
   Decision Eval Seed.
2. Golden 8 does not duplicate full case contents into a second Gold source.
3. Membership and order cannot change merely because a model performs poorly.
4. Expected decisions, claims, evidence refs, trajectories, constraints,
   labels, and other Gold semantics cannot change merely to improve evaluation
   scores.
5. Any future Gold correction requires a documented factual or semantic defect,
   independent review, an explicit version/change record, and separate
   reporting of old and new evaluation results.
6. M1 implementation must consume this frozen target rather than redefine it.
7. Evaluation and inference code must never expose Gold labels or hidden
   expected outputs to the model.
8. Golden 8 is the fast regression/acceptance subset; the accepted 30-case
   dataset remains the broader evaluation seed and source of truth.

## What this freeze does not mean

FORMALLY FROZEN does not mean that:

- M1 production Team Decision & Action behavior exists;
- the Agent currently passes Golden 8;
- Gate D is complete;
- Gate F is complete;
- the entire 30-case dataset is permanently immutable;
- production code changes are authorized;
- real-provider calls are authorized; or
- model quality or business accuracy has already been measured.

## Freeze evidence

- The seed validator confirms 30 cases, six synthetic workspaces, 36 notes,
  valid paths/anchors, trajectory constraints, claims, and metric mappings.
- Each frozen case has exactly one dataset record, valid workspace and
  source-note references, required and forbidden claims, and a trajectory that
  forbids a final answer without evidence.
- [`tests/eval/test_golden8_freeze.py`](../../../tests/eval/test_golden8_freeze.py)
  verifies the manifest schema, parent hash, exact ordered IDs, per-ID dataset
  uniqueness, and canonical Git-object SHA-256.
