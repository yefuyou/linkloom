"""Actual route contracts, offline fake transport; not the QA campaign."""
from datetime import UTC,datetime,timedelta,timezone
import json
import pytest
from tests.graph_eval_support import confirm_candidates
from tests.unit.test_temporal_graph_projection import fixture_public
from tests.unit.test_temporal_graph_eval import inputs
from run_temporal_graph_dev import FrozenDevInputs,CampaignBudget,PriceReference,BudgetExceeded
from temporal_graph_routes import TemporalGraphDevRoutes
from linkloom.agents.model_adapter import ModelResponse,ModelAction
from linkloom.tools.contracts import ToolCall
from run_bounded_multihop import SourceOnlyController


def setup_routes(tmp_path):
    base=inputs(tmp_path);public=fixture_public();fixture=tmp_path/'public.json';fixture.write_text(json.dumps(public),encoding='utf8')
    db=tmp_path/'formal.sqlite';confirm_candidates(public,db)
    config=dict(model_id='fake',max_input_bytes=98304,max_output_tokens=4096,legacy_max_turns=12,
        retry_attempts=1,retrieval_mode='bm25',graph_limits={})
    path=tmp_path/'configuration.json';path.write_text(json.dumps(config),encoding='utf8')
    frozen=FrozenDevInputs(base.questions,base.expected,tmp_path/'route-freeze.json',
        public_fixture=fixture,confirmed_database=db,configuration=path);frozen.create()
    today=datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    price=PriceReference('fake',.0001,.0001,today,'https://ai.google.dev/gemini-api/docs/pricing')
    budget=CampaignBudget(tmp_path/'route-budget.json',price,validate_inputs=frozen.verify)
    observed=[]
    def factory(config):
        controller=SourceOnlyController()
        def answer(request):
            observed.append(request)
            if request.available_tools and request.available_tools[0].tool_id=='emit_graph_plan':
                payload=json.loads(request.user_input.split('INPUT_JSON\n')[1]);project=next(n for n in payload['nodes'] if n['kind']=='project')
                return ModelAction.tool(ToolCall('emit','emit_graph_plan',dict(applicable=True,as_of='',refusal_reasons=[],steps=[
                    dict(id='owner',input_node=project['node_id'],input_step='',edge_type='owner_of',direction='in',as_of=''),
                    dict(id='leader',input_node='',input_step='owner',edge_type='reports_to',direction='out',as_of='')],
                    outputs=[dict(step='owner',field='node'),dict(step='leader',field='node')])))
            return controller(request)
        class OfflineProvider:
            def complete(self,request):return ModelResponse(action=answer(request))
        return OfflineProvider()
    return TemporalGraphDevRoutes(frozen,budget,factory,tmp_path/'routes'),budget,observed


def test_routes_use_production_workers_and_same_unchanged_sources(tmp_path,monkeypatch):
    monkeypatch.setenv('LINKLOOM_RETRIEVAL_MODE','bm25')
    routes,budget,observed=setup_routes(tmp_path)
    public=dict(question='Who is the direct leader of the owner of Bay?',fixture_id='DEV',as_of='2026-04-01')
    graph=routes.graph_only(public);legacy=routes.legacy_only(public)
    assert graph['status']==legacy['status']=='FOUND',(graph,legacy)
    assert graph['values']==legacy['values']==['ada','bo']
    assert graph['relations']==legacy['relations']
    assert graph['evidence']==legacy['evidence']==['a.md','b.md']
    assert graph['verified_by']=='graph-receipt-reviewer' and legacy['verified_by']=='legacy-runtime-reviewer'
    assert len(budget.rows)>1 and all(r['status']=='COMPLETED' for r in budget.rows)
    assert all('required_evidence' not in r.user_input for r in observed)


def test_graph_route_restores_campaign_exhaustion_swallowed_by_planner(tmp_path):
    routes,budget,observed=setup_routes(tmp_path)
    budget.max_usd=.00000001
    with pytest.raises(BudgetExceeded):routes.graph_only(dict(question='负责人上级',fixture_id='DEV',as_of='2026-04-01'))
    assert not observed and budget.exhaustions==1
