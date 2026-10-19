"""Frozen route contracts. Start spies measure admission, never model quality."""
import json
from pathlib import Path

from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.task_investigation import ProductTaskSources
from tests.m3i_support import product_case
from tests.integration.unified_ask_fixture import UnifiedAskFixture, ATLAS
from linkloom.ui.unified_ask import UnifiedAskService

CASES_PATH = Path(__file__).parent / 'fixtures' / 'm3m_admission.json'
CASES = json.loads(CASES_PATH.read_text(encoding='utf8'))['cases']


def context_for(app, workspace, domain):
    sources = app.snapshot_investigation_sources(workspace)[1]
    return dict(domain=domain, snapshot_hash=canonical_hash(dict(
        workspace_id=workspace, sources=ProductTaskSources._identity(sources))))


class StartSpy:
    def __init__(self, config):
        self.settings, self.calls = config, []

    def config(self):
        return dict(self.settings)

    def start(self, query, workspace_id, **kwargs):
        self.calls.append(dict(query=query, workspace_id=workspace_id, **kwargs))
        return dict(kind='running', run=dict(id='route-spy'), reviewer={})

    def start_temporal(self, query, workspace_id, **kwargs):
        return self.start(query, workspace_id, temporal_scope=kwargs)


def observe(ask, spy, app, workspace, case):
    spy.calls.clear()
    spy.settings = {**dict(available=True, external_provider=False, task_investigation=True,
                          adaptive_planning=True, canary_live=False), **case['config']}
    payload = {**dict(query=case['query'], workspace_id=workspace), **case['payload']}
    stale = payload.pop('stale_context', False)
    if case['domain']:
        payload['investigation_context'] = context_for(app, workspace, case['domain'])
        if stale:
            payload['investigation_context']['snapshot_hash'] = '0' * 64
    result, status = ask.ask(payload)
    code = (result.get('error') or {}).get('code')
    if spy.calls:
        route = ('TASK_INVESTIGATION' if spy.calls[0].get('task_investigation') else
                 'AGENT' if spy.calls[0].get('temporal_scope') else 'SOURCE_RELATION_INVESTIGATION')
    elif result['path'] == 'DETERMINISTIC' and result['status'] == 'COMPLETED':
        route = 'DETERMINISTIC'
    elif code in {'TASK_INVESTIGATION_UNAVAILABLE', 'INVESTIGATION_CAPABILITY_UNAVAILABLE',
                  'TASK_DATE_SCOPE_UNSUPPORTED'}:
        route = 'CAPABILITY'
    else:
        route = 'CLARIFY'
    return dict(id=case['id'], query=case['query'], domain=case['domain'], expected=case['expected'],
        observed=route, path=result['path'], status=result['status'], http_status=status,
        error=code, start_count=len(spy.calls),
        question_preserved=not spy.calls or spy.calls[0]['query'] == case['query'],
        classifier_model_calls=0, actual_model_calls=0,
        measurement='START_SPY_ONLY; stage/Runtime calls measured separately')


def matrix(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    app, service, _, _, workspace = product_case(root / 'tasks')
    decision = UnifiedAskFixture(root / 'decision.sqlite')
    try:
        decision.select_workspace(ATLAS)
        spy = StartSpy({})
        asks = dict(tasks=(UnifiedAskService(app, lambda: spy), app, workspace),
            decision=(UnifiedAskService(decision.app, lambda: spy), decision.app, decision.app._workspace_id()))
        return [observe(asks[c['profile']][0], spy, asks[c['profile']][1], asks[c['profile']][2], c)
                for c in CASES]
    finally:
        app.close()
        decision.stop()


def counts(rows):
    admitted = {'TASK_INVESTIGATION', 'SOURCE_RELATION_INVESTIGATION', 'AGENT', 'DETERMINISTIC'}
    return dict(cases=len(rows), false_rejection=sum(r['expected'] in admitted and r['observed'] not in admitted for r in rows),
        misroute=sum(r['observed'] in admitted and r['observed'] != r['expected'] for r in rows),
        fast_path_preserved=sum(r['observed'] == r['expected'] == 'DETERMINISTIC' for r in rows),
        capability_limits=sum(r['observed'] == 'CAPABILITY' for r in rows),
        reasonable_clarification=sum(r['observed'] == r['expected'] == 'CLARIFY' for r in rows),
        start_boundaries=sum(r['start_count'] for r in rows),
        unnecessary_start_boundaries=sum(r['start_count'] for r in rows if r['expected'] not in admitted),
        classifier_model_calls=0, actual_model_calls=0)
