# memory-live-001

This synthetic fixture is reserved for M1.2b Decision Memory live-coverage
qualification. It is not a member of the frozen semantic benchmark, does not
change its Gold data or metrics, and is never sent to a real Provider during
offline qualification.

The notes intentionally contain both a superseded Provider A approval and a
later Provider B approval. The deterministic SQLite Decision Memory fixture
marks Provider B `CURRENT` and points to `vault/final-approval.md` as its source
evidence. Runtime qualification must still search and read that source before
producing a grounded Final.
