# Checkpoint boundary

An authorized call is recorded as pending before its executor starts. A pending
read can be retried safely after inspection. A pending operation with unknown
side effects requires a manual decision. Terminal checkpoint failure creates
durability uncertainty and must be reported explicitly.
