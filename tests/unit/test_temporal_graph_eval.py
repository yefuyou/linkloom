"""Harness unit checks use synthetic structured rows, not the frozen QA campaign."""
from datetime import UTC,datetime,timedelta,timezone
from pathlib import Path
import json
import pytest
from run_temporal_graph_dev import FrozenDevInputs,CampaignBudget,PriceReference,BudgetExceeded,run_matrix
from linkloom.agents.model_adapter import ModelAction,ModelResponse,ModelUsage,ModelGenerationOptions,ModelTurnRequest


def inputs(tmp_path):
    questions=tmp_path/'questions.json';expected=tmp_path/'expected.json';frozen=tmp_path/'freeze.json'
    cases=[]
    for i in range(1,14):
        template=f'T{i:02d}';parent='A-'+template
        cases.append(dict(id=parent,group='A',template_id=template,question='original '+template,fixture_id=template))
        for j in range(2):cases.append(dict(id=f'B-{template}-{j}',group='B',template_id=template,parent_id=parent,
                                          question=f'variant {template} {j}',fixture_id=template))
    for i in range(12):cases.append(dict(id=f'C-{i}',group='C',question=f'combination {i}',fixture_id='combo'))
    questions.write_text(json.dumps(dict(split='dev',authoring='外部起草，人工确认',cases=cases)),encoding='utf8')
    expected.write_text(json.dumps(dict(split='dev',cases={r['id']:dict(status='FOUND',values=['value'],relations=[],required_evidence=['source']) for r in cases})),encoding='utf8')
    deps={name:tmp_path/(name+'.json') for name in ('public_fixture','confirmed_database','configuration')}
    for path in deps.values():path.write_text('{}',encoding='utf8')
    result=FrozenDevInputs(questions,expected,frozen,**deps);result.create();return result


def price():
    today=datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    return PriceReference('fake',1,1,today,'https://ai.google.dev/gemini-api/docs/pricing')


def request():
    return ModelTurnRequest(run_id='run',turn_id='turn',task_id='task',agent_id='graph-worker',sequence=1,
       user_input='bounded input',observation=None,available_tools=[],model_id='fake',
       generation_options=ModelGenerationOptions(max_output_tokens=16))


def test_freeze_is_raw_bytes_read_only_and_rejects_changes(tmp_path):
    frozen=inputs(tmp_path);raw=frozen.questions.read_bytes();frozen.verify()
    assert frozen.questions.read_bytes()==raw
    with pytest.raises(ValueError,match='ALREADY'):frozen.create()
    frozen.questions.write_bytes(raw+b'\n')
    with pytest.raises(ValueError,match='CHANGED'):frozen.verify()


def test_harness_sends_public_fields_only_runs_three_times_and_groups_reports(tmp_path):
    frozen=inputs(tmp_path);seen=[]
    def route(public):
        assert set(public)=={'question','fixture_id'}
        assert all(k not in public for k in ('expected','gold','id','group'))
        seen.append(public)
        return dict(status='FOUND',values=['value'],relations=[],evidence=['source'])
    report=run_matrix(frozen,dict(legacy_only=route,graph_only=route),tmp_path/'results')
    assert len(seen)==306 and report['completed_executions']==306
    assert len(report['aggregate'])==6 and all(r['repetitions']==3 for r in report['aggregate'])
    assert all(r['metrics']['accuracy']==dict(mean=1,min=1,max=1) for r in report['aggregate'])
    assert report['cost_reserved_usd']==0


def test_reservation_precedes_calls_includes_unknown_failure_and_hard_cap(tmp_path):
    frozen=inputs(tmp_path)
    with pytest.raises(ValueError):CampaignBudget(tmp_path/'bad-budget',price(),validate_inputs=frozen.verify,max_usd=1.01)
    budget=CampaignBudget(tmp_path/'budget.json',price(),validate_inputs=frozen.verify,max_usd=.01)
    class Failed:
        calls=0
        def complete(self,request):self.calls+=1;raise RuntimeError('uncertain provider outcome')
    inner=Failed();wrapped=budget.wrap(inner,max_input_bytes=8192,max_output_tokens=16)
    with pytest.raises(BudgetExceeded,match='PREFLIGHT'):wrapped.complete(request())
    budget.preflight(1,1)
    with pytest.raises(RuntimeError):wrapped.complete(request())
    assert inner.calls==1 and budget.rows[0]['status']=='UNCERTAIN' and budget.reserved>0
    with pytest.raises(BudgetExceeded,match='EXHAUSTED'):wrapped.complete(request())
    assert inner.calls==1
    with pytest.raises(FileExistsError):CampaignBudget(tmp_path/'budget.json',price(),validate_inputs=frozen.verify)


def test_budget_refuses_unaffordable_complete_matrix_before_any_provider(tmp_path):
    frozen=inputs(tmp_path);budget=CampaignBudget(tmp_path/'budget.json',price(),validate_inputs=frozen.verify)
    with pytest.raises(BudgetExceeded,match='80_PERCENT'):budget.preflight(1_000_000,1_000_000)
    assert budget.reserved==0 and budget.rows==[]


def test_dataset_tamper_during_execution_stops_without_repairing_file(tmp_path):
    frozen=inputs(tmp_path)
    def tamper(public):
        frozen.expected.write_text('{}',encoding='utf8')
        return dict(status='FOUND',values=[],relations=[],evidence=[])
    with pytest.raises(ValueError,match='CHANGED'):
        run_matrix(frozen,dict(legacy_only=tamper,graph_only=tamper),tmp_path/'results')
    assert frozen.expected.read_text(encoding='utf8')=='{}'


def test_total_tokens_cannot_hide_billable_thinking_output(tmp_path):
    frozen=inputs(tmp_path);budget=CampaignBudget(tmp_path/'budget.json',price(),validate_inputs=frozen.verify)
    budget.preflight(100,100)
    class Thinking:
        calls=0
        def complete(self,request):
            self.calls+=1
            return ModelResponse(action=ModelAction.final('ok'),usage=ModelUsage(input_tokens=1,output_tokens=1,total_tokens=1_000_000))
    inner=Thinking();wrapped=budget.wrap(inner,max_input_bytes=8192,max_output_tokens=16)
    with pytest.raises(BudgetExceeded,match='EXCEEDS'):wrapped.complete(request())
    assert budget.rows[0]['additional_output_tokens']==999998
    assert budget.rows[0]['status']=='USAGE_EXCEEDS_RESERVATION'
    with pytest.raises(BudgetExceeded,match='STOPPED'):wrapped.complete(request())
    assert inner.calls==1


def test_freeze_includes_fixture_confirmation_configuration_and_source_schema(tmp_path):
    frozen=inputs(tmp_path);data=frozen.verify()
    assert all(key in data['dependencies'] for key in ('public_fixture','confirmed_database','configuration'))
    assert 'code:src/linkloom/temporal_graph/planning.py' in data['dependencies']
    assert 'code:src/linkloom/temporal_graph/models.py' in data['dependencies']
    frozen.dependencies['public_fixture'].write_text('{"changed":true}',encoding='utf8')
    with pytest.raises(ValueError,match='DEPENDENCY_CHANGED'):frozen.verify()


def test_frozen_configuration_cannot_be_replaced_and_wal_cannot_evade_hash(tmp_path):
    frozen=inputs(tmp_path)
    route=lambda public:dict(status='FOUND',values=['value'],relations=[],evidence=['source'])
    with pytest.raises(ValueError,match='CONFIGURATION_MISMATCH'):
        run_matrix(frozen,dict(legacy_only=route,graph_only=route),tmp_path/'results',config={'different':True})
    wal=Path(str(frozen.dependencies['confirmed_database'])+'-wal');wal.write_bytes(b'changed')
    with pytest.raises(ValueError,match='CHECKPOINTED'):frozen.verify()


def test_correct_answer_requires_the_whole_expected_relation_chain():
    from run_temporal_graph_dev import _score
    expected=dict(status='FOUND',values=['bo'],relations=[dict(subject='bay',edge_type='owner_of',target='ada'),
        dict(subject='ada',edge_type='reports_to',target='bo')],required_evidence=['first','second'])
    actual=dict(status='FOUND',values=['bo'],relations=[],evidence=['first','second'])
    assert _score(actual,expected)==dict(correct=False,error_relations=0,refused=False,answerable=True)


def test_official_counter_cap_is_reserved_and_excess_stops_before_transport(tmp_path):
    frozen=inputs(tmp_path);budget=CampaignBudget(tmp_path/'token-budget.json',price(),validate_inputs=frozen.verify)
    budget.preflight(1,1)
    class Inner:
        calls=0
        def complete(self,request):
            self.calls+=1
            return ModelResponse(action=ModelAction.final('ok'),usage=ModelUsage(input_tokens=30,output_tokens=2))
    inner=Inner();wrapped=budget.wrap(inner,max_input_bytes=8192,max_output_tokens=16,max_input_tokens=32,token_counter=lambda r:31)
    wrapped.complete(request())
    assert inner.calls==1 and budget.rows[0]['input_token_cap']==32
    assert budget.rows[0]['reserved_usd']==pytest.approx(48/1e6)
    limited=budget.wrap(inner,max_input_bytes=8192,max_output_tokens=16,max_input_tokens=32,token_counter=lambda r:33)
    with pytest.raises(BudgetExceeded,match='TOKEN_COUNT'):limited.complete(request())
    assert inner.calls==1
