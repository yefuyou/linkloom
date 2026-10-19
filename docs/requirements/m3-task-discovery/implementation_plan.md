# Implementation plan

1. Inspect real contracts; record the user-approved SPEC and bounded coverage.
2. Add failing FakeModel tool-contract and M1 validation tests.
3. Implement bounded admission, existing extractor scheduling, projection,
   exact revalidation and explicit result categories in a new M1 module.
4. Expand independent synthetic failure/coverage cases; run focused regressions.
5. Freeze new raw inputs and separate labels, execute at most three authorized
   live calls, save requests/results/usage before independent scoring.
6. Independent read-only Reviewer checks evidence and scope; Worker fixes
   substantive findings and reruns affected checks.
7. Record delivery, precise staging, commit, normal push, verify remote SHA, stop.

Learning reflection: M1 validates source fields but does not decide whether work
is an actual commitment. Scheduling must preserve model semantics, use rejection
guards and expose uncertainty rather than treating absent proposals as no tasks.
