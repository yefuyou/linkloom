# Read-only gap mapping (before production edits)

| Real observation | Production path / existing guard | Initial disposition |
| --- | --- | --- |
| R07 Argon NONE with identity/owner/scope uncertainty | `task_relation_semantic_proposer._decode` accepts typed SUPPORTED/REFUTED/UNKNOWN/CONFLICT only; `assess_same_task` requires affirmative identity/counter-proof | Research schema rejected; adapted false REFUTED remains MODEL_PROPOSED, not P4. No NONE conversion to add. |
| R02 Flash REVISES_PLAN instead of REVOKED, requires_review=false | `validate_proposals` checks exact quotes, polarity and model identity premise; `SemanticAnalysis._proposal_view` always publishable=False; P4 replays `assess_plan_revision` | Exact-source validation is not semantic acceptance. No production REVOKED predicate or model review waiver. Business revocation is N/E. |
| R01/R03/R05 owner and completion overclaims | M1 `build_task_observation`, `TaskObservation` authority invariant; `ui/task_result` and task DOM label source/model fields unconfirmed | Verify owner falls back to unknown for isolated you. Completion is not a business field exposed by semantic proposal API. Field-binding semantic truth remains unverified. |
| R04 real scope revision | Directed subject=new/object=old; P4 requires same-task proof; date math and effective-time remain separate | English research context need not satisfy bounded P4 grammar. Do not expand grammar to fit data. |
| R05 AFFECTS prerequisite | M3 AFFECTED_BY requires a typed ChangeRef; P4 revision comparison requires separate verified premises | AFFECTS is not automatically convertible. No actual delay/duration proof; N/E. |
| R06 same-meeting contradiction with correction | CONFLICT proposals never publishable; P4 conflict blocks comparison/unique plan | Pure unresolved cross-meeting conflict remains N/E; never count R06 as that coverage. |
| R08 denied B; R09 historical B | `context_errors`, current registry checks, semantic snapshot binding and review; `RelationQuery` current replay | Controlled fixture revocation/version tests required, not production ACL certification. |
| Confirmed tool projection leak | `_relation_status` aggregated historical assessment verdicts, even when `RelationRead.category=HISTORICAL`, evidence invalid or lifecycle retracted | Seven RED failures in `red-currentness-02.xml`. With include_history=True, invalid REFUTED/CONFLICT leaked into current step status; historical REFUTED also overrode current UNKNOWN. Fixed by filtering current ACTIVE/VERIFIED reads before aggregation. Historical rows remain visible. |

Audit reflection: existing layers separate quote integrity from semantic/business
authority. The confirmed gap was a projection of already-invalidated records,
not a new semantic proof mechanism. The RED run preceded production edits.
`green-currentness.xml` records 17 passing focused/new and existing checks.
