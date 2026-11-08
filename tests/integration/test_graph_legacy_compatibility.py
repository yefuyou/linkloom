"""Offline mixed-Ask contracts; frozen inputs are only read, never rewritten.

Adapters control planning, not evidence. SQLite projection, traversal, Runtime
legacy reads and both receipt reviewers execute normally; no paid client exists.
"""
from copy import deepcopy
import json
from pathlib import Path
from time import monotonic, sleep
from dataclasses import replace

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.temporal_graph.models import digest
from linkloom.temporal_graph.planning import GraphQuestionPlanner
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.ui.graph_investigation import GraphInvestigationService
from linkloom.ui.unified_ask import UnifiedAskService
from tests.graph_eval_support import confirm_candidates
from tests.unit.test_temporal_graph_planning import Model
from tests.unit.test_temporal_graph_projection import fixture_public
from run_bounded_multihop import SourceOnlyController
from unified_ask_fixture import UnifiedAskFixture
from unified_investigation_fixture import TWO, THREE


CASES = {
    c['id']: c for c in json.loads(Path('tests/fixtures/bounded_multihop/dev.json').read_text(encoding='utf8'))['cases']
    if c['id'] in {'DEV-A', 'DEV-B', 'DEV-C', 'DEV-H', 'DEV-I'}
}
# The two original HTTP positive sources are copied verbatim from their fixture.
CASES['HTTP-TWO'] = dict(query=TWO, workspace_id='original', documents=[
    dict(path='owner.md', content='[2026-10-06T10:00:00Z] Synthetic: 林舟 is the accountable owner of Orion.'),
    dict(path='leader.md', content='[2026-10-06T10:00:00Z] Synthetic: 林舟 reports directly to 陈宁.')])
CASES['HTTP-THREE'] = dict(query=THREE, workspace_id='original', documents=[
    dict(path='alias.md', content='[2026-10-06T10:00:00Z] Synthetic: Copper Meadow is the team alias for Falcon Reach.'),
    dict(path='owner.md', content='[2026-10-06T10:00:00Z] Synthetic: Ada Vale is the accountable owner of Falcon Reach.'),
    dict(path='leader.md', content='[2026-10-06T10:00:00Z] Synthetic: Ada Vale reports directly to Owen Brook.')])
ROOTS = {key: ('Comet-Key', 'Glacier Bay', 'alias') if key in {'DEV-B', 'DEV-C'} else
         ('Copper Meadow', 'Falcon Reach', 'alias') if key == 'HTTP-THREE' else
         ('Orion', 'Orion', 'project') if key == 'HTTP-TWO' else
         ('Glacier Bay', 'Glacier Bay', 'project') for key in CASES}


class ControlledLocator:
    """Only tests Host ambiguity; it does not predict real local-model rankings."""
    def __init__(self, missing=False, blocked=False):
        self.missing, self.blocked = missing, blocked
    def locate(self, snapshot, text, kind, when, mode):
        if self.blocked: raise ValueError('LOCAL_RETRIEVAL_UNAVAILABLE')
        nodes = [n for n in snapshot.nodes if n.kind == kind]
        return dict(status='NOT_FOUND' if self.missing else 'AMBIGUOUS',
                    node_ids=[] if self.missing else [n.node_id for n in nodes],
                    reason='NO_CONFIRMED_ENTITY_ABOVE_THRESHOLD' if self.missing else 'MULTIPLE_CANDIDATES',
                    catalogue_sha256=digest([n.to_dict() for n in snapshot.nodes]))


class Harness:
    def __init__(self, root, key, *, ready=True):
        self.demo = UnifiedAskFixture(root / 'app.sqlite')
        self.case = deepcopy(CASES[key]); self.key = key
        self.mention, self.project, self.kind = ROOTS[key]
        documents = [dict(d, workspace_id='original') for d in self.case['documents']]
        candidates = []
        if ready:
            # Independent regression context, not a benchmark fixture change.
            # Approved aliases make the graph ready but confer NO owner evidence.
            first_alias = self.mention if self.kind == 'alias' else 'Compat reference'
            for i, (alias, project) in enumerate([(first_alias, self.project), ('Compat alternate', 'Compat alternate project')]):
                name = f'graph-anchor-{i}.md'
                documents.append(dict(workspace_id='original', path=name,
                    content=f'As of 2026-10-01 {alias} is the team alias for {project}.'))
                candidates.append(dict(case_id=key, workspace_id='original', source_path=name, candidate_id=f'anchor-{i}',
                    original_candidate=dict(relation='alias_of', subject=alias.casefold(), value=project.casefold(),
                        valid_on='2026-10-01', temporal_kind='event', project_scope=None)))
        public = dict(split='dev', cases=[dict(case_id=key, workspace_id='original', documents=documents)], candidates=candidates)
        confirmed = confirm_candidates(public, self.demo.app.repository.database_path)
        assert all(item['materialized'] for item in confirmed['confirmed']), confirmed['confirmed']
        self.workspace = confirmed['workspaces'][(key, 'original')]
        self.demo.app.repository.select_workspace(self.workspace)
        self.service = self.demo.configure_agent()
        self.legacy_models = []
        def factory(query):
            controller = SourceOnlyController()
            def respond(request):
                action = controller(request)
                if action.kind == 'tool_call' and action.tool_call.tool_id == 'search_notes':
                    call = action.tool_call
                    action = ModelAction.tool(replace(call, arguments=dict(call.arguments,
                        limit=4 if call.arguments['source_context']['subgoal_id'] == 'leader' else 2)))
                return action
            model = FakeModelAdapter([respond] * 12)
            self.legacy_models.append(model)
            return model
        self.service.model_factory = factory
        self.models = []

    def close(self): self.demo.stop()

    def store(self, locator=None):
        def authorized(ws, source, version):
            return self.demo.app._workspace_id() == ws and any(
                s['source_id'] == source and s['source_version'] == version and s['active']
                for s in self.demo.app.repository.list_sources(ws))
        return SQLiteGraphStore(self.demo.app.repository.database_path, authorized_workspace=self.workspace,
                                authorize_source=authorized, mention_locator=locator)

    def worker(self, mode):
        locator = ControlledLocator(missing=mode == 'not_found', blocked=mode == 'blocked') if mode in {'ambiguous', 'not_found', 'blocked'} else None
        store = self.store(locator)
        def factory(context):
            if mode == 'no_planner': return None
            if mode == 'unauthorized': raise PermissionError('CURRENT_WORKSPACE_CHANGED')
            root = next(n for n in context.nodes if n.kind == 'project' and n.label.casefold() == self.project.casefold())
            raw = dict(applicable=True, as_of='', refusal_reasons=[], steps=[
                dict(id='owner', input_node=root.node_id, input_step='', edge_type='owner_of', direction='in', as_of=''),
                dict(id='leader', input_node='', input_step='owner', edge_type='reports_to', direction='out', as_of='')],
                outputs=[dict(step='leader', field='node')])
            if mode == 'partial':
                alias = next(n for n in context.nodes if n.kind == 'alias' and n.label == 'compat reference')
                raw['steps'] = [dict(id='project', input_node=alias.node_id, input_step='',
                    edge_type='alias_of', direction='out', as_of=''),
                    dict(raw['steps'][0], input_node='', input_step='project'), raw['steps'][1]]
                raw['outputs'] = [dict(step='project', field='node'), dict(step='leader', field='node')]
            mentions = mode in {'ambiguous', 'not_found', 'blocked'}
            if mentions:
                steps = []
                parent = dict(mention='root')
                if self.kind == 'alias':
                    steps.append(dict(id='project', operation='traverse', input=parent, as_of='',
                        edge_type='alias_of', direction='out', cardinality='single', selection='all'))
                    parent = dict(step='project')
                steps.extend([dict(id='owner', operation='traverse', input=parent, as_of='',
                    edge_type='owner_of', direction='in', cardinality='single', selection='all'),
                    dict(id='leader', operation='traverse', input=dict(step='owner'), as_of='',
                    edge_type='reports_to', direction='out', cardinality='single', selection='all')])
                raw.update(intent=dict(mode='lookup', result_kinds=['person']),
                    mentions=[dict(id='root', mention=self.mention, kind=self.kind, reference='direct')], steps=steps)
            if mode == 'refused': raw.update(applicable=False, refusal_reasons=['UNSUPPORTED_GRAPH_CAPABILITY'], steps=[], outputs=[])
            if mode == 'fallback': raw = {}
            model = Model(raw); self.models.append(model)
            return GraphQuestionPlanner(model, model_id='fake', guard_id='compat', mention_grounding=mentions,
                request_guard=lambda request: ProviderRequestAuthorization.approve(request,
                    guard_id='compat', reservation_id='offline', estimated_cost_upper_bound_usd=0))
        return GraphInvestigationService(lambda workspace: store, factory)

    def ask_service(self, worker):
        return UnifiedAskService(self.demo.app, lambda: self.service, graph_service=worker, slot_embedder=None)

    def payload(self): return dict(query=self.case['query'], workspace_id=self.workspace)


def finish(ask, payload):
    result, status = ask.ask(payload)
    deadline = monotonic() + 90
    while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
        result, status = ask.inspect(result['agent_run_id'], payload['workspace_id'])
        sleep(.01)
    assert result['status'] != 'INVESTIGATING'
    return result, status


def evidence(result):
    return {(e['source_name'], e['source_id'], e['source_version'], e['line_start'], e['line_end'],
             e['quote'], e.get('content_sha256'), e.get('quote_sha256')) for e in result['evidence']}


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('LINKLOOM_GRAPH_QUERY', 'on')
    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE', 'bm25')
    monkeypatch.setenv('LINKLOOM_RERANK', 'off')


@pytest.fixture
def legacy_spy(monkeypatch):
    import linkloom.ui.unified_ask as module
    original = module.plan_relation_investigation
    calls = []
    def spy(question): calls.append(question); return original(question)
    monkeypatch.setattr(module, 'plan_relation_investigation', spy)
    return calls


@pytest.mark.parametrize('key', list(CASES))
def test_seven_old_positives_keep_answer_and_evidence_off_on(tmp_path, monkeypatch, key, legacy_spy):
    h = Harness(tmp_path, key)
    try:
        monkeypatch.setenv('LINKLOOM_GRAPH_QUERY', 'off')
        baseline, status = finish(h.ask_service(False), h.payload())
        assert status == 200 and baseline['status'] == 'COMPLETED', baseline
        assert baseline['source_investigation']['answer'] and baseline['reviewer']['accepted']
        monkeypatch.setenv('LINKLOOM_GRAPH_QUERY', 'on')
        for mode, expected in [('unknown', 'UNKNOWN'), ('ambiguous', 'AMBIGUOUS')]:
            worker = h.worker(mode); ask = h.ask_service(worker); before = len(legacy_spy)
            actual, status = finish(ask, h.payload())
            assert status == 200 and actual['status'] == 'COMPLETED', actual
            assert actual['source_investigation']['answer'] == baseline['source_investigation']['answer']
            assert evidence(actual) == evidence(baseline)
            assert all(e['verified'] for e in actual['evidence']) and actual['reviewer']['accepted']
            assert actual.get('answer_source') == actual['decided_by'] == 'legacy', (mode, actual, worker.metrics.snapshot())
            assert actual['graph_fallback']['graph_status'] == expected
            assert actual['graph_fallback']['reason'] == 'GRAPH_INSUFFICIENT_' + expected
            assert len(legacy_spy) == before + 1
            counts = worker.metrics.snapshot()
            assert next(r for r in counts if r['name'] == 'graph_query_status')['reason'] == expected
            assert next(r for r in counts if r['name'] == 'legacy_fallback')['count'] == 1
            reopened, _ = ask.inspect(actual['agent_run_id'], h.workspace)
            assert reopened['answer_source'] == 'legacy' and evidence(reopened) == evidence(actual)
            assert len(legacy_spy) == before + 1  # Poll/reopen never starts another attempt.
    finally: h.close()


@pytest.mark.parametrize('mode', ['fallback', 'no_planner', 'not_ready'])
def test_explicit_unavailable_or_invalid_planner_uses_old_path(tmp_path, mode, legacy_spy):
    h = Harness(tmp_path, 'DEV-A', ready=mode != 'not_ready')
    try:
        worker = h.worker(mode); actual, status = finish(h.ask_service(worker), h.payload())
        assert status == 200 and actual['status'] == 'COMPLETED', actual
        assert len(legacy_spy) == 1 and actual['reviewer']['accepted'] and actual['evidence']
        reason = dict(fallback='GRAPH_PLANNING_OUTPUT_INVALID', no_planner='GRAPH_PLANNER_NOT_CONFIGURED', not_ready='GRAPH_NOT_READY')[mode]
        assert next(r for r in worker.metrics.snapshot() if r['name'] == 'legacy_fallback')['reason'] == reason
    finally: h.close()


@pytest.mark.parametrize('mode,code', [('refused', 'GRAPH_PLANNER_REFUSED'),
    ('blocked', 'GRAPH_TOOL_FAILED'), ('unauthorized', 'WORKSPACE_OR_SOURCE_PERMISSION_CHANGED')])
def test_terminal_graph_status_never_enters_old_planner(tmp_path, mode, code, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        actual, status = finish(h.ask_service(h.worker(mode)), h.payload())
        assert status == (403 if mode == 'unauthorized' else 200)
        assert actual['error']['code'] == code, actual
        assert legacy_spy == [] and h.legacy_models == [] and not actual['evidence']
    finally: h.close()


@pytest.mark.parametrize('mode,expected', [('unknown', 'UNKNOWN'), ('ambiguous', 'AMBIGUOUS'), ('not_found', 'NOT_FOUND')])
def test_unanswered_legacy_restores_revalidated_original_graph(tmp_path, mode, expected, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        h.service.model_factory = lambda query: FakeModelAdapter([ModelAction.final('invalid legacy final')])
        worker = h.worker(mode)
        original_run = worker.run; originals = []
        def capture(*args, **kwargs):
            result = original_run(*args, **kwargs); originals.append(deepcopy(result)); return result
        worker.run = capture
        ask = h.ask_service(worker)
        actual, status = finish(ask, h.payload())
        assert status == 200 and actual['status'] == 'INSUFFICIENT', actual
        assert actual['graph_status'] == expected and actual['decided_by'] == 'graph'
        assert actual['graph_fallback']['legacy_status'] == 'FAILED'
        # AMBIGUOUS carries a clarification string, UNKNOWN has no answer.
        # Neither may be replaced by the failed legacy response.
        assert actual['source_investigation'] == originals[0]['source_investigation']
        assert actual['evidence'] == originals[0]['evidence'] and len(legacy_spy) == 1
        reopened, _ = ask.inspect(actual['agent_run_id'], h.workspace)
        assert reopened['graph_status'] == expected and len(legacy_spy) == 1
    finally: h.close()


def test_not_found_keeps_old_answer_and_evidence_off_on(tmp_path, monkeypatch, legacy_spy):
    # Authorized policy change: missing confirmed roots are capability failures,
    # so NOT_FOUND now gets the same verified legacy attempt as UNKNOWN.
    h = Harness(tmp_path, 'DEV-A')
    try:
        monkeypatch.setenv('LINKLOOM_GRAPH_QUERY', 'off')
        baseline, status = finish(h.ask_service(False), h.payload())
        assert status == 200 and baseline['status'] == 'COMPLETED', baseline
        assert baseline['source_investigation']['answer'] and baseline['reviewer']['accepted']
        monkeypatch.setenv('LINKLOOM_GRAPH_QUERY', 'on')
        worker = h.worker('not_found'); ask = h.ask_service(worker); before = len(legacy_spy)
        actual, status = finish(ask, h.payload())
        assert status == 200 and actual['status'] == 'COMPLETED', actual
        assert actual['source_investigation']['answer'] == baseline['source_investigation']['answer']
        assert evidence(actual) == evidence(baseline) and all(e['verified'] for e in actual['evidence'])
        assert actual['reviewer']['accepted'] and actual['answer_source'] == actual['decided_by'] == 'legacy'
        assert actual['graph_fallback']['graph_status'] == 'NOT_FOUND'
        assert actual['graph_fallback']['reason'] == 'GRAPH_INSUFFICIENT_NOT_FOUND'
        assert len(legacy_spy) == before + 1
        counters = worker.metrics.snapshot()
        assert next(r for r in counters if r['name'] == 'graph_query_status')['reason'] == 'NOT_FOUND'
        assert next(r for r in counters if r['name'] == 'legacy_fallback')['reason'] == 'GRAPH_INSUFFICIENT_NOT_FOUND'
        reopened, _ = ask.inspect(actual['agent_run_id'], h.workspace)
        assert reopened['answer_source'] == 'legacy' and evidence(reopened) == evidence(actual)
        assert len(legacy_spy) == before + 1
    finally: h.close()


def test_partial_graph_answer_does_not_try_legacy(tmp_path, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        worker = h.worker('partial')
        actual, status = finish(h.ask_service(worker), h.payload())
        assert status == 200 and actual['status'] == 'INSUFFICIENT', actual
        assert actual['graph_status'] == 'PARTIAL' and actual['decided_by'] == 'graph'
        assert actual['conclusions'] and actual['evidence'] and actual['reviewer']['accepted']
        assert all(e['verified'] for e in actual['evidence'])
        assert 'graph_fallback' not in actual and 'answer_source' not in actual
        assert legacy_spy == [] and h.legacy_models == []
        assert not any(r['name'] == 'legacy_fallback' for r in worker.metrics.snapshot())
    finally: h.close()


def test_confirmed_candidate_clarification_reaches_ask_without_legacy(tmp_path, legacy_spy, monkeypatch):
    h = Harness(tmp_path, 'DEV-A')
    try:
        worker = h.worker('ambiguous')
        clarification = dict(status='INSUFFICIENT', graph_status='AMBIGUOUS', run_id='clarification',
            source_investigation=dict(answer='Which inventory audit do you mean?', graph_query=dict(
                steps=[dict(reason_code='CONFIRMED_CANDIDATES_NEED_CLARIFICATION')])))
        monkeypatch.setattr(worker, 'run', lambda *args, **kwargs: clarification)
        actual, status = finish(h.ask_service(worker), h.payload())
        assert status == 200 and actual['graph_status'] == 'AMBIGUOUS'
        assert actual['source_investigation'] == clarification['source_investigation']
        assert not legacy_spy and not h.legacy_models
    finally: h.close()


def dated_graph(h):
    public = fixture_public()
    confirmed = confirm_candidates(public, h.demo.app.repository.database_path)
    workspace = confirmed['workspaces'][('DEV', 'original')]
    h.demo.app.repository.select_workspace(workspace)
    store = SQLiteGraphStore(h.demo.app.repository.database_path, authorized_workspace=workspace,
        authorize_source=lambda ws, source, version: h.demo.app._workspace_id() == ws and any(
            s['source_id'] == source and s['source_version'] == version and s['active']
            for s in h.demo.app.repository.list_sources(ws)))
    def factory(context):
        project = next(n for n in context.nodes if n.kind == 'project')
        model = Model(dict(applicable=True, as_of='', refusal_reasons=[], steps=[
            dict(id='owner', input_node=project.node_id, input_step='', edge_type='owner_of', direction='in', as_of=''),
            dict(id='leader', input_node='', input_step='owner', edge_type='reports_to', direction='out', as_of='')],
            outputs=[dict(step='leader', field='node')]))
        h.models.append(model)
        return GraphQuestionPlanner(model, model_id='fake', guard_id='dated',
            request_guard=lambda r: ProviderRequestAuthorization.approve(r, guard_id='dated',
                reservation_id='offline', estimated_cost_upper_bound_usd=0))
    return workspace, store, GraphInvestigationService(lambda ws: store, factory)


def test_found_graph_keeps_verified_answer_without_old_planner(tmp_path, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        ws, store, worker = dated_graph(h)
        actual, status = finish(h.ask_service(worker), dict(query='Who is the direct leader of the owner of Bay?',
            workspace_id=ws, as_of='2026-04-01'))
        assert status == 200 and actual['status'] == 'COMPLETED'
        assert actual['graph_status'] == 'FOUND' and actual['decided_by'] == 'graph'
        assert actual['source_investigation']['answer'] == 'bo'
        assert len(actual['evidence']) == 2 and all(e['verified'] for e in actual['evidence'])
        assert legacy_spy == [] and h.legacy_models == []
    finally: h.close()


def test_time_gate_is_preserved_after_legacy_attempt(tmp_path, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        ws, store, worker = dated_graph(h)
        actual, status = finish(h.ask_service(worker), dict(query='Who is the direct leader of the owner of Bay?',
            workspace_id=ws, as_of='2026-05-01'))
        assert status == 200 and actual['status'] == 'INSUFFICIENT', actual
        assert actual['graph_status'] == 'UNKNOWN' and actual['decided_by'] == 'graph'
        assert actual['source_investigation']['answer'] is None and actual['conclusions'] == []
        assert len(legacy_spy) >= 1 and actual['graph_fallback']['graph_status'] == 'UNKNOWN'
    finally: h.close()


def test_workspace_gate_precedes_both_planners(tmp_path, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        worker = h.worker('unknown'); ask = h.ask_service(worker)
        actual, status = ask.ask(dict(h.payload(), workspace_id='foreign'))
        assert status == 409 and actual['error']['code'] == 'WORKSPACE_MISMATCH'
        assert legacy_spy == [] and h.models == [] and h.legacy_models == []
        assert actual['evidence'] == []
    finally: h.close()


def test_revoking_legacy_evidence_on_reopen_never_replays_old_answer(tmp_path, legacy_spy):
    h = Harness(tmp_path, 'DEV-A')
    try:
        ask = h.ask_service(h.worker('unknown'))
        actual, _ = finish(ask, h.payload())
        assert actual['answer_source'] == 'legacy'
        with h.demo.app.repository._connection:
            h.demo.app.repository._connection.execute(
                "UPDATE ll_source SET active=0 WHERE workspace_id=? AND name='n1.md'", (h.workspace,))
        restored, status = ask.inspect(actual['agent_run_id'], h.workspace)
        assert restored['status'] != 'COMPLETED'
        assert not restored.get('source_investigation', {}).get('answer')
        assert restored['evidence'] == [] and len(legacy_spy) == 1
    finally: h.close()


def test_graph_and_legacy_share_one_provider_reservation(tmp_path, legacy_spy):
    from linkloom.ui.graph_investigation import _GRAPH_PROVIDER
    from linkloom.ui.task_provider import TaskProviderSession, TaskProviderLimits
    h = Harness(tmp_path, 'DEV-A')
    try:
        limits = TaskProviderLimits(1, 100000, 10000, 1, 30, 5, .001)
        session = TaskProviderSession(tmp_path / 'shared', limits, lambda: None)
        h.service._provider_sessions.append(session)
        worker = h.worker('unknown'); original = worker.planner_factory
        def factory(context):
            planner = original(context)
            _GRAPH_PROVIDER.set(session)
            planner.session.adapter = session.bind(planner.session.adapter, 'graph-planning')
            return planner
        worker.planner_factory = factory
        actual, status = finish(h.ask_service(worker), h.payload())
        assert status == 200 and actual['graph_status'] == 'UNKNOWN'
        assert actual['status'] == 'INSUFFICIENT' and actual['decided_by'] == 'graph'
        assert session.requests == 1 and session.failure == 'TASK_PROVIDER_BUDGET_EXHAUSTED'
        assert len(legacy_spy) == 1 and all(m.call_count == 0 for m in h.legacy_models)
    finally: h.close()
