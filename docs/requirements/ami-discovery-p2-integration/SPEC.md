# SPEC: AMI Discovery P2 integration

User-approved scope (2026-10-23): port the accepted quote-boundary repair from
`f68c3e17d0de0c88d5ace0f84cf2b3c5a25fccda` onto current master
`1bfd910bb3a53a902a708ca2d00a39f4dde68091`, using an isolated worktree.
Preserve the original commit and its 455-pass receipt. No feature expansion,
paid Provider call, master merge or edits to merged #13/#15/#17 contracts.

Implementation and acceptance:

1. Independently review the actual production diff, Unicode/source invariants
   and nested matching cost. Check bounded adversarial input sizes separately
   for the parser and the existing overlapping-range consumers.
2. Port only `text_scopes.py` and the generic apostrophe/nesting regressions;
   add them to the existing offline CI job. Do not import #16 archives or Gold.
3. Replay #16's externally supplied, sealed 28 responses against both master
   and candidate code, with network blocked. Assert exact requests and all 75
   original claims unchanged. This validates the original F profile, not a new
   task-recall/v3 model run. Report Discovery changes and the known model event
   false positive separately from true task recovery.
4. Run affected source/permission, task identity, UNKNOWN, P4, Reviewer and
   quote tests and `git diff --check`. Preserve explicit before/after receipts.
5. If no P0/P1/P2 blocker remains, commit and push a new independent branch,
   create a Draft PR targeting master, inspect CI at its exact head, and hand
   off that SHA for a separately authorized live Canary. Do not run it here.

NON_ASSERTED, F prompts/schema, source validation and authority contracts must
not be relaxed. The finite elision lexicon is punctuation lexing, never AMI
sentence recognition. Existing natural-language ambiguities and overlapping
range masking costs remain explicit limitations, not semantic success claims.
