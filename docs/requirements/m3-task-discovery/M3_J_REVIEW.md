# M3-J independent read-only review

Verdict: **PASS with notes** for the F-only refusal/structural coverage repair.
Semantic-recall improvement remains **INTERFACE_BLOCKED / NOT_MEASURED**;
this is not acceptance of the entire semantic-recall objective.

Reviewer: independent subagent `/root/m3j_review`, `gpt-5.6-luna`, reasoning
`max`. It read the approved user boundary, F SPEC, M3_J.md, original frozen
responses/scoring, current implementation/diff and new evidence. It ran only
read-only Git/file inspection and in-memory `python -B` FakeModel probes.
It did not edit files, run pytest, change Git or call a network model.
The Worker implemented fixes and wrote this record from returned review results.

## Findings closed

- P1: reverse cancellation, intervening verbs/punctuation and bare Chinese or
  English task cancellation could release actual cancelled work. Added explicit
  regression cases and fixed bounded cancellation scopes, including optional
  English `the`.
- P1: common conditional plans and suffix conditions could release noncommitted
  work. Condition/refusal scopes now propagate conservatively across clauses.
- P1: ASCII single-quoted and inline-code work could be accepted, and quoted
  cancellation could affect outside work. Quote/code masking preserves original
  offsets and scope punctuation; internal apostrophes remain word content.
- P2: source/sentence-wide referential cancellation and comma/semicolon refusal
  could suppress independent actual tasks. Negative scopes now close at explicit
  independent assignments without creating tasks or migrating fields.
- P2: discussion-only cancellation could suppress an earlier actual task.
  Discussion/proposal qualifiers do not themselves retract another occurrence.

The Worker recorded RED and GREEN evidence, then the Reviewer independently
rechecked these categories and returned the final PASS with notes. New scripted
set: **68 cases**, **18/18** positive candidates retained, **0 false accepts**
and **0 false rejects**. These are development contracts, not model quality.
Final focused checks: **114 passed**. Final related result is in
`evidence/m3j/related.xml` and `checks.json`.

## Boundaries verified

The existing ProviderNeutralSemanticExtractor remains the sole semantic
extractor; shared extraction and M1 validation files are unchanged. There is no
prompt-version injection, provider-request rewrite, new dependency, authority,
storage mutation or runtime integration. Default max_calls=3 and one attempt
remain unchanged; new paid provider calls=0.

Cross-message observations preserve each original canonical evidence and bind
fields locally. Shared owner/project/day does not group distinct proposals;
missing deadline year stays unknown. Stale/revoked/foreign source references
are withheld from candidate inventory. The inventory does not imply semantic
exhaustiveness. Frozen three-source replay retains its original observations,
groups, failures and coverage fields, with historical **4/5 Recall / 4/5
Precision**, **24/24 fields** and **5/5 evidence**. Historical Gold, predictions
and score files remain unchanged.

## Notes retained

1. The original model omitted `补齐回归脚本` before projection/veto. The shared
   extractor lacks an F-specific input/instruction extension. No new live quality
   evaluation is authorized, so genuine semantic recall is neither fixed nor
   measured here. A shared-interface decision is a separate task boundary.
2. A wide model excerpt containing quoted and actual occurrences of identical
   work remains conservatively unresolved. A precise independent actual excerpt
   can be released; Host does not guess the intended occurrence.
3. `此任务取消：整理旧截图` can still withhold an earlier unrelated task because
   its reference conflicts with its named object. This known conservative
   ambiguity is disclosed; no universal anaphora/semantic correctness is claimed.
4. Full unit/integration collection has the same four pre-existing missing-script
   errors, documented in full-collection.xml. CI/UI/deployment/real vault/live
   provider validation are not claimed.

Final Reviewer statement: **“最终收口确认：PASS with notes。”** It requested
only that the last related test result be recorded before commit; no additional
scope expansion or lexical probing was required.
