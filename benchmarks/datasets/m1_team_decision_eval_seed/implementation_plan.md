# M1 Team Decision & Action Eval Seed Implementation Plan

## Boundary

This plan produces only the synthetic data, its contract, and deterministic
consistency checks under this directory. It has no production-code or runtime
dependency.

## Steps

1. Read the governing repository and Master SPEC contracts and freeze the
   dataset schema, case distribution, privacy boundary, and metric registry.
2. Create six realistic synthetic Markdown workspaces with dated notes,
   authority/status metadata, stable headings, current-vs-stale records, and
   hard-negative distractors.
3. Author 30 machine-readable Gold cases with source/relevant/distractor
   scopes, claim-level evidence, structured actions, uncertainty behavior, and
   preferred plus acceptable symbolic trajectories.
4. Implement a standard-library-only validator for JSONL shape, counts, safe
   paths, heading anchors, trajectory constraints, claims, and metric names.
5. Run the validator and read-only scope/format checks. Do not run or alter
   M0.2/M0.3, production code, existing tests, a provider, or a real vault.
6. Hand the completed seed to an independent Reviewer for case-by-case
   semantic verification. The authoring agent reports readiness only; it does
   not accept the corpus.

## Completion evidence

The completed artifact must report exact case/note/workspace counts,
difficulty/scenario distribution, hard-negative and uncertainty coverage,
validator output, representative cases, limitations, and independent-review
status. Any semantic finding must name the case and evidence note/heading.
