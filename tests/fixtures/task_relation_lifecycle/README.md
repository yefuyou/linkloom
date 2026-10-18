# M2-P4 relation lifecycle Dev fixture

This fixture is intentionally separate from the P1/P2/P3 public inputs and
Gold files. The `inputs/` file contains only allowlisted case references and a
new synthetic lifecycle sequence; it has no expected verdicts, lifecycle
labels, or comparison deltas. The evaluator-only `expected/dev.json` freezes
the observable Dev assertions.

The lifecycle cases exercise the real M1 observation constructors and the M2
P1/P2/P3 assessment/comparison constructors at runtime. Relation persistence
uses a fresh SQLite path supplied by the test or evaluation script. The side
store keeps the original assessment payload, including its M1/EvidenceSpan
locator and complete quote snapshot for auditability, but does not add a
second copy of the RawArtifact document body. Source artifacts and the current
registry are passed to the read-only query for verification.
