# Golden 8 Formal Freeze

## Status

**FORMALLY FROZEN — 2026-09-10**

This document fixes the first small regression subset for the Team Decision &
Action vertical slice. It freezes evaluation data and the subset contract only;
it does not authorize or implement M1 production behavior.

## Canonical dataset identity

- Dataset: [`dataset.jsonl`](dataset.jsonl)
- Canonical representation: exact UTF-8 LF bytes stored in Git
- SHA-256:
  `49A955D98C18E9BDE8609C2747BA9BEF55516EFEF878FD52F37E2300A16D1C9F`

The canonical identity is the Git/LF byte sequence, not a Windows working-tree
hash. A CRLF checkout may therefore produce a different raw file hash without
indicating content drift.

## Frozen case order

| Order | Case ID |
|---:|---|
| 1 | `mps-001` |
| 2 | `aer-002` |
| 3 | `drm-003` |
| 4 | `inc-004` |
| 5 | `ret-005` |
| 6 | `iti-005` |
| 7 | `drm-002` |
| 8 | `aer-005` |

The machine-readable counterpart is
[`golden8_manifest.json`](golden8_manifest.json). The ordered IDs are part of
the contract; a set with the same members but a different order is not this
freeze.

## Freeze evidence

- The seed validator confirms 30 cases, six synthetic workspaces, 36 notes,
  valid paths/anchors, trajectory constraints, claims, and metric mappings.
- Each frozen case has one dataset record, valid workspace and source-note
  references, required and forbidden claims, and a trajectory that forbids a
  final answer without evidence.
- [`tests/eval/test_golden8_freeze.py`](../../../tests/eval/test_golden8_freeze.py)
  verifies the exact ordered IDs, absence of unknown cases, and the canonical
  Git-object SHA-256.

## Change control

Golden 8 is a fixed regression baseline. Do not edit `dataset.jsonl`, reorder
the subset, or substitute cases to improve a model result. Any intentional
change requires a versioned freeze update with fresh canonical Git/LF hash,
focused regression evidence, and independent review.

## Explicit non-goals

This freeze does not implement Team Decision & Action, call a model or
provider, access a real Vault, modify runtime behavior, or report any quality
metric. It is the data baseline for later approved M1 work.
