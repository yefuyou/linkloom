"""One frozen F-profile A/B experiment, not a general evaluation framework.

Labels are read only by score(). Offline execution has no SDK construction path.
The LIVE ledger is separate from M3-L and lives at one manifest-bound location.
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import sqlite3

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor, ProviderRequestAuthorization, TaskDiscoveryProfile,
)
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService
from linkloom.tools.contracts import ToolCall

ROOT = Path(__file__).resolve().parents[3]
FREEZE = ROOT / 'docs/requirements/m3-o-quality-convergence/ab-v3'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def save(path, value):
    with Path(path).open('x', encoding='utf8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read_frozen(name, manifest):
    data = (FREEZE/name).read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest['files'][name]:
        raise ValueError('FREEZE_HASH_MISMATCH:' + name)
    return json.loads(data.decode('utf8'))


def load_freeze():
    manifest = read(FREEZE / 'freeze_manifest.json')
    # Execution never opens scoring-only files, even for a checksum. Their
    # hashes remain bound by the manifest/grant; score() verifies their bytes.
    for name, sha in manifest['code_files'].items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest() != sha:
            raise ValueError('FROZEN_IMPLEMENTATION_CHANGED:' + name)
    if hashlib.sha256((ROOT/'src/linkloom/semantic_ingestion/extraction.py').read_bytes()).hexdigest() != manifest['extractor_sha256']:
        raise ValueError('FROZEN_EXTRACTOR_CHANGED')
    # Hash verification does not deserialize labels into the execution process.
    from linkloom.semantic_ingestion.extraction import _provider_tool
    if read_frozen('schema.json',manifest) != _provider_tool().to_dict():
        raise ValueError('FROZEN_SCHEMA_CHANGED')
    return dict(manifest_sha256=digest(manifest), manifest=manifest,
        sources=read_frozen('sources.json',manifest), config=read_frozen('config.json',manifest),
        control=read_frozen('control.json',manifest),
        usage_contract=read_frozen('usage_contract.json',manifest))


def artifact_for(source):
    artifact = RawArtifact(source['workspace_id'], source['id'], source['source_type'], source['content'],
        ingestion_time=datetime.fromisoformat(source['ingestion_time']))
    if artifact.artifact_version_id != source['artifact_version_id'] or artifact.content_hash != source['content_sha256']:
        raise ValueError('SOURCE_IDENTITY_MISMATCH')
    return artifact


def validate_grant(grant, freeze):
    config = freeze['config']
    expected = dict(approved=True, experiment=freeze['manifest']['version'], manifest_sha256=freeze['manifest_sha256'],
        model_id=config['model_id'], provider_id=config['provider_id'],
        max_requests=config['max_requests'], max_cost_usd=config['max_cost_usd'],
        input_price_ceiling_per_million=config['input_price_ceiling_per_million'],
        output_price_ceiling_per_million=config['output_price_ceiling_per_million'])
    if (not isinstance(grant, dict) or set(grant) != set(expected)|{'expires_at'}
            or any(grant.get(k) != v for k,v in expected.items()) or grant.get('approved') is not True):
        raise ValueError('LIVE_AUTHORIZATION_REQUIRED_OR_MISMATCH')
    try:
        expires = datetime.fromisoformat(grant['expires_at'].replace('Z','+00:00'))
        if expires.tzinfo is None or expires <= datetime.now(UTC):
            raise ValueError('expired')
    except (ValueError, TypeError, AttributeError):
        raise ValueError('LIVE_AUTHORIZATION_EXPIRED') from None


class Budget:
    """SQLite transactions reserve before send; incomplete requests stop the run.

    No reset/delete/release operation. A new output path cannot reset LIVE budget.
    Only this experiment's fixed configuration is accepted.
    """
    def __init__(self, path, freeze):
        self.config, self.manifest = freeze['config'], freeze['manifest_sha256']
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS binding (manifest TEXT PRIMARY KEY)')
        self.db.execute('CREATE TABLE IF NOT EXISTS requests (identity TEXT PRIMARY KEY, request TEXT, status TEXT, response TEXT)')
        self.db.execute('INSERT INTO binding SELECT ? WHERE NOT EXISTS (SELECT 1 FROM binding)', (self.manifest,))
        if self.db.execute('SELECT manifest FROM binding').fetchall() != [(self.manifest,)]:
            raise ValueError('BUDGET_BINDING_MISMATCH')

    def reserve(self, identity, request):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            rows = self.db.execute('SELECT identity,status FROM requests').fetchall()
            if any(status != 'COMPLETE' for _,status in rows):
                raise ValueError('PENDING_OR_UNKNOWN_STOP')
            if any(key == identity for key,_ in rows):
                raise ValueError('DUPLICATE_REQUEST_STOP')
            count = len(rows)+1
            if count > self.config['max_requests'] or count*self.config['reservation_usd'] > self.config['max_cost_usd']:
                raise ValueError('BUDGET_EXCEEDED')
            self.db.execute('INSERT INTO requests VALUES (?, ?, ?, NULL)',
                (identity, json.dumps(request,ensure_ascii=False,sort_keys=True), 'RESERVED'))
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def complete(self, identity, response, *, safe):
        self.db.execute('UPDATE requests SET status=?,response=? WHERE identity=? AND status=?',
            ('COMPLETE' if safe else 'UNKNOWN_STOP', json.dumps(response,ensure_ascii=False,sort_keys=True),identity,'RESERVED'))

    def close(self):
        self.db.close()


def account_usage(record, freeze):
    """Conserve Total=P+tool-use+thinking+C; never edit the SDK snapshot.

    Missing optional components have a joint residual, not an assumed zero.
    Independent worst-case bounds protect both caps; the joint residual is
    charged once at the higher rate. All provenance is separate from Usage.
    """
    contract = freeze['usage_contract']['version']
    try:
        usage, normalized = record['usage'], record['normalized']['usage']
        if not isinstance(usage,dict) or digest(usage) != digest(record['sdk_response']['usage_metadata']):
            raise ValueError('RAW_USAGE_MISMATCH')
        required = ('prompt_token_count','candidates_token_count','total_token_count')
        optional = ('thoughts_token_count','tool_use_prompt_token_count')
        for name in required + optional + ('cached_content_token_count',):
            value = usage.get(name)
            if value is None and name not in required:
                continue
            if type(value) is not int or value<0:
                raise ValueError('INVALID_TOKEN_COUNT:'+name)
        p,c,total = (usage[name] for name in required)
        for name,value in zip(('input_tokens','output_tokens','total_tokens'),(p,c,total),strict=True):
            if type(normalized.get(name)) is not int or normalized[name]!=value:
                raise ValueError('NORMALIZED_USAGE_MISMATCH')
        if usage.get('cached_content_token_count') is not None and usage['cached_content_token_count']>p:
            raise ValueError('CACHE_EXCEEDS_PROMPT')
        residual = total-p-c
        unknown = [name for name in optional if usage.get(name) is None]
        remainder = residual-sum(usage[name] for name in optional if name not in unknown)
        if remainder<0 or (not unknown and remainder!=0):
            raise ValueError('TOTAL_COMPONENTS_CONTRADICT')
        parts = []
        for name in optional:
            reported = usage.get(name)
            if reported is not None:
                state,low,high = 'REPORTED',reported,reported
            elif len(unknown)==1 or remainder==0:
                state,low,high = 'DERIVED',remainder,remainder
            else:
                state,low,high = 'BOUNDED',0,remainder
            parts.append(dict(state=state,lower=low,upper=high,
                sdk_field_state='ABSENT' if name not in usage else 'NULL' if reported is None else 'INTEGER'))
        thinking,tool = parts
        input_upper,generated_upper = p+tool['upper'],c+thinking['upper']
        config = freeze['config']
        ip,op = (Decimal(str(config[name])) for name in
            ('input_price_ceiling_per_million','output_price_ceiling_per_million'))
        unallocated = residual-thinking['lower']-tool['lower']
        cost = (p*ip+c*op+thinking['lower']*op+tool['lower']*ip
            +unallocated*max(ip,op))/Decimal(1000000)
        within_reservation = cost<=Decimal(str(config['reservation_usd']))
        reason = None
        if (input_upper>config['input_token_cap'] or generated_upper>config['max_output_tokens']
                or generated_upper>config['billable_output_token_cap']):
            reason = 'TOKEN_UPPER_BOUND_EXCEEDED'
        elif not within_reservation:
            reason = 'RESERVATION_UPPER_BOUND_EXCEEDED'
        state = 'BOUNDED' if any(part['state']=='BOUNDED' for part in parts) else 'DERIVED' if unknown else 'REPORTED'
        return dict(contract=contract,state=state,safe=reason is None,reason=reason,
            prompt=dict(state='REPORTED',value=p),
            candidates=dict(state='REPORTED',value=c),total=dict(state='REPORTED',value=total),
            thinking=thinking,tool_use_prompt=tool,optional_joint_total=residual,
            input_upper=input_upper,generated_upper=generated_upper,cost_upper_usd=str(cost),
            reservation_usd=str(Decimal(str(config['reservation_usd']))),within_reservation=within_reservation)
    except (KeyError,TypeError,AttributeError,ValueError) as exc:
        return dict(contract=contract,state='UNKNOWN',safe=False,reason=str(exc))


class LiveTransport:
    def __init__(self, freeze, grant, sdk, budget):
        from linkloom.agents.providers import GeminiProviderAdapter
        self.freeze, self.grant, self.sdk, self.budget = freeze, grant, sdk, budget
        self.adapter = GeminiProviderAdapter(self, model_id=freeze['config']['model_id'])
        self.active = False
        self.sent = False
        self.wire_sends = 0
        self.raw = None

    def observe_wire(self, _):
        self.wire_sends += 1
        if self.wire_sends != 1:
            raise ValueError('SDK_RETRY_FORBIDDEN')

    def generate_content(self, *, model, contents, config):
        validate_grant(self.grant, self.freeze)
        if load_freeze()['manifest_sha256'] != self.freeze['manifest_sha256']:
            raise ValueError('LIVE_FREEZE_CHANGED')
        if not self.active or self.sent:
            raise ValueError('RETRY_FORBIDDEN')
        self.sent = True
        payload = dict(model=model, contents=contents, config=config)
        if len(json.dumps(payload,ensure_ascii=False).encode()) > self.freeze['config']['max_wire_bytes']:
            raise ValueError('REQUEST_TOO_LARGE')
        self.raw = self.sdk.models.generate_content(**payload)
        return self.raw

    def complete(self, request, identity):
        validate_grant(self.grant, self.freeze)
        self.budget.reserve(identity, request.to_dict())
        self.active, self.sent, self.wire_sends, self.raw = True, False, 0, None
        try:
            response = self.adapter.complete(request)
            raw = self.raw if isinstance(self.raw,dict) else self.raw.model_dump(mode='json') if self.raw is not None else None
            usage = raw.get('usage_metadata') if isinstance(raw,dict) else None
            version = response.provider_metadata.get('model_version')
            record = dict(normalized=response.to_dict(), sdk_response=raw, usage=usage,
                          actual_model_version=version, reservation_usd=self.freeze['config']['reservation_usd'],
                          wire_sends=self.wire_sends)
            record['usage_accounting'] = account_usage(record,self.freeze)
            safe = (not response.error and self.wire_sends == 1
                and version == self.freeze['config']['model_id']
                and record['usage_accounting']['safe'])
            self.budget.complete(identity, record, safe=safe)
            if not safe:
                raise ValueError('PROVIDER_USAGE_VERSION_OR_OUTCOME_STOP')
            return response, record
        finally:
            self.active = False


def live_root():
    return Path.home()/'.linkloom'/'m3o-ab-v3'


def open_live(freeze, grant, *, budget=None):
    validate_grant(grant, freeze)
    if version('google-genai') != '2.22.0':
        raise ValueError('PINNED_SDK_VERSION_REQUIRED')
    from linkloom.agents.providers import build_gemini_sdk_client
    # Deliberately no output/CLI override for the persistent LIVE budget.
    budget = budget or Budget(live_root()/'budget.sqlite3', freeze)
    transport = LiveTransport(freeze, grant, None, budget)
    transport.sdk = build_gemini_sdk_client(os.environ.get('GEMINI_API_KEY',''),
        http_options_kwargs={'timeout':130000,'retry_options':{'attempts':1},
                             'base_url':'https://generativelanguage.googleapis.com'},
        wire_payload_observer=transport.observe_wire)
    return transport


def discover(freeze, source, profile, provider):
    """One canonical request/Host projection for offline, staged LIVE and verification."""
    artifact = artifact_for(source)
    config = freeze['config']
    def guard(request):
        return ProviderRequestAuthorization.approve(request, guard_id=config['guard_id'],
            reservation_id=source['id']+'-'+('B' if profile else 'A'),
            estimated_cost_upper_bound_usd=config['reservation_usd'])
    extractor = ProviderNeutralSemanticExtractor(provider,provider_id=config['provider_id'],model_id=config['model_id'],
        request_guard=guard,request_guard_id=config['guard_id'],max_attempts=1,
        max_output_tokens=config['max_output_tokens'],temperature=config['temperature'],thinking_level=config['thinking_level'])
    registry = SourceReferenceRegistry()
    for segment in parse_source_text(artifact).segments:
        registry.register_evidence(artifact.workspace_id, segment.evidence_span.evidence_ref,
            content_hash=artifact.content_hash,source_version=artifact.artifact_version_id)
    service = TaskObservationDiscoveryService(extractor,source_registry=registry,current_source=lambda *_:artifact,
        task_profile=TaskDiscoveryProfile(profile) if profile else None)
    return service.discover((artifact,),workspace_id=artifact.workspace_id)


def execute(output, *, mode='mock', replay=None, grant=None, stage=None, enable_live=False):
    if mode not in ('mock','replay','live'):
        raise ValueError('INVALID_MODE')
    freeze = load_freeze()
    if mode == 'live':
        validate_grant(grant, freeze)
        if not enable_live:
            raise ValueError('LIVE_AUTHORIZATION_REQUIRES_ENABLE_LIVE')
        from linkloom.evaluation.task_profile_checkpoint import execute_stage
        return execute_stage(output,freeze,grant,stage)
    if stage is not None or enable_live:
        raise ValueError('STAGES_REQUIRE_EXPLICIT_LIVE')
    if mode == 'replay' and replay is None:
        raise ValueError('REPLAY_REQUIRED')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    save(output/'freeze.json', freeze)
    rows, counts, pairs = [], {'A':0,'B':0}, []
    config = freeze['config']
    try:
        for source in freeze['sources']:
            requests = {}
            for arm,profile in config['arms'].items():
                identity = source['id']+'-'+arm
                class Provider:
                    response = None
                    def complete(self, request):
                        requests[arm] = request.to_dict()
                        save(output/(identity+'.request.json'), request.to_dict())
                        counts[arm] += 1
                        if mode == 'replay':
                            if read(Path(replay)/(identity+'.request.json')) != request.to_dict():
                                raise ValueError('REPLAY_REQUEST_MISMATCH')
                            self.response = ModelResponse.from_dict(read(Path(replay)/(identity+'.response.json')))
                        elif source['id'] == 'five-messages':
                            # SAME old output in both profiles: contract-only reuse,
                            # never a B model response or a strict request replay.
                            self.response = ModelResponse.from_dict(read(ROOT/'docs/requirements/m3-task-discovery/evidence/live/response-1.json'))
                        else:
                            self.response = ModelResponse(action=ModelAction.tool(ToolCall(identity,
                                'emit_semantic_candidates', {'claims':[]})), usage=ModelUsage())
                        save(output/(identity+'.response.json'), self.response.to_dict())
                        return self.response
                provider = Provider()
                result = discover(freeze,source,profile,provider)
                save(output/(identity+'.host.json'),result.to_dict())
                if provider.response is None:
                    raise ValueError('PROVIDER_OR_INFRASTRUCTURE_STOP:'+identity)
                rows.append(dict(identity=identity,case=source['id'],arm=arm))
            a,b = requests['A'],requests['B']
            pairs.append(dict(case=source['id'],schema_equal=a['available_tools']==b['available_tools'],
                options_equal=a['generation_options']==b['generation_options'],
                profile_different=digest(a)!=digest(b), request_sha256={k:digest(v) for k,v in requests.items()}))
        if counts['A'] != counts['B'] or not all(p['schema_equal'] and p['options_equal'] and p['profile_different'] for p in pairs):
            raise ValueError('PAIRING_INVALID')
        report = dict(mode=mode, counts=counts, pairs=pairs, rows=rows,
            real_provider_requests=0,
            REAL_MODEL_SEMANTIC_EFFECT='NOT_EVALUATED', manifest_sha256=freeze['manifest_sha256'],
            interpretation='Mock uses empty outputs plus SAME historical failure output for both profiles; contract evidence only.')
        save(output/'execution.json',report)
        return report
    except BaseException as exc:
        save(output/'STOP.json',dict(reason=type(exc).__name__,detail=str(exc),counts=counts,
            completed=rows, resume='Inspect durable budget and raw artifacts; never automatically resend.'))
        raise


def score(output):
    from linkloom.evaluation.task_profile_score import score_case
    from linkloom.semantic_ingestion.task_observation import TaskObservation
    from types import SimpleNamespace
    output = Path(output)
    freeze = load_freeze()
    execution = read(output/'execution.json')
    if execution['manifest_sha256'] != freeze['manifest_sha256']:
        raise ValueError('SCORE_FREEZE_MISMATCH')
    read_frozen('rubric.json',freeze['manifest'])
    labels = read_frozen('labels.json',freeze['manifest'])
    groups = read_frozen('groups.json',freeze['manifest'])
    sources = {s['id']:s for s in freeze['sources']}
    scores = {}
    for row in execution['rows']:
        identity = row['identity']
        raw = ModelResponse.from_dict(read(output/(identity+'.response.json')))
        host = read(output/(identity+'.host.json'))
        result = SimpleNamespace(
            valid_observations=tuple(TaskObservation.from_dict(o) for o in host['valid_observations']),
            partial_observations=tuple(TaskObservation.from_dict(o) for o in host['partial_observations']),
            failures=tuple(SimpleNamespace(**f) for f in host['failures']),
            groups=tuple(SimpleNamespace(**g) for g in host['groups']),
            extractions=tuple(SimpleNamespace(failure_code=SimpleNamespace(value=e['failure_code']) if e['failure_code'] else None)
                              for e in host['extractions']))
        scores[identity] = score_case(artifact_for(sources[row['case']]),labels[row['case']],raw,result,
            allowed_groups=groups.get(row['case'],()))
        sdk = read(output/(identity+'.sdk.json')) if (output/(identity+'.sdk.json')).exists() else {}
        scores[identity]['usage'] = dict(normalized=raw.usage.to_dict(),
            provider_input_output_thinking=sdk.get('usage'),
            provenance='LIVE_SDK' if sdk else 'RECORDED_HISTORICAL_OUTPUT' if row['case']=='five-messages' else 'MOCK_UNKNOWN',
            new_cost_upper_bound_usd=freeze['config']['reservation_usd'] if execution['mode']=='live' else 0)
    save_or_match(output/'scores.json',scores)
    return scores


def save_or_match(path, value):
    if Path(path).exists():
        if read(path)!=value:
            raise ValueError('EXISTING_REPORT_MISMATCH')
    else:
        save(path,value)


def run(output, **kwargs):
    report = execute(output, **kwargs)
    if kwargs.get('mode') == 'live' and report.get('status') != 'LIVE_AB_COMPLETED':
        return report
    sends = report.pop('sends_this_invocation',None)
    report['scores'] = score(output)
    report['totals'] = {}
    for arm in ('A','B'):
        rows = [s for key,s in report['scores'].items() if key.endswith('-'+arm)]
        totals = {}
        for stage in ('raw','host'):
            count = {k:sum(s[stage][k] for s in rows) for k in
                ('expected','predicted','matched','erroneous_merges','description_coverage')}
            count['recall'] = count['matched']/count['expected'] if count['expected'] else None
            count['precision'] = count['matched']/count['predicted'] if count['predicted'] else None
            count['fields'] = {f:{k:sum(s[stage]['fields'][f][k] for s in rows) for k in ('correct','total')}
                               for f in ('owner','project','deadline_text','vendor')}
            totals[stage] = count
        totals.update({k:sum(s[k] for s in rows) for k in ('false_rejections','false_acceptances','schema_valid')})
        report['totals'][arm] = totals
    save_or_match(Path(output)/'report.json', report)
    if sends is not None:
        report['sends_this_invocation'] = sends
    return report
