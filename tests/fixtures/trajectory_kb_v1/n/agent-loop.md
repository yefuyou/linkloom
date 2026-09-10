# Agent loop control

Tool calls must have stable call identities. A repeated `call_id` is a ledger
conflict. Semantic repetition across different call IDs requires a dedicated
loop guard and must not be inferred from timestamps or run IDs.
