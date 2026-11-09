"""Offline graph demo seeded through the ordinary import/approval lifecycle.

Only extraction and planning responses are scripted; source IDs, versions,
evidence, approvals and graph execution belong to the real product.
"""
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor, ProviderRequestAuthorization,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.ui.graph_investigation import GraphInvestigationService
from tests.unit.test_temporal_graph_planning import Model, planner
from unified_ask_fixture import UnifiedAskFixture, OfflineSemanticProvider, claim, request_json, wait_source

GRAPH_MEETING = (
    '[2026-04-01T09:00:00Z] Speaker-A: Bay 项目自 2026-04-01 起由 演示成员A 负责。\n'
    '[2026-04-01T10:00:00Z] Speaker-A: 演示成员A 自 2026-04-01 起向 演示主管B 直接汇报。'
)
GRAPH_QUERY = 'Bay 项目负责人的直属领导是谁？'
GRAPH_AS_OF = '2026-04-02'


def build_graph_demo(database_path, *, seed_graph=True):
    demo = UnifiedAskFixture(database_path, seed=False)
    try:
        claims = []
        for subject, relation, value in [('Bay', 'owner_of_project', '演示成员A'), ('演示成员A', 'reports_to', '演示主管B')]:
            item = claim(value, claim_type='FACT')
            item.update(subject=subject, relation='负责' if relation == 'owner_of_project' else '直接汇报', temporal_status='EXPLICIT', valid_from='2026-04-01')
            claims.append(item)
        demo.app.extractor = ProviderNeutralSemanticExtractor(
            OfflineSemanticProvider([*claims, claim('Birchline')]),
            provider_id='offline-semantic-provider', model_id='offline-semantic-model',
            request_guard=lambda request: ProviderRequestAuthorization.approve(
                request, guard_id='graph-demo', reservation_id='offline', estimated_cost_upper_bound_usd=0),
            request_guard_id='graph-demo', max_attempts=1)
        demo.app.relation_resolver = FrozenRelationResolver(('owner_of_project', 'reports_to', 'uses supplier'),
            aliases={'负责': 'owner_of_project', '直接汇报': 'reports_to'})
        demo.configure_agent()

        def authorized(ws, source, version):
            return demo.app._workspace_id() == ws and any(
                s['source_id'] == source and s['source_version'] == version and s['active']
                for s in demo.app.repository.list_sources(ws))

        def factory(context):
            project = next(n for n in context.nodes if n.kind == 'project' and n.label == 'Bay')
            return planner(Model(dict(applicable=True, as_of='', refusal_reasons=[], steps=[
                dict(id='owner', input_node=project.node_id, input_step='', edge_type='owner_of', direction='in', as_of=''),
                dict(id='leader', input_node='', input_step='owner', edge_type='reports_to', direction='out', as_of='')],
                outputs=[dict(step='leader', field='node')])))

        demo.server.unified_ask.graph_service = GraphInvestigationService(
            lambda ws: SQLiteGraphStore(database_path, authorized_workspace=ws, authorize_source=authorized), factory)
        if seed_graph:
            seed_graph_meeting(demo)
        return demo
    except BaseException:
        demo.stop()
        raise


def seed_graph_meeting(demo):
    """Use public import/approval APIs, including their registration and gates.

    Do not insert document-type evaluation records or reconstruct their versions
    by hand: the Product UI imports timestamped_text artifacts.
    """
    status, body = request_json(demo.base_url, '/api/workspaces', method='POST',
                               data={'name': '离线图检索演示'})
    assert status == 201, body
    status, body = request_json(demo.base_url, '/api/sources/import', method='POST',
                               data={'name': 'graph-demo-meeting.txt', 'content': GRAPH_MEETING})
    assert status == 202, body
    source = wait_source(demo.base_url, body['source']['source_id'])
    assert source['ingestion_status'] == 'COMPLETE' and source['candidate_count'] == 2, source
    status, body = request_json(demo.base_url, '/api/review/candidates')
    assert status == 200 and len(body['candidates']) == 2, body
    for candidate in body['candidates']:
        status, detail = request_json(demo.base_url, f"/api/review/candidates/{candidate['candidate_id']}")
        assert status == 200, detail
        review = {'reviewer_id': 'offline-demo-simulated-human',
                  'reason': 'Synthetic demo: checked the explicit relationship and effective date.'}
        if 'valid_from' in detail['candidate']['current_unresolved_fields']:
            review['valid_from'] = '2026-04-01'
        status, approved = request_json(demo.base_url,
            f"/api/review/candidates/{candidate['candidate_id']}/approve", method='POST',
            data=review)
        assert status == 200 and approved['workflow_state'] == 'MATERIALIZED', approved
    return source
