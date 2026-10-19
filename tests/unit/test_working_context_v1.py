from dataclasses import replace
import json

import pytest

from linkloom.context.policy import estimate_tokens
from linkloom.context.recovery import EvidenceRecovery, EvidenceRef, digest
from linkloom.context.working import (WorkingContext, RecordRef, ToolState, EvidenceConflict,
    CompactionProposal, compact_working_context)
from tests.unit.test_context_recovery_v1 import snapshot


class FakeModel:
    def __init__(self, proposal):
        self.proposal = proposal
        self.requests = []

    def compact(self, request):
        self.requests.append(request)
        return self.proposal


def working():
    source = snapshot()
    refs = (EvidenceRef.from_snapshot(source, 0, 4), EvidenceRef.from_snapshot(source, 5, 9))
    failure = ToolState('failed-read', 'failed', 'SOURCE_NOT_FOUND')
    state = WorkingContext(workspace_id='w', run_id='run', user_goal='选择供应商并修订计划',
        completed_subgoals=('find supplier',), unresolved_questions=('which claim is current?',),
        evidence_refs=refs, conflicts=(EvidenceConflict('supplier', (refs[0].evidence_id,), (refs[1].evidence_id,)),),
        negative_evidence_ids=(refs[1].evidence_id,), last_tool=failure, failed_tools=(failure,),
        next_investigations=('read revised plan',),
        ledger_ref=RecordRef('w', 'run', 'ledger', 'ledger.json', digest('original ledger')),
        checkpoint_ref=RecordRef('w', 'run', 'checkpoint', 'checkpoint.json', digest('original checkpoint')),
        operations=tuple('searched candidate list ' + 'noise ' * 60 for _ in range(40)))
    return source, state


def test_fake_compaction_roundtrip_preserves_conflicts_failures_and_raw_refs(tmp_path):
    source, state = working()
    ledger = tmp_path / 'ledger.json'
    ledger.write_text('original ledger')
    fake = FakeModel(CompactionProposal('Prior searches completed; conflict and failed read remain.',
                                      (state.evidence_refs[0].evidence_id,)))
    before = state.to_json()
    result = compact_working_context(state, compactor=fake, recovery=EvidenceRecovery(lambda w, r: source), max_tokens=2000)
    assert result.status == 'complete'
    assert result.state.operations == ()
    assert state.to_json() == before
    assert ledger.read_text() == 'original ledger'
    for name in ('user_goal', 'completed_subgoals', 'unresolved_questions', 'evidence_refs', 'conflicts',
                 'negative_evidence_ids', 'last_tool', 'failed_tools', 'next_investigations', 'ledger_ref', 'checkpoint_ref'):
        assert getattr(result.state, name) == getattr(state, name)
    restored = WorkingContext.from_json(result.state.to_json())
    assert restored == result.state
    assert result.compression_ratio < 0.5
    assert 'UNTRUSTED_OPERATIONAL_HINT' in restored.render()
    assert len(fake.requests) == 1
    assert result.recovery.evidence[0].quote == '支持甲。'


def test_unknown_summary_ref_cannot_be_promoted_to_verified_evidence():
    source, state = working()
    for proposal in (CompactionProposal('invented', ('unknown',)),
                     CompactionProposal('context_ev_v1_' + 'a' * 64, ())):
        with pytest.raises(ValueError, match='reference'):
            compact_working_context(state, compactor=FakeModel(proposal),
                recovery=EvidenceRecovery(lambda w, r: source), max_tokens=2000)


def test_missing_current_source_is_incomplete_and_summary_is_never_evidence():
    source, state = working()
    result = compact_working_context(state, compactor=FakeModel(CompactionProposal('Supplier A wins.', ())),
        recovery=EvidenceRecovery(lambda w, r: replace(source, current=False)), max_tokens=2000)
    assert result.status == 'incomplete'
    assert not result.recovery.evidence
    assert result.state.conflicts == state.conflicts
    assert result.state.failed_tools == state.failed_tools


def test_compaction_budget_cannot_be_met_by_dropping_protected_fields():
    source, state = working()
    result = compact_working_context(state, compactor=FakeModel(CompactionProposal('short', ())),
        recovery=EvidenceRecovery(lambda w, r: source), max_tokens=10)
    assert result.status == 'budget_exhausted'
    assert result.state == state


def test_cross_workspace_refs_and_lost_failures_rejected_on_deserialization():
    _, state = working()
    with pytest.raises(ValueError, match='workspace'):
        replace(state, ledger_ref=replace(state.ledger_ref, workspace_id='other'))
    with pytest.raises(ValueError, match='failure'):
        replace(state, failed_tools=())
    data = json.loads(state.to_json())
    data['schema_version'] = 2
    with pytest.raises(ValueError, match='schema'):
        WorkingContext.from_json(json.dumps(data))


def test_second_turn_compaction_keeps_prior_failure_and_new_investigations():
    source, state = working()
    recovery = EvidenceRecovery(lambda w, r: source)
    first = compact_working_context(state, compactor=FakeModel(CompactionProposal('First turn.', ())),
                                    recovery=recovery, max_tokens=2000)
    second_turn = replace(WorkingContext.from_json(first.state.to_json()),
        operations=('new search returned nothing',), last_tool=ToolState('next-search', 'completed'),
        next_investigations=(*state.next_investigations, 'inspect new supplier'))
    second = compact_working_context(second_turn, compactor=FakeModel(CompactionProposal('Second turn.', ())),
                                     recovery=recovery, max_tokens=2000)
    assert second.state.failed_tools == state.failed_tools
    assert second.state.conflicts == state.conflicts
    assert second.state.last_tool.call_id == 'next-search'
    assert second.state.next_investigations[-1] == 'inspect new supplier'
