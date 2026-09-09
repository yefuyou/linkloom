# Retrieval contract

Use `search_notes` for broad discovery and `read_verified_note` for an exact
synthetic note reference. An empty search or absent note is a successful tool
envelope with business status `NOT_FOUND`. Partial retrieval preserves valid
evidence while reporting failed branches.
