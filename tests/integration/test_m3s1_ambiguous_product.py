"""OFFLINE normal Product HTTP, recorded F/initial G and scripted bounded replan."""
import json
from dataclasses import replace
from types import SimpleNamespace
import pytest
from linkloom.agents.model_adapter import ModelAction,ModelResponse,ModelUsage
from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_provider import compose_task_provider
from tests.m3p_support import product_http,cold_read
from tests.m3s1_support import SOURCE,QUESTION,saved_attempt,saved_arguments
from tests.integration.test_m3q_provider_host import ScriptedGeminiTransport,poll
from tests.integration.test_product_application_flow import _client
from tests.unit.test_m3q_provider_budget import limits
from tests.unit.test_task_relation_question_planner import step,arguments

class RecordedStage:
    def __init__(self,stage,repeat_discover=False,only_one=False):
        self.stage,self.requests,self.repeat_discover,self.only_one=stage,[],repeat_discover,only_one
    def complete(self,request):
        self.requests.append(request)
        if self.stage=='discovery':
            # Stored neutral claims use different wire property names. Preserve
            # every candidate value while re-encoding the Provider tool schema.
            claims=[]
            for stored in saved_attempt()['discovery']['extractions'][0]['claims']:
                row=dict(stored)
                for old,new in [('relation_phrase','relation'),('valid_from_raw','valid_from'),('valid_to_raw','valid_to')]:
                    row[new]=row.pop(old)
                claims.append(row)
            data=dict(claims=claims[:1] if self.only_one else claims)
        elif self.stage=='planning':
            payload=json.loads(request.user_input.split('INPUT_JSON\n',1)[1])
            if payload['replan']['previous_plan'] is None:
                data=(arguments(step('initial_read',handles=('task_0',)),
                    dict(step('missing','discover',()),target_kind='task',missing_entities=['Second task observation'],
                         evidence_gap='Only one observation was returned; absence is not established.'))
                      if self.only_one else saved_arguments())
            elif self.repeat_discover:
                data=arguments(dict(step('again','discover',()),target_kind='change',
                    missing_entities=['another missing record'],evidence_gap='Still missing'))
            else:
                rows=[]
                for i in range(1 if self.only_one else 2):
                    for field in ('owner','deadline'):
                        rows.append(dict(step(f'read_{i}_{field}',handles=(f'task_{i}',)),field=field,
                            evidence_gap='The admitted task field remains uninspected by Runtime.'))
                rows.append(dict(step('ask_missing','clarify',()),missing_entities=['Final assignment and delivery decision']))
                data=arguments(*rows)
        else:
            raise AssertionError('Field-only plan should not request SemanticAnalysis')
        return ModelResponse(action=ModelAction.tool(ToolCall('recorded',request.available_tools[0].tool_id,data)),
            usage=ModelUsage(input_tokens=20,output_tokens=10,total_tokens=30))

def ambiguous_product(root,repeat_discover=False,only_one=False):
    app=ProductApplication(database_path=root/'product.sqlite',extractor=None)
    stages=[RecordedStage(s,repeat_discover,only_one) for s in ('discovery','planning','semantics')]
    transport=ScriptedGeminiTransport(stages)
    budget=replace(limits(),max_requests=10,max_input_tokens=500000,max_output_tokens=65536,max_cost_usd=3,
        max_seconds=90,request_timeout_seconds=10)
    return app,compose_task_provider(app,GeminiProviderAdapter(transport),budget),transport,stages

def start(base):
    _,b=_client(base,'/api/workspaces',method='POST',data=dict(name='Original M3-S ambiguous source'))
    ws=b['workspace']['workspace_id'];_client(base,'/api/workspaces/current',method='POST',data=dict(workspace_id=ws))
    _client(base,'/api/sources/import',method='POST',data=dict(name='ambiguous-meetings.txt',content=SOURCE))
    _,config=_client(base,'/api/product/ask/config')
    _,body=_client(base,'/api/product/ask',method='POST',data=dict(query=QUESTION,workspace_id=ws,
        allow_external_provider=True,investigation_context=dict(domain='tasks',snapshot_hash=config['investigation_context']['snapshot_hash'])))
    return ws,poll(base,ws,body['result'])

@pytest.mark.parametrize('only_one',[False,True])
def test_original_ambiguous_unknown_survives_replan_runtime_reviewer_and_cold_read(tmp_path,only_one):
    app,service,transport,stages=ambiguous_product(tmp_path,only_one=only_one)
    try:
        with product_http(app,service) as base:
            ws,result=start(base)
            assert result['status']=='PARTIAL',result
            assert result['reviewer']['accepted']
            view=result['task_review'];assert len(view['observations'])==(1 if only_one else 2)
            assert all(o['owner'] is None and o['deadline_text'] is None and o['deadline_role']=='UNKNOWN' for o in view['observations'])
            assert result['evidence'] and all(e['verified'] for e in result['evidence'])
            assert [r['tool'] for r in view['executed_tools']]==['inspect_task_context','query_task_relations']
            discover=next(s for s in view['steps'] if s['kind']=='discover')
            assert discover['status']=='UNKNOWN'
            assert view['unresolved'] and not result['conclusions']
            assert len(stages[0].requests)==1 and len(stages[1].requests)==2
            count=len(transport.requests)
            code,cold=cold_read(app.repository.database_path,result['agent_run_id'],ws)
            assert code==200 and cold['result']['status']=='PARTIAL',cold
            assert cold['result']['task_review']['observations']==view['observations']
            assert count==len(transport.requests)
    finally:app.close()

def test_host_rejects_repeated_discovery_after_unknown_without_extra_provider_loop(tmp_path):
    app,service,transport,stages=ambiguous_product(tmp_path,True)
    try:
        with product_http(app,service) as base:
            _,result=start(base)
            assert result['status']=='BLOCKED',result
            assert len(stages[0].requests)==1 and len(stages[1].requests)==2
            assert len(transport.requests)==3 and not stages[2].requests
            assert not result['evidence'] and not result.get('task_review')
    finally:app.close()
