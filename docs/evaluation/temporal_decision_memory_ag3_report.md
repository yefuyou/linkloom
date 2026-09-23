# Phase 3 Verification Report: SQLite Temporal Decision Memory

- **Role**: AG-3 baseline, Codex repair verification, and independent Review B
- **Target**: LinkLoom Sprint V2 Phase 3 SQLite Temporal Decision Memory
- **Repository**: `D:\webproject\Linkloom`
- **Execution Date**: 2026-09-23
- **Status**: **PASS (14/14 focused tests passed; independent Review B PASS)**

---

## 1. Summary

The AG-3 baseline established the temporal, relational, and SQLite invariants. The authorized Review B repair then closes the three identified gaps; the current local suite verifies:
1. Exactly one current decision per `(workspace_id, subject_key)`.
2. Clean `SUPERSEDED` state transitions with precise interval timestamps.
3. Left-closed, right-open temporal intervals $[valid\_from, valid\_to)$.
4. Relational provenance (Decision $\to$ Evidence, Decision $\to$ Previous Decision, Decision $\to$ Action $\to$ Owner).
5. Comprehensive workspace isolation across all lookup and search APIs.
6. Database-level partial unique index enforcement preventing split-brain/duplicate active truths.
7. Fail-closed write gates requiring TeamDecision Contract PASS, Grounding PASS, and non-empty explicit approval.
8. Exact SQLite index coverage for performance and isolation.
9. No public ungated decision-write method on `TemporalDecisionStore`.
10. Explicit workspace-scoped source episode/evidence validation before persistence.

The AG-3 baseline was read-only. The current repair changed only the Phase 3 decision-memory package, demo, tests, and evaluation artifact under the user's explicit authorization. No unrelated dirty files were staged or modified.

---

## 2. Validation Commands and Exact Output

### 2.1 Focused Pytest Suite

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/unit/test_temporal_decision_memory.py -q -p no:cacheprovider
```

**Exact Console Output**:
```text
..............                                                           [100%]
14 passed in 0.21s
```

### 2.2 Deterministic Demo Execution

```powershell
$env:PYTHONPATH='src'; python scripts/run_temporal_decision_memory_demo.py
```

**Exact Console Output**:
```json
{
  "output": "D:\\webproject\\Linkloom\\docs\\evaluation\\temporal_decision_memory_demo.json"
}
```

### 2.3 Independent Review B

AGY CLI (`gpt-oss-120b-medium`) reviewed the Phase 3 source, tests, demo, and report in a read-only sandbox. Phase 3 file hashes were captured before and after review and were unchanged.

```text
BLOCKERS: None identified.
VERDICT: PASS.
```

---

## 3. Test Counts & Coverage Matrix

| Test Function | Category | Invariants / Boundaries Locked | Result |
| :--- | :--- | :--- | :--- |
| `test_current_historical_as_of_and_supersession_evolution` | Baseline | Current truth, history order, midpoint `as_of`, evolution transition | **PASS** |
| `test_decision_action_owner_and_evidence_relations` | Baseline | Action ownership, decision evidence ordinal, action evidence links | **PASS** |
| `test_store_rejects_two_current_truths_and_invalid_sources` | Baseline | Supersede requirement, nonexistent supersedes ID, empty evidence rejection | **PASS** |
| `test_candidate_requires_contract_grounding_and_explicit_approval` | Baseline | TeamDecision PASS gate, Grounding PASS gate, empty approval rejection | **PASS** |
| `test_workspace_scope_precedes_search_and_required_sqlite_indexes_exist` | Baseline | Search workspace isolation, PRAGMA index coverage validation | **PASS** |
| `test_required_temporal_demo_answers_current_history_and_evolution` | Baseline | Verification against `build_demo_report()` payload | **PASS** |
| `test_as_of_boundary_conditions_and_tz_enforcement` | Focused Gap | Pre-epoch (`None`), exact start, exact handover point $[valid\_from, valid\_to)$, future query, timezone-naive fail-fast | **PASS** |
| `test_sqlite_unique_index_and_foreign_key_enforcement` | Focused Gap | SQLite engine-level partial unique index (`ux_decision_current_subject`), SQLite foreign key violation on orphan action | **PASS** |
| `test_invalid_action_workspace_and_supersession_ordering` | Focused Gap | Mismatched action workspace rejection, backward/identical supersession timestamp rejection, whitespace-only approval rejection | **PASS** |
| `test_comprehensive_workspace_isolation` | Focused Gap | Multi-workspace identical subject isolation across `get_current`, `get_history`, `get_as_of`, `get_evolution` | **PASS** |
| `test_chained_supersession_three_generations` | Focused Gap | 3-generation linear transition ($A \to B \to C$) leaving exactly 1 current truth ($C$) and 2 evolution steps | **PASS** |
| `test_store_does_not_expose_an_ungated_decision_write` | Review B repair | No public `add_decision` write surface remains | **PASS** |
| `test_materialization_rejects_unknown_episode_and_evidence_fail_closed` | Review B repair | Unknown episode and fabricated evidence are rejected before persistence | **PASS** |
| `test_id_based_reads_require_workspace_scope` | Review B repair | Same IDs cannot be read through another workspace; decision/action/evidence reads are scoped | **PASS** |

**Total**: 14 passed, 0 failed, 0 skipped.

---

## 4. Verified Invariants

1. **One current decision**:
   - Enforced by application logic and SQLite index `ux_decision_current_subject` (`WHERE valid_to IS NULL`).
2. **A SUPERSEDED_BY B closes A and leaves exactly one current truth**:
   - Closed record receives `valid_to = B.valid_from` and `status = SUPERSEDED`.
3. **Historical and as_of interval boundaries**:
   - Validated against $[valid\_from, valid\_to)$. At $t_B$, $A$ is expired and $B$ is active.
4. **Decision $\to$ Evidence**:
   - Stored in `decision_evidence` with preserved `ordinal` and primary key `(decision_id, evidence_ref)`.
5. **Decision $\to$ Previous Decision**:
   - Stored as `supersedes_id` referencing `decision(decision_id)`.
6. **Decision $\to$ Action $\to$ Owner**:
   - Stored in `action` table referencing `decision(decision_id)`.
7. **Workspace isolation before lookup/search**:
   - All query APIs require `workspace_id` and query with `workspace_id = ?`.
8. **No two current truths (including SQLite unique enforcement)**:
   - Direct raw SQL duplicate current insert triggers `sqlite3.IntegrityError: UNIQUE constraint failed`.
9. **Invalid/missing supersedes source handling**:
   - Rejects unannounced overwrites, missing references, and backward timestamps.
10. **Invalid action source handling**:
    - Rejects mismatched `workspace_id`, invalid `source_decision_id`, and raw orphan actions.
11. **TeamDecision Contract PASS plus Grounding PASS candidate gate**:
    - `DecisionMaterializer.propose()` rejects any candidate with `team_decision_contract_pass=False` or `grounding_pass=False`.
12. **Explicit approval required before materialization**:
    - `materialize()` rejects empty and whitespace-only approval strings with `PermissionError`.
13. **No direct public memory write**:
    - `TemporalDecisionStore` exposes no `add_decision`/insert-style public method; the private persistence path re-checks candidate gates and approval.
14. **Fail-closed source references**:
    - An empty `SourceReferenceRegistry` accepts no episode/evidence reference; workspace-scoped known catalogs are required before materialization.
15. **Real SQLite indexes exact coverage**:
    - `idx_decision_workspace_subject`: `("workspace_id", "subject_key")`
    - `idx_decision_workspace_subject_validity`: `("workspace_id", "subject_key", "valid_from", "valid_to")`
    - `idx_decision_source_episode`: `("source_episode_id",)`
    - `ux_decision_current_subject`: `("workspace_id", "subject_key")` (`WHERE valid_to IS NULL`)

---

## 5. Failure Cases

- **Concrete Failures Found in the Tested Phase 3 Boundary**: **0** in the local repair suite.
- The 14 local tests cover the prior Review B findings and the original temporal invariants. Independent Reviewer B found no blockers and returned PASS. No Codex usage reset was used.

---

## 6. Exact Artifacts

- **Verified Test Suite**: `tests/unit/test_temporal_decision_memory.py`
- **Demo Script**: `scripts/run_temporal_decision_memory_demo.py`
- **Demo Output JSON**: `docs/evaluation/temporal_decision_memory_demo.json`
- **Verification Report**: `docs/evaluation/temporal_decision_memory_ag3_report.md`

---

## 7. Limitations

1. **Concurrency / Process-level Lock Contention**: Tests were conducted under SQLite single-connection and `:memory:` contexts. High-concurrency multi-process WAL contention under real Obsidian file-system mounts was not evaluated in this unit suite.
2. **Clock Skew / Distributed Ordering**: System relies on UTC `datetime` timestamps supplied by callers. Monotonic time ordering across distributed machines is not guaranteed by SQLite alone and relies on application-level clock synchronization.
3. **Gate Semantics Evaluation**: The verification confirms that the write gate is strictly fail-closed when given boolean flags. The algorithmic accuracy of model-driven TeamDecision extraction and Grounding checks themselves is evaluated in upstream evaluation pipelines.
