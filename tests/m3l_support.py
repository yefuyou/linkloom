"""Scripted SDK seam for offline wiring only, never a semantic evaluation."""
import json
import re
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from time import monotonic, sleep

from linkloom.ui.canary_spec import load_spec
from linkloom.ui.canary import CanaryHost
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.unified_ask import UnifiedAskService
from tests.unit.test_task_observation_discovery import claim


def finish(ask, workspace, result):
    # Local disk/fsync contention is not model latency. Stay inside the frozen
    # 600s case budget while allowing offline Checkpoint/Reviewer I/O to finish.
    deadline = monotonic() + 60
    while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
        sleep(.04)
        result, status = ask.inspect(result['agent_run_id'], workspace)
        assert status == 200, result
    return result


class OfflineSDK:
    def __init__(self, spec, fault=None):
        self.spec, self.fault, self.calls = spec, fault, []
        self.models = self
        self.receipts = {}

    def generate_content(self, **payload):
        self.calls.append(payload)
        if self.fault == 'timeout':
            raise TimeoutError('offline injected timeout')
        texts = '\n'.join(p.get('text', '') for c in payload['contents'] for p in c['parts'])
        declarations = payload['config']['tools'][0]['function_declarations']
        names = [d['name'] for d in declarations]
        name = names[0]
        if name == 'emit_semantic_candidates':
            text = json.loads(texts.rsplit('Input:\n', 1)[1])['source_segment']['text']
            args = dict(claims=[claim(text, '日志迁移')] if '日志迁移' in text else [])
        elif name == 'emit_investigation_plan':
            data = json.loads(texts.split('INPUT_JSON\n', 1)[1])
            handles = [e['handle'] for e in data['endpoints']][:2]
            base = dict(id='identity', kind='relation', question='这些候选是否延续同一任务？',
                handles=handles, source_handles=[], depends_on=[], predicate='SAME_TASK',
                field='', target_kind='', missing_entities=[])
            args = dict(steps=[base, dict(base, id='revision', predicate='REVISES_PLAN',
                handles=list(reversed(handles)), depends_on=['identity'])], uncertainties=[], refusal_reasons=[])
            if self.fault == 'schema':
                args = {'steps': 'invalid'}
        elif name == 'propose_task_relations':
            data = json.loads(texts.split('AUTHORIZED_SNAPSHOT_JSON\n', 1)[1])
            evidence = [dict(span_id=e['span_id'], quote=e['source']['quote']) for e in data['allowed_evidence']]
            if self.fault == 'citation':
                evidence[0]['span_id'] = 'not-a-real-span'
            args = dict(proposals=[dict(predicate=q['predicate'], subject=q['subject'], object=q['object'],
                proposed_verdict='UNKNOWN' if len(data['allowed_evidence']) > 2 else 'SUPPORTED',
                supporting_evidence=evidence, counter_evidence=[],
                missing_evidence=['任务归属和有效截止存在冲突，尚未确认。'] if len(data['allowed_evidence']) > 2 else [],
                reason_codes=['OFFLINE_SCRIPT_ONLY'], changed_fields=[]) for q in data['queries']])
        else:
            # Scripted outer calls are explicitly a protocol fixture, not planning.
            n = sum(1 for c in self.calls if c['config']['tools'][0]['function_declarations'][0]['name']
                    not in {'emit_semantic_candidates', 'emit_investigation_plan', 'propose_task_relations'})
            if n > 1:
                previous = re.search(r'"last_tool":\{"call_id":"(rtc_[0-9a-f]+)"', texts)
                self.receipts[['context_call_id', 'relations_call_id', 'semantic_call_id'][n - 2]] = previous.group(1)
            if n <= 3:
                name = ['inspect_task_context', 'query_task_relations', 'analyze_task_semantics'][n - 1]
                definition = next(d for d in declarations if d['name'] == name)
                schema = definition.get('parameters_json_schema') or definition.get('parameters')
                args = {'workspace_id': schema['properties']['workspace_id']['enum'][0]}
                if name == 'analyze_task_semantics':
                    args['investigation_id'] = 'product-investigation'
            else:
                part = {'text': json.dumps(self.receipts)}
                return self.response(part)
        part = {'function_call': {'name': name, 'args': args,
                'id': 'outer-' + str(n) if name in {'inspect_task_context', 'query_task_relations', 'analyze_task_semantics'} else 'stage-call'}}
        return self.response(part)

    def response(self, part):
        raw = dict(candidates=[dict(content=dict(role='model', parts=[part]), finish_reason='STOP')],
            model_version=self.spec.model_id, response_id='offline-response',
            usage_metadata=dict(prompt_token_count=20, candidates_token_count=10,
                                thoughts_token_count=2, total_token_count=32, extra_counter='retained'))
        if self.fault == 'usage':
            raw.pop('usage_metadata')
        if self.fault == 'usage_shape':
            raw['usage_metadata']['unserializable_field'] = object()
        if self.fault == 'stage_output':
            raw['usage_metadata'].update(candidates_token_count=1600, total_token_count=1622)
        return raw


def offline_product(root, monkeypatch, *, fault=None):
    from linkloom.ui import canary
    spec = load_spec()
    sdk = OfflineSDK(spec, fault)
    construction = []
    def build(*args, **kwargs):
        construction.append(kwargs)
        return sdk
    monkeypatch.setattr(canary, '_campaign_root', lambda: root)
    monkeypatch.setattr(canary, 'build_gemini_sdk_client', build)
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-placeholder')
    grant = dict(approved=True, campaign_id=spec.campaign_id, manifest_sha256=spec.digest,
        provider_id='gemini', model_id=spec.model_id, max_requests=34, max_cost_usd=3,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    host = CanaryHost.open_live(enabled=True, grant=grant)
    app = host.product_application()
    service = ProductInvestigationService(app, canary_host=host, relation_max_steps=6,
                                         run_mode='OFFLINE_SDK_SIMULATION')
    ask = UnifiedAskService(app, lambda: service)
    return SimpleNamespace(host=host, app=app, service=service, ask=ask, sdk=sdk,
                           construction=construction, spec=spec, grant=grant)
