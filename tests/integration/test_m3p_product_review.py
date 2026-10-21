from tests.m3p_support import product_case
from tests.m3p_support import product_http, http_ask
from tests.m3p_support import revision_product
import pytest
import json
import subprocess
import sys


def test_offline_demo_rejects_a_user_named_database(tmp_path):
    from linkloom.ui.offline_task_demo import build_offline_task_demo
    from linkloom.ui.product import ProductApplication

    database = tmp_path / 'user.sqlite'
    app = ProductApplication(database_path=database, extractor=None)
    try:
        app.repository.create_workspace('User workspace')
    finally:
        app.close()
    with pytest.raises(ValueError, match='OFFLINE_DEMO_DATABASE_NOT_DEDICATED'):
        build_offline_task_demo(database)


def test_http_partial_exposes_original_tasks_proposal_and_exact_block(tmp_path):
    app, service, _, stages, workspace = product_case(tmp_path)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, workspace)
        assert result['status'] == 'PARTIAL'
        view = result['task_review']
        assert len(view['observations']) == 2
        # Existing F does not resolve the revision wording to a deadline role.
        # Preserve the second raw date without inventing a normalized field.
        assert {o['deadline'] for o in view['observations']} == {'2029-06-20', None}
        assert '2029-06-24' in view['observations'][1]['raw_text']
        assert 'deadline' in view['observations'][1]['unknown_fields']
        assert all(o['owner'] == '林枫' and o['field_evidence'] for o in view['observations'])
        proposal, = view['proposals']
        assert proposal['proposed_verdict'] == 'SUPPORTED'
        assert proposal['publishable'] is False
        assert proposal['supporting_evidence'] and proposal['source_validation']
        assert view['human_review']['available'] is False
        assert view['human_review']['reason'] == 'NO_AUTHENTICATED_RELATION_REVIEW_INTERFACE'
        revision = next(s for s in view['steps'] if s['step_id'] == 'revision')
        assert revision['status'] == 'BLOCKED'
        assert 'identity' in revision['depends_on']
        assert view['next_actions'] and view['unresolved']
        assert [t['tool'] for t in view['executed_tools']] == [
            'inspect_task_context', 'query_task_relations', 'analyze_task_semantics']
        assert all(e['source_id'] and e['quote'] for e in result['evidence'])
        assert not result['conclusions']
    finally:
        app.close()


def test_http_explicit_tasks_domain_accepts_owner_field_in_original_question(tmp_path):
    app, service, _, _, workspace = product_case(tmp_path)
    question = '请调查这几次会议里交付任务如何延续，负责人和截止日期的变动是否有可靠依据？'
    try:
        with product_http(app, service) as base:
            result = http_ask(base, workspace, question=question)
        assert result['query'] == question
        assert result['route']['intent'] == 'TASK_INVESTIGATION'
        assert result['path'] == 'TASK_INVESTIGATION'
        assert result['status'] == 'PARTIAL'
        assert result['route']['admission']['context']['domain'] == 'tasks'
    finally:
        app.close()


def test_new_service_reads_saved_result_without_model_calls(tmp_path):
    from linkloom.ui.product import ProductApplication
    from linkloom.ui.investigation import ProductInvestigationService
    from tests.integration.test_product_application_flow import _client
    app, service, _, _, workspace = product_case(tmp_path)
    with product_http(app, service) as base:
        result = http_ask(base, workspace)
    app.close()
    fresh = ProductApplication(database_path=tmp_path / 'product.sqlite', extractor=None)
    restored = ProductInvestigationService(
        fresh, model_factory=None, run_mode='MODEL_NOT_CONFIGURED', external_provider=False,
    )
    try:
        assert restored.config()['available'] is False
        assert restored.config()['external_provider'] is False
        with product_http(fresh, restored) as base:
            status, payload = _client(base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
        assert status == 200, payload
        saved = payload['result']
        assert saved['status'] == 'PARTIAL'
        assert saved['task_review']['observations'] == result['task_review']['observations']
        assert saved['recovery']['mode'] == 'SAVED_RESULT_READ_ONLY'
        assert saved['recovery']['session_resumed'] is False
    finally:
        fresh.close()
    # A separate Python process has no _runs/_jobs, F/G adapters or runtime model.
    child = subprocess.run([sys.executable, '-m', 'tests.m3p_support',
        str(tmp_path / 'product.sqlite'), result['agent_run_id'], workspace],
        capture_output=True, text=True, encoding='utf8', timeout=120, check=True)
    code, response = json.loads(child.stdout)
    assert code == 200 and response['result']['status'] == 'PARTIAL', response
    assert response['result']['recovery']['session_resumed'] is False


@pytest.mark.parametrize('mutation', ['ledger_missing', 'reviewer_rejected'])
def test_cold_read_requires_accepted_reviewer_and_complete_ledger(tmp_path, mutation):
    from dataclasses import replace
    from linkloom.runtime.checkpoint import SQLiteCheckpointer
    from tests.m3p_support import cold_read

    app, service, _, _, workspace = product_case(tmp_path)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, workspace)
    finally:
        app.close()
    base = tmp_path / 'work' / 'investigations'
    pointer = json.loads((base / (result['agent_run_id'] + '.json')).read_text(encoding='utf8'))
    root = base / pointer['root']
    checkpointer = SQLiteCheckpointer(root / 'runtime')
    state = checkpointer.get_latest(pointer['thread_id'])
    if mutation == 'ledger_missing':
        checkpointer.save(replace(state, tool_ledger=[], step_seq=state.step_seq + 1))
    else:
        path = root / 'runtime' / state.result_ref
        artifact = json.loads(path.read_text(encoding='utf8'))
        artifact['review']['decision'] = 'evidence_insufficient'
        path.write_text(json.dumps(artifact, ensure_ascii=False), encoding='utf8')
    code, response = cold_read(tmp_path / 'product.sqlite', result['agent_run_id'], workspace)
    saved = response['result']
    assert code == 200 and saved['status'] == 'BLOCKED'
    assert not saved['evidence'] and not saved['trace']
    assert not saved.get('task_review') and not saved.get('preparation')


@pytest.mark.parametrize('records', ['both', 'identity', 'none'])
def test_http_trusted_p4_only_unlocks_matching_prerequisites(tmp_path, records):
    app, service, stages, ws, identity, revision, comparison = revision_product(tmp_path, records=records)
    path = tmp_path / 'relations.sqlite'
    before = path.read_bytes() if path.exists() else None
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        assert result['status'] == 'PARTIAL', result
        steps = {s['step_id']: s for s in result['task_review']['steps']}
        assert steps['identity']['status'] == ('SUPPORTED' if records != 'none' else 'BLOCKED')
        assert steps['revision']['status'] == ('SUPPORTED' if records == 'both' else 'BLOCKED')
        assert steps['dates']['status'] == ('COMPLETED' if records == 'both' else 'BLOCKED')
        assert len(result['task_review']['observations']) == 2
        assert {o['deadline'] for o in result['task_review']['observations']} == {'2026-10-14', '2026-10-18'}
        assert 'query_task_relations' in [t['tool'] for t in result['trace']]
        if records == 'both':
            current_comparison = steps['dates']['comparisons'][0]['current_comparison']
            assert current_comparison['comparison_id'] == comparison.comparison_id
            assert current_comparison['delta_calendar_days'] == 4
            assert current_comparison['plan_currentness'] == 'NOT_ESTABLISHED'
        else:
            # Without both supported premises the product must not project a
            # comparison value, even when one prerequisite relation exists.
            assert steps['dates']['comparisons'] == []
            assert steps['dates']['assessment_ids'] == []
        assert (path.read_bytes() if path.exists() else None) == before
    finally:
        app.close()


@pytest.mark.parametrize('mutation', ['update', 'remove', 'relation_retract'])
def test_http_hot_and_new_process_hide_invalidated_results(tmp_path, mutation):
    from tests.integration.test_product_application_flow import _client
    from tests.unit.test_task_relation_query import retract
    from linkloom.semantic_ingestion.document_text import parse_source_text
    app, service, stages, ws, identity, _, _ = revision_product(tmp_path)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
            assert result['status'] == 'PARTIAL'
            if mutation == 'relation_retract':
                retract(tmp_path / 'relations.sqlite', ws, identity.assessment_id)
            else:
                source = app.snapshot_investigation_sources(ws)[1][0]
                if mutation == 'remove':
                    app.repository.delete_source(ws, source['source_id'])
                else:
                    artifact = app._artifact_from_version(dict(source, content=source['content'] + ' 资料更正。'))
                    app.repository.update_source(ws, source['source_id'], source['name'], artifact,
                        tuple(s.evidence_span for s in parse_source_text(artifact).segments))
            code, payload = _client(base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
            blocked = payload['result']
            assert code == 200 and blocked['status'] == 'BLOCKED'
            assert not blocked['evidence'] and not blocked['trace']
            assert not blocked.get('task_review') and not blocked.get('task_investigation')
    finally:
        app.close()
    child = subprocess.run([sys.executable, '-m', 'tests.m3p_support',
        str(tmp_path / 'product.sqlite'), result['agent_run_id'], ws, str(tmp_path / 'relations.sqlite')],
        capture_output=True, text=True, encoding='utf8', timeout=120, check=True)
    code, response = json.loads(child.stdout)
    blocked = response['result']
    assert code == 200 and blocked['status'] == 'BLOCKED'
    assert not blocked['evidence'] and not blocked['trace'] and not blocked.get('task_review')


def test_same_name_separate_work_never_inherits_fields_or_model_identity(tmp_path):
    from uuid import uuid4
    from linkloom.agents.model_adapter import ModelAction, ModelResponse
    from linkloom.tools.contracts import ToolCall
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.document_text import parse_source_text
    from tests.m3p_support import ScriptedStage
    from tests.unit.test_task_observation_discovery import claim
    from dataclasses import replace
    app, service, _, stages, ws = product_case(tmp_path)
    for source in app.snapshot_investigation_sources(ws)[1]:
        app.repository.delete_source(ws, source['source_id'])
    texts = [
        '[2029-06-01T09:00:00Z] 林枫: 项目：青竹；负责人：林枫；日志迁移；截止日期：2029-06-20；生产环境工作。',
        '[2029-06-04T09:00:00Z] 陈宁: 项目：青竹；日志迁移；这是独立的演练环境任务。',
    ]
    for i, text in enumerate(texts):
        sid = 'src_' + uuid4().hex
        artifact = RawArtifact(ws, sid, 'timestamped_text', text, source_uri=f'synthetic-{i}.txt')
        app.repository.create_source(ws, sid, f'distinct-{i}.txt', artifact,
            tuple(s.evidence_span for s in parse_source_text(artifact).segments))
        app.repository.set_ingestion_result(ws, sid, artifact.artifact_version_id,
            status='COMPLETE', candidate_count=0, review_required_count=0)

    class DistinctDiscovery:
        def complete(self, request):
            index = next(i for i, text in enumerate(texts) if text.split(': ', 1)[1] in request.user_input)
            return ModelResponse(action=ModelAction.tool(ToolCall('distinct', request.available_tools[0].tool_id,
                {'claims': [claim(texts[index].split(': ', 1)[1], '日志迁移', project='青竹',
                    owner='林枫' if index == 0 else None, deadline_text='2029-06-20' if index == 0 else None)]})))
    service.task_models = replace(service.task_models, discovery=DistinctDiscovery())
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        view = result['task_review']
        assert result['status'] == 'PARTIAL' and not view['relations']
        first, second = view['observations']
        assert first['description'] == second['description'] == '日志迁移'
        assert first['owner'] == '林枫' and second['owner'] is None and second['deadline'] is None
        assert view['proposals'][0]['proposed_verdict'] == 'SUPPORTED'
        assert all(s['status'] == 'BLOCKED' for s in view['steps'])
        assert not result['conclusions']
    finally:
        app.close()


def test_revoked_evidence_without_source_change_cannot_be_resurrected(tmp_path):
    from tests.integration.test_product_application_flow import _client
    app, service, _, ws, *_ = revision_product(tmp_path)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
            assert result['task_review']['steps'][-1]['status'] == 'COMPLETED'
            evidence_ref = result['evidence'][0]['evidence_ref']
            # Synthetic test-only authorization revocation. Content/version stay identical.
            with app.repository._lock, app.repository._connection:
                app.repository._connection.execute(
                    'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                    (ws, evidence_ref))
            code, payload = _client(base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={ws}")
            blocked = payload['result']
            assert code == 200 and blocked['status'] == 'BLOCKED', blocked
            assert not blocked['evidence'] and not blocked['trace'] and not blocked.get('task_review')
    finally:
        app.close()
    child = subprocess.run([sys.executable, '-m', 'tests.m3p_support',
        str(tmp_path / 'product.sqlite'), result['agent_run_id'], ws, str(tmp_path / 'relations.sqlite')],
        capture_output=True, text=True, encoding='utf8', timeout=120, check=True)
    code, response = json.loads(child.stdout)
    blocked = response['result']
    assert code == 200 and blocked['status'] == 'BLOCKED', blocked
    assert not blocked['evidence'] and not blocked['trace'] and not blocked.get('task_review')
