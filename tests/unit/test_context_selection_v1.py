from dataclasses import replace

import pytest

from linkloom.context.assembler import ContextAssembler, ContextBudget, ContextSourceType
from linkloom.context.policy import ContextPolicy, EvidenceGroup, RequestUsage, estimate_tokens
from tests.unit.test_context_assembler import _evidence


def ids(bundle):
    return [i.item_id for i in bundle.selected if i.source_type is ContextSourceType.EVIDENCE]


def test_oversized_top_five_backfill_with_later_short_evidence():
    long = replace(_evidence('long', 1), metadata={'text': 'x' * 6000})
    bundle = ContextAssembler(budget=ContextBudget(max_evidence=2, max_chars=400)).assemble(
        workspace_id='borealis', query='Q', retrieved_evidence=(long, _evidence('a', 2), _evidence('b', 3)))
    assert ids(bundle) == ['a', 'b']
    assert bundle.selection.selected_ids == ('a', 'b')


def test_atomic_conflict_and_chain_groups_report_all_incomplete_goals():
    groups = (EvidenceGroup('conflict', ('a', 'b'), ('supplier',), True),
              EvidenceGroup('chain', ('c', 'd'), ('owner',)))
    bundle = ContextAssembler(budget=ContextBudget(max_evidence=3)).assemble(
        workspace_id='borealis', query='Q', retrieved_evidence=tuple(_evidence(x, i + 1) for i, x in enumerate('abcd')),
        evidence_groups=groups, task_subgoals=('supplier', 'owner', 'unknown'))
    assert ids(bundle) == ['a', 'b']
    coverage = {c.subgoal_id: c for c in bundle.selection.coverage}
    assert coverage['supplier'].complete
    assert coverage['owner'].missing_evidence_ids == ('c', 'd')
    assert coverage['unknown'].reason == 'NO_CANDIDATES'


def test_filtered_group_member_cannot_leave_one_sided_conclusion():
    bundle = ContextAssembler().assemble(workspace_id='borealis', query='Q',
        retrieved_evidence=(_evidence('a', 1), _evidence('b', 2, workspace_id='other')),
        evidence_groups=(EvidenceGroup('both', ('a', 'b'), ('decision',), True),))
    assert ids(bundle) == []
    assert not bundle.selection.coverage[0].complete


def test_model_allocation_and_exact_counter_enforce_remaining_budget():
    policy = ContextPolicy(model_id='fake', context_window_tokens=500, output_reserve_tokens=50,
                           max_request_bytes=500, token_counter=len, token_count_kind='EXACT_FAKE_CHAR')
    bundle = ContextAssembler(policy=policy).assemble(workspace_id='borealis', query='Q',
        retrieved_evidence=(_evidence('a', 1),), request_usage=RequestUsage(tokens=420, bytes=460))
    assert bundle.estimated_tokens == len(bundle.rendered_text)
    assert bundle.budget_report.effective_tokens == 30
    assert bundle.budget_report.effective_bytes == 40
    assert ids(bundle) == []
    with pytest.raises(ValueError, match='allocation'):
        ContextAssembler(policy=policy).assemble(workspace_id='borealis', query='Q', request_usage=RequestUsage(tokens=500))


def test_chinese_and_candidate_scan_limits():
    assert estimate_tokens('中文测试') == 6
    bundle = ContextAssembler(policy=ContextPolicy(max_candidates=2)).assemble(
        workspace_id='borealis', query='Q', retrieved_evidence=tuple(_evidence(str(i), i) for i in range(1, 5)),
        evidence_groups=(EvidenceGroup('late', ('4',), ('late-goal',)),))
    assert ids(bundle) == ['1', '2']
    assert bundle.selection.omitted_candidates == 2
    assert not bundle.selection.coverage[0].complete


def test_utf8_byte_budget_and_atomic_budget_rejection_allow_later_group():
    long = replace(_evidence('a', 1), metadata={'text': '中文' * 100})
    bundle = ContextAssembler(policy=ContextPolicy(max_request_bytes=400)).assemble(
        workspace_id='borealis', query='Q', retrieved_evidence=(long, _evidence('b', 2), _evidence('c', 3)),
        evidence_groups=(EvidenceGroup('big', ('a', 'b'), ('g1',), True), EvidenceGroup('small', ('c',), ('g2',))))
    assert ids(bundle) == ['c']
    assert bundle.budget_report.used_bytes <= 400
    assert bundle.selection.coverage[0].missing_evidence_ids == ('a', 'b')


def test_overlapping_or_unbounded_hint_groups_are_rejected():
    assembler = ContextAssembler(policy=ContextPolicy(max_candidates=2))
    with pytest.raises(ValueError, match='disjoint'):
        assembler.assemble(workspace_id='borealis', query='Q', retrieved_evidence=(_evidence('a', 1),),
            evidence_groups=(EvidenceGroup('g1', ('a',)), EvidenceGroup('g2', ('a',))))
    with pytest.raises(ValueError, match='limit'):
        assembler.assemble(workspace_id='borealis', query='Q',
            evidence_groups=(EvidenceGroup('g', ('a', 'b', 'c')),))


@pytest.mark.parametrize('flag', ['active', 'current', 'authorized'])
@pytest.mark.parametrize('value', ['false', 'true', 0, 1, None, []])
def test_malformed_source_flags_cannot_expose_evidence(flag, value):
    candidate = replace(_evidence('bad', 1), metadata={'text': 'unsafe', flag: value})
    bundle = ContextAssembler().assemble(workspace_id='borealis', query='Q', retrieved_evidence=(candidate,))
    assert ids(bundle) == []
    assert bundle.dropped[0].reason.value == 'INVALID_PROVENANCE'


def test_nested_subgoal_hints_and_hint_strings_are_bounded():
    assembler = ContextAssembler(policy=ContextPolicy(max_candidates=1))
    with pytest.raises(ValueError, match='limit'):
        assembler.assemble(workspace_id='borealis', query='Q',
            evidence_groups=(EvidenceGroup('g', ('a',), ('goal1', 'goal2')),))
    with pytest.raises(ValueError, match='hint'):
        EvidenceGroup('g', ('a',), ('x' * 257,))


def test_candidate_input_tail_is_never_materialized_or_sorted():
    class RankedPool:
        def __len__(self):
            return 1_000_000

        def __iter__(self):
            raise AssertionError('must not iterate the entire upstream pool')

        def __getitem__(self, index):
            assert isinstance(index, slice) and index.start is None and index.stop == 2
            return (_evidence('first', 1), _evidence('second', 2))

    bundle = ContextAssembler(policy=ContextPolicy(max_candidates=2)).assemble(
        workspace_id='borealis', query='Q', retrieved_evidence=RankedPool())
    assert ids(bundle) == ['first', 'second']
    assert bundle.selection.inspected_candidates == 2
    assert bundle.selection.omitted_candidates == 999_998
    assert len(bundle.dropped) == 1
    assert bundle.dropped[0].reason.value == 'CANDIDATE_LIMIT'
