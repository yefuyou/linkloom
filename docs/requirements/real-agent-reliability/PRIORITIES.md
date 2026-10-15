# Measured priorities before production edits

2026-10-15. Severity × observed frequency × repairability / implementation
complexity (ordinal 1–5; these are prioritization judgments, not probabilities).

| Candidate | Evidence | Score | Decision |
| --- | --- | --- | --- |
| Missing required source identity accepted at ToolRuntime boundary | Security audit F-02: expected identity configured, omitted fields still invoke callback | 3×5×5/1=75 | Select: reject omission before executor. No claim of observed cross-workspace data access. |
| Repeated whole-vault verification | 500 synthetic sources, 12 read_notes calls per two-hop run; median 23.65s, roughly 16.7–21.6s in these reads | 4×5×2/4=10 | Measure/report. Replacing freshness with mtime or stale snapshots would weaken safety; shared index lifecycle needs separate architecture. |
| Tight 12-turn limit | Real two-hop 5 turns, three-hop 7, zero invalid calls | 2×1×3/2=3 | No default increase justified. Historical 11 is search/read policy, not a minimum. |
| Empty evidence context under Product workspace | Every request in real 5-turn/7-turn success has an empty evidence_context. Tool values carry ws_* while loop filtering uses root_*; bounded tool history is the sole remaining model evidence | 4×5×5/2=50 | Select: pass trusted application workspace separately from source fingerprint; preserve both checks and old-read retention. |
| Duplicate evidence prefix | Replay of the actual requests gives zero byte saving; the prefix was empty because of the identity defect above | 1×1×1/1=1 | Reject standalone optimization. A draft adapter edit was reverted before acceptance. |
| Local actor label lacks authentication | F-01: actor string + boolean human confirmation, local-only application | 4×5×1/5=4 | Report boundary; enterprise authentication/RBAC outside sprint. |
| Provider interruption | First ConnectError; fresh user-authorized retry succeeded; conflict later RemoteProtocolError | 4×2×1/4=2 | Preserve UNCERTAIN/no automatic resend. Network redesign outside sprint. |

The two identity defects are selected. The second was isolated while testing the
context duplication hypothesis, before any acceptance execution. There is no
claim that the existing production context was successfully deduplicated: it was
incorrectly filtered out. Restoring necessary evidence can increase input cost.
Raw run/ledger evidence stays under ignored work/real-agent-reliability/.
