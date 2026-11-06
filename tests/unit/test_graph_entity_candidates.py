"""Synthetic offline examples of the confirmed candidate and clarification path."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest

from linkloom.temporal_graph.entity_locator import LocalMentionLocator
from linkloom.temporal_graph.mention_planning import compile_plan
from linkloom.temporal_graph.models import GraphSnapshot
from linkloom.ui.graph_investigation import GraphInvestigationService
from tests.unit.test_graph_mentions import mention, raw
from tests.unit.test_temporal_graph_query import edge, evidence, node


class RankedLocator(LocalMentionLocator):
    def __init__(self, ranking):
        super().__init__()
        self.ranking = ranking

    def rank(self, snapshot, text, kind, when, temporal_mode='current'):
        return dict(coverage='complete', candidates=self.ranking)


def task_record(task, owner, start='2028-04-06'):
    state = dict(valid_from=start, valid_to=None, status='open', evidence=[evidence().to_dict()],
                 fields=dict(title=task.label, owner=owner))
    return dict(node_id=task.node_id, kind='task', title=task.label, task_id=task.key, states=[state])


def scored(task, score, similarity=.6):
    return dict(node_id=task.node_id, score=score, identity_similarity=similarity, identity=task.label)


def service(nodes, records, ranking, *, edges=(), pending=False, as_of=None, term='Inventory'):
    class Store:
        mention_locator = RankedLocator(ranking)

        def load_edges(self, workspace, *args):
            return GraphSnapshot(workspace, tuple(edges), (), tuple(nodes), tuple(records))

        def unconfirmed_context(self, workspace, text):
            return dict(count=int(pending), evidence=[], candidate_refs=[])

    class Planner:
        def plan(self, context):
            request = raw([mention(term, 'task')],
                          [dict(id='read', operation='inspect', input={'mention': 'm'}, as_of='')],
                          [dict(step='read', field='owner')], 'lookup', ['task'])
            return dict(status='PLANNED', plan=compile_plan(request, context))

    store = Store()
    worker = GraphInvestigationService(lambda ws: store, lambda ctx: Planner(),
                                       query_clock=lambda: datetime(2028, 5, 1, tzinfo=UTC))
    return worker, lambda question: worker.run(question, 'w', as_of=as_of)


@pytest.mark.parametrize('qualifier', ['', ' for Morgan', ' from 2028/4/6', ' from 2027/4/6'])
def test_single_weak_candidate_requires_independent_literal_evidence(qualifier):
    task = node('task', 'Inventory audit')
    worker, ask = service([task], [task_record(task, 'Morgan')], [scored(task, 1.4)])
    result = ask('Who owns Inventory' + qualifier + '?')
    answer = result['source_investigation']
    assert result['reviewer']['accepted'] and worker.inspect(result['run_id'], 'w') == result
    if qualifier in {' for Morgan', ' from 2028/4/6'}:
        assert result['graph_status'] == 'FOUND' and '负责人：Morgan' in answer['answer']
    else:
        assert result['graph_status'] == 'AMBIGUOUS' and 'Inventory audit' in answer['answer']
        assert answer['facts'] == [] and answer['structured_outputs'] == []
        assert answer['graph_query']['steps'][-1]['status'] == 'BLOCKED'


def test_multiple_near_candidates_are_not_narrowed_by_one_owner_hint():
    one, two = node('task', 'Inventory audit'), node('task', 'Stock check')
    _, ask = service([one, two], [task_record(one, 'Morgan'), task_record(two, 'Taylor')],
                     [scored(one, 1.4), scored(two, -.4, .36)])
    result = ask('Who owns Inventory for Morgan?')
    view = result['source_investigation']
    assert result['graph_status'] == 'AMBIGUOUS'
    assert all(name in view['answer'] for name in (one.label, two.label))
    assert not view['facts'] and not view['structured_outputs']


def test_low_score_candidate_is_listed_without_being_selected():
    one, two = node('task', 'Stock review'), node('task', 'Cycle count')
    _, ask = service([one, two], [task_record(one, 'Morgan'), task_record(two, 'Taylor')],
                     [scored(one, 1.0), scored(two, -2.6, .35)], term='Warehouse')
    result = ask('Who owns Warehouse?')
    view = result['source_investigation']
    assert result['graph_status'] == 'AMBIGUOUS'
    assert all(name in view['answer'] for name in (one.label, two.label))
    assert not view['facts'] and not view['structured_outputs']


def test_strong_match_keeps_original_behavior_even_with_a_weak_competitor():
    one, two = node('task', 'Inventory audit'), node('task', 'Stock check')
    _, ask = service([one, two], [task_record(one, 'Morgan'), task_record(two, 'Taylor')],
                     [scored(one, 4), scored(two, 1.4)])
    result = ask('Who owns Inventory?')
    assert result['graph_status'] == 'FOUND'
    assert result['source_investigation']['answer'].startswith('负责人：Morgan')


def test_partial_title_requires_word_boundary_and_never_overrides_pending_proposal():
    task = node('task', 'Inventory audit')
    data = [task_record(task, 'Morgan')]
    _, ask = service([task], data, [scored(task, -5, .2)])
    assert ask('Who owns Inventory?')['graph_status'] == 'AMBIGUOUS'
    _, ask = service([task], data, [scored(task, -5, .2)], term='Invent')
    assert ask('Who owns Invent?')['graph_status'] == 'NOT_FOUND'
    _, ask = service([task], data, [scored(task, 1.2)], pending=True)
    assert ask('Who owns Inventory?')['graph_status'] == 'NOT_FOUND'


def test_alias_corroboration_is_time_scoped_and_keeps_its_evidence():
    task, alias = node('task', 'Inventory audit'), node('alias', 'Bluebird')
    alias_edge = edge(alias, task, 'alias_of', start='2028-04-06', end='2028-04-12')
    arguments = ([task, alias], [task_record(task, 'Morgan')], [scored(task, 1.4)])
    _, ask = service(*arguments, edges=[alias_edge], as_of='2028-04-10')
    result = ask('Who owns Inventory for Bluebird?')
    root = result['source_investigation']['graph_query']['steps'][0]
    assert result['graph_status'] == 'FOUND' and root['root_resolution']['witness_evidence']
    _, ask = service(*arguments, edges=[alias_edge], as_of='2028-04-20')
    assert ask('Who owns Inventory for Bluebird?')['graph_status'] == 'AMBIGUOUS'


def test_future_or_unproven_candidate_cannot_be_adopted():
    task = node('task', 'Inventory audit')
    future = task_record(task, 'Morgan', start='2029-04-06')
    unproven = deepcopy(task_record(task, 'Morgan'))
    unproven['states'][0]['evidence'] = []
    for record in (future, unproven):
        _, ask = service([task], [record], [scored(task, 1.4)])
        assert ask('Who owns Inventory for Morgan?')['graph_status'] == 'NOT_FOUND'
