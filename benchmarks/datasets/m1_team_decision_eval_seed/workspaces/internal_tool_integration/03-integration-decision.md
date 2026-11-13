---
date: 2026-04-10
status: approved
authority: decision_record
---
# Internal Tool Integration — Final Decision

## Decision

The first integration slice will use the approved read-only connector and an
export bundle. The connector may read source records; the bundle carries
provenance for offline review.

## Rejected Alternative

The write-enabled direct API was rejected because it would cross the read-only
permission boundary and make source mutation available before human-confirmed
writeback exists.

## Future Boundary

Any source mutation remains outside this slice and must use the separately
reviewed human-in-the-loop writeback path. The decision does not authorize a
direct write demo.

## Approval

The integration review group approved this decision on 2026-04-10.
