"""Offline Product HTTP clients; scripted semantics are test inputs, never Gold."""
from contextlib import contextmanager
from threading import Thread
from time import monotonic, sleep

from linkloom.ui.server import create_http_server
from tests.integration.test_product_application_flow import _UnusedRunBackend, _client
from tests.m3i_support import QUESTION


@contextmanager
def product_http(app, service):
    server = create_http_server(_UnusedRunBackend(), product_app=app,
                                investigation_service=service, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def http_ask(base, workspace, question=QUESTION):
    status, payload = _client(base, '/api/product/ask', method='POST',
                             data=dict(query=question, workspace_id=workspace))
    assert status in {200, 202}, payload
    result = payload['result']
    deadline = monotonic() + 120
    while result['status'] == 'INVESTIGATING' and monotonic() < deadline:
        sleep(.05)
        status, payload = _client(base,
            f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
        assert status == 200, payload
        result = payload['result']
    assert result['status'] != 'INVESTIGATING', result
    return result


def revision_product(root, *, records='both'):
    """Pre-existing P4 records from public inputs + original proof APIs.

    Only fixture setup writes the sidecar. Product execution never seeds it.
    No Gold file or verdict override is involved.
    """
    import json
    from uuid import uuid4
    from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
    from linkloom.tools.contracts import ToolCall
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.document_text import parse_source_text
    from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
    from linkloom.semantic_ingestion.task_relation_models import EvidenceQuote
    from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
    from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
    from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
    from linkloom.semantic_ingestion.task_revision_comparison import compare_revision
    from linkloom.ui.product import ProductApplication
    from linkloom.ui.task_investigation import ProductTaskSources, TaskInvestigationModels
    from linkloom.ui.investigation import ProductInvestigationService
    from tests.m3i_support import runtime_model, ScriptedStage
    from tests.task_revision_support import public_cases
    from tests.unit.test_task_observation_discovery import claim

    case = next(c for c in public_cases() if c['case_id'] == 'D04')
    app = ProductApplication(database_path=root / 'product.sqlite', extractor=None)
    workspace = app.repository.create_workspace('Public synthetic revision')
    ws = workspace['workspace_id']
    app.repository.select_workspace(ws)
    for item in case['sources'][:2]:
        sid = 'src_' + uuid4().hex
        artifact = RawArtifact(ws, sid, 'timestamped_text', item['content'], source_uri=item['artifact_id'])
        app.repository.create_source(ws, sid, item['artifact_id'], artifact,
            tuple(s.evidence_span for s in parse_source_text(artifact).segments))
        app.repository.set_ingestion_result(ws, sid, artifact.artifact_version_id,
            status='COMPLETE', candidate_count=0, review_required_count=0)
    sources = ProductTaskSources(ws, app.snapshot_investigation_sources(ws)[1],
        lambda: app.snapshot_investigation_sources(ws)[1])
    observations = [build_task_observation(TaskProposal.from_dict(item['task']),
        parse_source_text(artifact).segments[0], artifact)
        for item, artifact in zip(case['observations'][:2], sources.artifacts)]
    old, new = observations
    proof = tuple(EvidenceQuote(o.provenance, o.provenance.char_start, o.provenance.char_end,
                                o.provenance.quote, 'RELATION_PROPOSAL') for o in observations)
    args = dict(artifacts=sources.artifacts, source_registry=sources.registry,
                context_observations=tuple(observations))
    identity = assess_same_task(new, old, **args)
    revision = assess_plan_revision(new, old, same_task=identity, evidence=proof, **args)
    comparison = compare_revision(new, old, same_task=identity, revision=revision, evidence=proof, **args)
    assert identity.verdict == revision.verdict == 'SUPPORTED'
    assert comparison.status == 'COMPUTABLE'
    sidecar = root / 'relations.sqlite'
    if records != 'none':
        with TaskRelationStore(sidecar) as store:
            store.put_assessment(identity)
            if records == 'both':
                store.put_assessment(revision, evidence=proof)
                store.put_comparison(comparison)

    class PublicStage:
        def __init__(self, stage):
            self.stage, self.requests = stage, []

        def complete(self, request):
            self.requests.append(request.to_dict())
            if self.stage == 'discovery':
                task = next(o['task'] for o in case['observations'][:2] if o['task']['text'] in request.user_input)
                arguments = {'claims': [claim(task['text'], *task['description_parts'], owner=task['owner'],
                    project=task['project'], deadline_text=task['deadline_text'])]}
            else:
                data = json.loads(request.user_input.split('INPUT_JSON\n', 1)[1])
                handles = [e['handle'] for e in data['endpoints']]
                template = dict(question='原文关系是否有可信支持？', handles=list(reversed(handles[:2])),
                    source_handles=[], field='', target_kind='', missing_entities=[])
                arguments = dict(steps=[
                    dict(template, id='identity', kind='relation', predicate='SAME_TASK', depends_on=[]),
                    dict(template, id='revision', kind='relation', predicate='REVISES_PLAN', depends_on=['identity']),
                    dict(template, id='dates', kind='compare_dates', predicate='', depends_on=['identity', 'revision'])],
                    uncertainties=[], refusal_reasons=[])
            return ModelResponse(action=ModelAction.tool(ToolCall('public-fixture',
                request.available_tools[0].tool_id, arguments)), usage=ModelUsage(input_tokens=20, output_tokens=10))

    stages = [PublicStage('discovery'), PublicStage('planning'), ScriptedStage('semantics')]
    service = ProductInvestigationService(app, model_factory=runtime_model, external_provider=False,
        run_mode='FAKE_MODEL_INTEGRATION', task_models=TaskInvestigationModels(*stages),
        task_relation_paths={ws: sidecar})
    return app, service, stages, ws, identity, revision, comparison


def cold_read(database, run_id, workspace, sidecar=None):
    from linkloom.ui.product import ProductApplication
    from linkloom.ui.investigation import ProductInvestigationService
    app = ProductApplication(database_path=database, extractor=None)
    try:
        service = ProductInvestigationService(app,
            task_relation_paths={workspace: sidecar} if sidecar else None)
        with product_http(app, service) as base:
            return _client(base, f'/api/product/ask/runs/{run_id}?workspace_id={workspace}')
    finally:
        app.close()


if __name__ == '__main__':
    import json
    import sys
    print(json.dumps(cold_read(*sys.argv[1:]), ensure_ascii=False))
