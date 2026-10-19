"""Closed Host composition for the frozen public Canary; no execution loop.

An explicit local grant enables construction, never HTTP input. The SDK remains
lazy and is configured for one attempt. Tests replace that construction boundary
and deny network independently of execution labels.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from threading import Lock

from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
from linkloom.semantic_ingestion.task_relation_models import canonical_hash

_SEAL = object()
_TOOLS = {
    'discovery': {'emit_semantic_candidates'},
    'planning': {'emit_investigation_plan'},
    'semantic': {'propose_task_relations'},
    'runtime': {'inspect_task_context', 'query_task_relations', 'analyze_task_semantics'},
}


def is_canary_adapter(adapter, stage=None):
    return (type(adapter) is _CanaryModel and adapter._seal is _SEAL
            and (stage is None or adapter.stage == stage))


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _campaign_root():
    # No CLI/HTTP override: changing worktree/database cannot create a new budget.
    return Path.home() / '.linkloom' / 'canary'


class _CanaryModel:
    def __init__(self, seal, host, case, stage, validate, source_identity):
        if seal is not _SEAL:
            raise ValueError('UNTRUSTED_CANARY_ADAPTER')
        self._seal, self.host, self.case, self.stage = seal, host, case, stage
        self.validate, self.source_identity = validate, source_identity
        self.adapter = GeminiProviderAdapter(self, model_id=host.spec.model_id)
        self.capability = self.adapter.capability
        self.execution_kind, self.provider_id, self.model_id = 'LIVE', 'gemini', host.spec.model_id
        self._active = False
        self._sent = False

    @property
    def provenance(self):
        return dict(execution_kind='LIVE', provider_id='gemini', model_id=self.model_id,
                    campaign_id=self.host.spec.campaign_id, manifest_sha256=self.host.spec.digest)

    def execution_receipt(self):
        return self.host.ledger.snapshot()

    def complete(self, request):
        self.host.check_authorization()
        self.host.ledger.check_open()
        self.validate()
        if self._active or request.model_id not in (None, self.model_id):
            self.host.ledger.stop('INVALID_ADAPTER_REQUEST')
            raise ValueError('INVALID_ADAPTER_REQUEST')
        self._active, self._sent = True, False
        self._request = request.to_dict()
        try:
            response = self.adapter.complete(request)
            if response.error and response.error.outcome == 'unknown_provider_outcome':
                self.host.ledger.stop('PROVIDER_RESPONSE_UNKNOWN')
            self.host.ledger.check_open()
            self.validate()
            return response
        finally:
            self._active = False

    def generate_content(self, *, model, contents, config):
        if not self._active or self._sent:
            self.host.ledger.stop('PROVIDER_RETRY_FORBIDDEN')
            raise ValueError('PROVIDER_RETRY_FORBIDDEN')
        self._sent = True
        self.validate()
        config = dict(config)
        tools = config.get('tools', [])
        names = set()
        for tool in tools:
            if set(tool) != {'function_declarations'}:
                raise ValueError('CANARY_HOSTED_TOOL_FORBIDDEN')
            names.update(f['name'] for f in tool['function_declarations'])
        if (model != self.model_id or not names <= _TOOLS[self.stage]
                or config.get('automatic_function_calling') != {'disable': True}):
            raise ValueError('CANARY_TOOL_PERMISSION_DENIED')
        limit = {'discovery': 1536, 'planning': 4096, 'semantic': 8192, 'runtime': 2048}[self.stage]
        config['max_output_tokens'] = min(config.get('max_output_tokens', limit), limit)
        config['thinking_config'] = {'thinking_level': 'LOW'}
        payload = dict(model=model, contents=contents, config=config)
        if len(_json(payload).encode('utf8')) > 49152:
            raise ValueError('CANARY_REQUEST_TOO_LARGE')
        with self.host._send_lock:
            self.host.check_authorization()
            self.host.ledger.check_open()
            self.validate()
            return self._send(payload)

    def _send(self, payload):
        request_id = self.host.ledger.reserve(self.case, self.stage,
            request_identity=canonical_hash(self._request), source_snapshot=self.source_identity,
            metadata=dict(model_request=self._request, wire_request=payload, **self.provenance))
        self.host._wire_sends = 0
        try:
            raw = self.host._sdk.models.generate_content(**payload)
            usage = raw.get('usage_metadata') if isinstance(raw, dict) else getattr(raw, 'usage_metadata', None)
            if hasattr(usage, 'model_dump'):
                usage = usage.model_dump(mode='json')
            receipt = self.host.ledger.complete(request_id, usage)
            if (receipt['usage_status'] == 'known'
                    and receipt['usage']['billable_output_tokens'] > payload['config']['max_output_tokens']):
                self.host.ledger.stop('STAGE_OUTPUT_USAGE_EXCEEDED')
                raise ValueError('STAGE_OUTPUT_USAGE_EXCEEDED')
            self.validate()
            return raw
        except Exception as exc:
            self.host.ledger.stop('PROVIDER_OUTCOME_UNKNOWN:' + type(exc).__name__)
            raise


class CanaryHost:
    """Owns the single campaign transport/ledger and exact source admission."""
    def __init__(self, seal, spec, ledger, sdk):
        if seal is not _SEAL:
            raise ValueError('UNTRUSTED_CANARY_HOST')
        self.spec, self.ledger, self._sdk = spec, ledger, sdk
        self._wire_sends = 0
        self._send_lock = Lock()

    @classmethod
    def open_live(cls, *, enabled=False, grant=None):
        if enabled is not True or not isinstance(grant, dict):
            raise ValueError('CANARY_LIVE_AUTHORIZATION_REQUIRED')
        from linkloom.ui.canary_spec import load_spec
        from linkloom.agents.providers.canary_ledger import CanaryLedger
        spec = load_spec()
        expected = dict(approved=True, campaign_id=spec.campaign_id, manifest_sha256=spec.digest,
                        provider_id='gemini', model_id=spec.model_id, max_requests=34, max_cost_usd=3)
        if any(grant.get(k) != v for k, v in expected.items()) or set(grant) != set(expected) | {'expires_at'}:
            raise ValueError('CANARY_LIVE_AUTHORIZATION_MISMATCH')
        try:
            if not isinstance(grant['expires_at'], str):
                raise ValueError('invalid expiry')
            expires = datetime.fromisoformat(grant['expires_at'].replace('Z', '+00:00'))
            if expires <= datetime.now(timezone.utc):
                raise ValueError('expired')
        except (TypeError, ValueError, KeyError):
            raise ValueError('CANARY_LIVE_AUTHORIZATION_EXPIRED') from None
        ledger = CanaryLedger(_campaign_root(), spec.campaign_id, spec.digest)
        host = cls(_SEAL, spec, ledger, None)
        host._expires = expires
        host.root = _campaign_root() / spec.campaign_id
        def wire_observer(_payload):
            host._wire_sends += 1
            if host._wire_sends > 1:
                host.ledger.stop('SDK_RETRY_FORBIDDEN')
                raise ValueError('SDK_RETRY_FORBIDDEN')
        host._sdk = build_gemini_sdk_client(os.environ.get('GEMINI_API_KEY', ''),
            http_options_kwargs={'timeout': 130000, 'retry_options': {'attempts': 1},
                                 'base_url': 'https://generativelanguage.googleapis.com'},
            wire_payload_observer=wire_observer)
        return host

    def check_authorization(self):
        if datetime.now(timezone.utc) >= self._expires:
            self.ledger.stop('CANARY_AUTHORIZATION_EXPIRED')
            raise ValueError('CANARY_AUTHORIZATION_EXPIRED')

    def bind(self, query, workspace_id, sources, current_sources):
        """Bind only seeded public workspaces, never infer scope from HTTP fields."""
        self.ledger.check_open()
        if workspace_id not in self._workspaces.values() or query != self.spec.question:
            raise ValueError('CANARY_WORKSPACE_OR_QUESTION_NOT_AUTHORIZED')
        try:
            case, expected = self.spec.match_sources(query, tuple(s['content'] for s in sources))
        except ValueError:
            self.ledger.stop('SOURCE_SNAPSHOT_CHANGED')
            raise
        if self._workspaces.get(case.case_id) != workspace_id:
            raise ValueError('CANARY_WORKSPACE_NOT_AUTHORIZED')
        expected_ids = {'src_' + canonical_hash([self.spec.campaign_id, case.case_id, label])[:32]
                        for label in expected}
        if {s['source_id'] for s in sources} != expected_ids:
            self.ledger.stop('CANARY_SOURCE_IDENTITY_MISMATCH')
            raise ValueError('CANARY_SOURCE_IDENTITY_MISMATCH')
        expected_text = {'src_' + canonical_hash([self.spec.campaign_id, case.case_id, label])[:32]:
                         self.spec.sources[label] for label in expected}
        if any(s['content'] != expected_text[s['source_id']] for s in sources):
            self.ledger.stop('CANARY_SOURCE_IDENTITY_MISMATCH')
            raise ValueError('CANARY_SOURCE_IDENTITY_MISMATCH')
        identity = canonical_hash(sorted((s['source_id'], s['source_version'],
                                          canonical_hash(s['content'])) for s in sources))
        def validate():
            try:
                now = current_sources()
            except Exception:
                self.ledger.stop('SOURCE_SNAPSHOT_UNAVAILABLE')
                raise
            current = canonical_hash(sorted((s['source_id'], s['source_version'],
                                             canonical_hash(s['content'])) for s in now))
            if current != identity:
                self.ledger.stop('SOURCE_SNAPSHOT_CHANGED')
                raise ValueError('SOURCE_SNAPSHOT_CHANGED')
        from linkloom.ui.task_investigation import TaskInvestigationModels
        from linkloom.semantic_ingestion.task_observation_discovery import DiscoveryLimits
        from linkloom.semantic_ingestion.task_relation_question_models import PlanningLimits
        source_binding = dict(workspace_id=workspace_id, snapshot_sha256=identity,
            sources=[dict(source_id=s['source_id'], source_version=s['source_version'],
                          content_sha256=hashlib.sha256(s['content'].encode('utf8')).hexdigest())
                     for s in sorted(sources, key=lambda s: s['source_id'])])
        self.ledger.bind_sources(case.case_id, source_binding)
        models = {s: _CanaryModel(_SEAL, self, case.case_id, s, validate, source_binding) for s in _TOOLS}
        stages = TaskInvestigationModels(models['discovery'], models['planning'], models['semantic'],
            execution_kind='LIVE', provider_id='gemini', model_id=self.spec.model_id,
            discovery_limits=DiscoveryLimits(max_calls=len(expected)),
            planning_limits=PlanningLimits(max_provider_calls=2))
        return stages, models['runtime']

    def product_application(self):
        """Seed only a dedicated catalog; never open an arbitrary product/Vault DB.

        Repository-issued workspace IDs persist in this catalog. Source bytes
        and deterministic fixture source IDs are verified at each admission.
        """
        from linkloom.ui.product import ProductApplication
        from linkloom.semantic_ingestion.artifacts import RawArtifact
        from linkloom.semantic_ingestion.document_text import parse_source_text
        self.root.mkdir(parents=True, exist_ok=True)
        app = ProductApplication(database_path=self.root / 'product.sqlite', extractor=None)
        existing = {w['name']: w['workspace_id'] for w in app.repository.list_workspaces()}
        expected_names = {'M3 Canary ' + c for c in self.spec.cases}
        if existing and set(existing) != expected_names:
            app.close()
            raise ValueError('CANARY_CATALOG_MISMATCH')
        self._workspaces = {}
        for case_id, case in self.spec.cases.items():
            name = 'M3 Canary ' + case_id
            workspace = existing.get(name)
            if workspace is None:
                workspace = app.repository.create_workspace(name)['workspace_id']
                for label in case.source_ids:
                    source_id = 'src_' + canonical_hash([self.spec.campaign_id, case_id, label])[:32]
                    artifact = RawArtifact(workspace, source_id, 'timestamped_text', self.spec.sources[label],
                        source_uri=f'canary://{case_id}/{label}')
                    app.repository.create_source(workspace, source_id, label + '.txt', artifact,
                        tuple(s.evidence_span for s in parse_source_text(artifact).segments))
                    app.repository.set_ingestion_result(workspace, source_id, artifact.artifact_version_id,
                        status='COMPLETE', candidate_count=0, review_required_count=0)
            self._workspaces[case_id] = workspace
        app.repository.select_workspace(self._workspaces['C1'])
        return app
