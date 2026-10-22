from dataclasses import replace
import pytest
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_field_binding import TaskFieldBinding, FIELD_ROLES
from linkloom.semantic_ingestion.task_observation import TaskProposal, TaskObservation, build_task_observation


def grounded(text, description, *, speaker='记录员', values=None, changes=None):
    values = values or {}
    artifact = RawArtifact('field-ws', 'meeting', 'timestamped_text',
        f'[2032-01-01T09:00:00Z] {speaker}: '+text)
    segment = parse_source_text(artifact).segments[0]
    bindings = []
    for name, role in FIELD_ROLES.items():
        value = values.get(name)
        data = dict(field=name, value=value, role=role if value else 'UNKNOWN',
                    state='ASSERTED' if value else 'NOT_STATED', quote=text if value else '',
                    task_quote=description, basis='SOURCE_TEXT', message_ordinal=0)
        data.update((changes or {}).get(name, {}))
        bindings.append(TaskFieldBinding(**data))
    proposal = TaskProposal(text, (description,), owner=values.get('owner'),
        deadline_text=values.get('deadline'), project=values.get('project'), vendor=values.get('vendor'),
        field_bindings=tuple(bindings), work_state='ASSERTED')
    return artifact, segment, proposal, build_task_observation(proposal, segment, artifact)


def test_roles_use_model_witnesses_without_legacy_phrase_grammar():
    text = '澄芽归属的导出封装还是由乔雨接着把关，交付落在2032年3月18日；云巢是这项工作的供货方。'
    a, s, p, o = grounded(text, '导出封装', values=dict(owner='乔雨', deadline='2032年3月18日', project='澄芽', vendor='云巢'))
    assert (o.owner, o.deadline, o.project, o.vendor) == ('乔雨', '2032-03-18', '澄芽', '云巢')
    assert o.authority == 'SUPPORTING_ONLY' and o.verify(a)
    restored = TaskObservation.from_dict(o.to_dict())
    assert restored == o and restored.verify(a)
    legacy = build_task_observation(replace(p, field_bindings=None, work_state=None), s, a)
    assert legacy.owner is None and legacy.deadline is None
    assert 'field_bindings' not in legacy.to_dict()


@pytest.mark.parametrize('state', ['AMBIGUOUS', 'CONDITIONAL', 'NEGATED', 'HISTORICAL', 'REPORTED', 'WITHDRAWN'])
def test_noncurrent_or_uncertain_roles_remain_unknown(state):
    a, _, _, o = grounded('乔雨处理导出封装，2032年3月18日有待讨论。', '导出封装',
        values=dict(owner='乔雨', deadline='2032年3月18日'), changes={n:dict(state=state) for n in ('owner','deadline')})
    assert o.owner is None and o.deadline is None and o.deadline_role == 'UNKNOWN'
    assert o.verify(a)


@pytest.mark.parametrize('change', [dict(quote='乔雨'), dict(quote='不存在的引文'),
    dict(task_quote='别的任务'), dict(message_ordinal=1), dict(role='HISTORICAL_OWNER')])
def test_name_presence_or_wrong_task_message_or_role_is_not_enough(change):
    a, _, _, o = grounded('乔雨接着处理导出封装。', '导出封装', values=dict(owner='乔雨'), changes={'owner':change})
    assert o.owner is None and o.verify(a)


def test_speaker_basis_requires_real_header_and_task_role_quote():
    a, _, _, o = grounded('这份导出封装由我接下来交付。', '导出封装', speaker='乔雨',
        values=dict(owner='乔雨'), changes={'owner':dict(basis='SPEAKER')})
    assert o.owner == '乔雨' and o.verify(a)
    assert {e.basis for e in o.field_evidence if e.field=='owner'} == {'SOURCE_TEXT','SPEAKER'}
    a, _, _, o = grounded('今天我只是旁听导出封装的汇报。', '导出封装', speaker='乔雨',
        values=dict(owner='乔雨'), changes={'owner':dict(basis='SPEAKER', state='NOT_STATED')})
    assert o.owner is None and o.verify(a)
    a, s, p, _ = grounded('这份导出封装由我接下来交付。', '导出封装', speaker='乔雨', values=dict(owner='伪造姓名'), changes={'owner':dict(basis='SPEAKER')})
    assert build_task_observation(p, replace(s,speaker='伪造姓名'), a).owner is None


@pytest.mark.parametrize('token,precision,value', [('3月18日','MONTH_DAY',None),('2032年3月','MONTH',None),('2032-03-18','DAY','2032-03-18')])
def test_date_precision_is_structural_and_never_fills_year(token, precision, value):
    a,_,_,o=grounded('导出封装的交付落在'+token+'。','导出封装',values=dict(deadline=token))
    assert o.deadline_precision==precision and o.deadline==value and o.deadline_role=='TASK_DEADLINE'
    assert o.verify(a)


def test_meeting_date_role_and_invalid_calendar_are_not_deadlines():
    for value, change in [('2032-03-18',dict(role='MEETING_DATE')),('2032-02-30',{})]:
        a,_,_,o=grounded('导出封装资料提到'+value+'。','导出封装',values=dict(deadline=value),changes={'deadline':change})
        assert o.deadline is None and o.deadline_role=='UNKNOWN' and o.verify(a)


def test_separate_role_and_work_witnesses_bind_within_one_task_message():
    text='导出封装还是乔雨接着把关；松桥是这项工作的供货方。'
    a,_,_,o=grounded(text,'导出封装',values=dict(vendor='松桥'),
        changes={'vendor':dict(quote='松桥是这项工作的供货方')})
    assert o.vendor=='松桥' and o.verify(a)
    assert next(e for e in o.field_evidence if e.field=='vendor').quote=='松桥是这项工作的供货方'


def test_first_person_and_deadline_role_clauses_need_not_repeat_work():
    text='迁移演练这件事我接下了，4月16日收尾。'
    a,_,_,o=grounded(text,'迁移演练',speaker='简宁',values=dict(owner='简宁',deadline='4月16日'),
        changes=dict(owner=dict(quote='这件事我接下了',basis='SPEAKER'),
                     deadline=dict(quote='4月16日收尾')))
    assert o.owner=='简宁' and o.deadline_text=='4月16日'
    assert o.deadline is None and o.deadline_precision=='MONTH_DAY' and o.verify(a)


def test_tampered_field_or_source_does_not_verify():
    a,_,_,o=grounded('乔雨接着处理导出封装。','导出封装',values=dict(owner='乔雨'))
    assert not replace(o,owner='其他人').verify(a)
    assert not o.verify(replace(a,content=a.content+'来源变化'))


def test_new_discovery_contract_preserves_bindings_and_legacy_wire_shape():
    from tests.unit.test_task_observation_discovery import setup, claim
    from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
    a,_,p,_=grounded('以前秦鸣经手导出封装；这一轮乔雨接手导出封装验收，2032-03-18交齐导出封装验收。',
        '导出封装验收',values=dict(owner='乔雨'))
    # Select an unambiguous role witness containing the work and value once.
    binding=next(b for b in p.field_bindings if b.field=='owner')
    p=replace(p,field_bindings=tuple(replace(b,quote='这一轮乔雨接手导出封装验收，2032-03-18交齐导出封装验收。',task_quote='导出封装验收') if b is binding else b for b in p.field_bindings))
    row=claim(p.text,*p.description_parts,owner=p.owner)
    row['task']=p.to_dict()
    service,provider,_,_=setup(a,[row])
    service.task_profile=TaskDiscoveryProfile.FIELD_GROUNDED_V2
    result=service.discover((a,),workspace_id=a.workspace_id)
    # Duplicate work witness is rejected at the field level, not lost on projection.
    obs=(*result.valid_observations,*result.partial_observations)[0]
    assert obs.field_bindings==p.field_bindings and obs.owner is None and obs.verify(a)
    request=provider.requests[0]
    task_schema=request.available_tools[0].input_schema['properties']['claims']['items']['properties']['task']
    assert 'field_bindings' in task_schema['required']
    assert 'containing both task_quote' not in request.user_input
    assert request.generation_options.max_output_tokens==service.extractor.generation_options.max_output_tokens


def test_new_discovery_never_silently_falls_back_to_legacy_roles():
    from tests.unit.test_task_observation_discovery import setup, claim
    from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
    a,_,_,_=grounded('乔雨负责导出封装。','导出封装')
    service,provider,_,_=setup(a,[claim('乔雨负责导出封装。','导出封装',owner='乔雨')])
    service.task_profile=TaskDiscoveryProfile.FIELD_GROUNDED_V2
    result=service.discover((a,),workspace_id=a.workspace_id)
    assert len(provider.requests)==1 and not result.valid_observations and not result.partial_observations
    assert result.failures


def test_legacy_profile_rejects_injected_role_bindings_even_if_asserted():
    from types import SimpleNamespace
    from tests.unit.test_task_observation_discovery import setup, claim
    from linkloom.semantic_ingestion.task_observation_discovery import _project
    a,_,p,_=grounded('导出封装已经取消，不再安排乔雨执行。','导出封装',values=dict(owner='乔雨'))
    row=claim(p.text,*p.description_parts,owner=p.owner)
    row['task']=p.to_dict()  # Maliciously asserts work_state despite cancellation.
    service,provider,_,_=setup(a,[row])
    result=service.discover((a,),workspace_id=a.workspace_id)
    assert len(provider.requests)==1 and result.failures
    assert not result.valid_observations and not result.partial_observations
    # Projection also rejects injected bindings if a caller bypasses parsing.
    parsed=SimpleNamespace(claim_type=SimpleNamespace(value='PLAN'),task=p)
    found,_,failures=_project(SimpleNamespace(claims=(parsed,)),a,parse_source_text(a).segments)
    assert not found and failures[0].reason=='TASK_PROFILE_MISMATCH'


def test_message_ordinal_256_is_valid_within_bounded_source():
    from linkloom.semantic_ingestion.task_field_binding import binding_schema
    lines=[f'[2032-01-01T09:00:00Z] 记录员: 事项{i}。' for i in range(256)]
    text='乔雨接着处理导出封装。'
    a,_,p,_=grounded(text,'导出封装',values=dict(owner='乔雨'))
    a=replace(a,content='\n'.join(lines+[a.content]))
    assert len(a.content)<16000
    s=parse_source_text(a).segments[256]
    p=replace(p,field_bindings=tuple(replace(b,message_ordinal=256) for b in p.field_bindings))
    o=build_task_observation(p,s,a)
    assert o.owner=='乔雨' and o.verify(a)
    assert binding_schema()['items']['properties']['message_ordinal']['maximum']>=256


def test_field_profile_honors_explicit_extractor_output_bound():
    from tests.unit.test_task_observation_discovery import setup, claim
    from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
    a,_,p,_=grounded('乔雨接着处理导出封装。','导出封装',values=dict(owner='乔雨'))
    row=claim(p.text,*p.description_parts,owner=p.owner);row['task']=p.to_dict()
    service,provider,_,_=setup(a,[row])
    service.task_profile=TaskDiscoveryProfile.FIELD_GROUNDED_V2
    service.extractor.generation_options=replace(service.extractor.generation_options,max_output_tokens=512)
    service.discover((a,),workspace_id=a.workspace_id)
    assert provider.requests[0].generation_options.max_output_tokens==512
