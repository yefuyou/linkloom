"""Two-stage product regression; synthetic Provider calls only, never a live model."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.semantic_ingestion.candidate_models import SemanticExtractionResult
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import (
    ProductTaskFieldContext, SemanticExtractionContext, ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization, _prompt, _EXTRACTION_SCHEMA,
)
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.product_task_stages import TOOL_ID
from linkloom.semantic_ingestion.task_observation import PRODUCT_TASK_FIELDS_V2
from linkloom.semantic_ingestion.validation import CandidateValidator
from linkloom.tools.contracts import ToolCall
from tests.unit.test_product_task_fields import artifact, proposal, A, B
from tests.unit.test_task_observation_discovery import claim
from linkloom.semantic_ingestion.relations import FrozenRelationResolver


def core(text=A, *parts):
    return claim(text, *(parts or (text,)), owner='stage one incidental owner', deadline_text='10/20')


def annotations(text=A, *, owner='李明', token='10/12', ordinal=0, part=None):
    task = proposal(text, owner=owner, deadline=token, description=part or text, ordinal=ordinal)['task']
    return {'field_bindings': task['field_bindings'], 'title_binding': task['title_binding']}


class Stages:
    def __init__(self, rows, *fields, on_call=None):
        self.rows, self.fields, self.requests, self.on_call = rows, list(fields), [], on_call

    def complete(self, request):
        self.requests.append(request)
        if self.on_call: self.on_call(request)
        if request.available_tools[0].tool_id == TOOL_ID:
            value = self.fields.pop(0)
            if isinstance(value, Exception): raise value
        else:
            value = {'claims': deepcopy(self.rows)}
        return ModelResponse(action=ModelAction.tool(ToolCall('offline-stages', request.available_tools[0].tool_id, value)))


def execute(rows, fields=(), *, a=None, on_call=None, admission=None):
    a = a or artifact()
    context = SemanticExtractionContext(a.workspace_id, FrozenRelationResolver(('uses vendor',)),
        product_task_fields=ProductTaskFieldContext(a, current_source=admission))
    provider = Stages(rows, *fields, on_call=on_call)
    guards = []
    def guard(request):
        guards.append(request.to_dict())
        return ProviderRequestAuthorization.approve(request, guard_id='offline-stage-guard',
            reservation_id=str(len(guards)), estimated_cost_upper_bound_usd=0)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='offline-stages',
        request_guard=guard, request_guard_id='offline-stage-guard')
    segment = parse_source_text(a).segments[-1]
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    result = pipeline.process_segment_with_trace(segment, a, context)
    return a, context, provider, guards, pipeline, result


def test_stage_one_master_wire_unchanged_and_stage_two_per_task_once_with_raw_receipts():
    a = artifact(A + B, preceding=True)
    _, context, provider, guards, pipeline, result = execute([core(A), core(B)],
        [annotations(A, ordinal=1), annotations(B, owner='王芳', token='10/16', ordinal=1)], a=a)
    assert len(provider.requests) == len(guards) == 3
    first = provider.requests[0]
    old_context = SemanticExtractionContext(a.workspace_id, context.relation_resolver)
    segment = parse_source_text(a).segments[-1]
    assert first.user_input == _prompt(segment, old_context, 'semantic-extraction/v1')
    assert first.available_tools[0].input_schema == _EXTRACTION_SCHEMA
    assert first.generation_options.max_output_tokens == 4096
    assert first.generation_options.thinking_level == 'low' and first.generation_options.temperature is None
    for index, request in enumerate(provider.requests[1:]):
        assert request.agent_id == 'product-task-fields-stage2-v2'
        assert request.task_id.endswith('_c'+str(index))
        data = json.loads(request.user_input.split('Input:\n')[1])
        assert data['canonical_message']['message_ordinal'] == 1
        assert '本条仅提供' not in request.user_input
    observed = [r.candidate.task_observation for r in result.candidate_results]
    assert [(o.owner,o.deadline_text,o.deadline) for o in observed] == [('李明','10/12',None),('王芳','10/16',None)]
    assert all(o.grounding_policy_version == PRODUCT_TASK_FIELDS_V2 and o.verify(a) for o in observed)
    saved = result.extraction.to_dict()
    assert saved['record_version'] == 'semantic-extraction-result/v3'
    assert saved['task_stages']['claim_indices'] == [0,1]
    assert len(saved['task_stages']['stage_two']) == 2
    assert saved['task_stages']['raw_claims'][0]['task']['owner'] == 'stage one incidental owner'
    cold = SemanticExtractionResult.from_dict(saved)
    assert cold.to_dict() == saved
    replayed = pipeline.replay_extraction(saved, segment, a, context)
    assert replayed.candidate_results == result.candidate_results and len(provider.requests) == 3


def test_bad_task_claim_isolated_between_two_good_siblings_and_no_fabricated_observation():
    bad = core('bad'); bad['task']['description_parts'] = 'not-an-array'
    a = artifact(A+B)
    _, _, provider, _, _, result = execute([core(A), bad, core(B)],
        [annotations(A), annotations(B,owner='王芳',token='10/16')], a=a)
    assert len(provider.requests) == 3
    candidates = [r.candidate for r in result.candidate_results]
    assert [c.task_observation is None for c in candidates] == [False,True,False]
    assert result.extraction.task_stages['claim_indices'] == [0,1,2]
    assert result.extraction.task_stages['diagnostics'] == [{'claim_index':1,'issue':'TASK_CORE_FORMAT_INVALID'}]
    assert [s['claim_index'] for s in result.extraction.task_stages['stage_two']] == [0,2]


@pytest.mark.parametrize('fields', [TimeoutError('offline injected timeout'), {'extra_task_list': []}, []])
def test_field_stage_failures_keep_core_task_with_all_fields_unknown_and_title_fallback(fields):
    a, _, provider, _, _, result = execute([core()], [fields])
    o = result.candidate_results[0].candidate.task_observation
    assert o.description == A and o.owner is None and o.deadline is None and o.deadline_text is None
    assert o.title == A and o.title_needs_edit and 'FIELD_STAGE_FAILED' in o.validation_issues
    assert o.verify(a) and len(provider.requests) == 2
    assert SemanticExtractionResult.from_dict(result.extraction.to_dict()).claims[0].task.field_stage_issues == ('FIELD_STAGE_FAILED',)


def test_sparse_bindings_omissions_are_allowed_and_bad_sibling_field_or_title_is_local():
    payload = annotations()
    owner = payload['field_bindings'][0]
    payload['field_bindings'] = [owner, {'field':'deadline','value':[]}]
    payload['title_binding'] = {'text':'malformed'}
    a, _, _, _, _, result = execute([core()], [payload])
    o = result.candidate_results[0].candidate.task_observation
    assert o.owner == '李明' and o.deadline_text is None
    assert o.title == A and o.title_needs_edit
    assert {'TITLE_STAGE_INVALID','FIELD_STAGE_BINDING_INVALID'} <= set(o.validation_issues)
    assert o.verify(a)
    a, _, _, _, _, result = execute([core()], [{}])
    o = result.candidate_results[0].candidate.task_observation
    assert o.owner is None and o.title_needs_edit and 'FIELD_STAGE_FAILED' not in o.validation_issues


def test_wrong_literal_field_value_is_cleared_without_discarding_the_task():
    payload = annotations()
    payload['field_bindings'][0]['value'] = '伪造人'
    a, _, _, _, _, result = execute([core()], [payload])
    o = result.candidate_results[0].candidate.task_observation
    assert o.owner is None and o.deadline_text == '10/12' and 'OWNER_EVIDENCE_INVALID' in o.validation_issues
    assert o.verify(a)


@pytest.mark.parametrize('task', [None, {'text':'invented','description_parts': ['invented'],
    'owner':None,'deadline_text':None,'project':None,'vendor':None}])
def test_missing_or_invalid_core_keeps_only_semantic_claim_and_never_calls_fields(task):
    row=core(); row['task']=task
    _, _, provider, _, _, result = execute([row])
    assert result.candidate_results[0].candidate.task_observation is None
    assert result.extraction.task_stages['diagnostics'] and len(provider.requests) == 1


@pytest.mark.parametrize('unsafe', [{'workspace_id':'foreign'}, {'char_start':0}, {'quote':'fabricated'}])
def test_security_failures_remain_message_wide_even_when_a_sibling_task_is_good(unsafe):
    bad=core();bad['task'].update(unsafe)
    _, _, provider, _, _, result = execute([core(),bad])
    assert result.extraction.failure_code is not None and not result.extraction.claims
    assert len(provider.requests) == 1


def test_source_revocation_during_stage_two_blocks_further_requests_without_quality_fallback():
    admitted=[True]
    def changed(request):
        if request.available_tools[0].tool_id == TOOL_ID: admitted[0]=False
    with pytest.raises(PermissionError,match='revoked'):
        execute([core(A),core(B)], [annotations(A),annotations(B)], a=artifact(A+B),
            on_call=changed, admission=lambda:admitted[0])


def test_cold_record_detects_raw_proposal_or_field_stage_receipt_tampering():
    _, _, _, _, _, result = execute([core()], [annotations()])
    data=result.extraction.to_dict()
    data['task_stages']['raw_claims'][0]['task']['owner']='tampered'
    with pytest.raises(ValueError,match='audit hash'):
        SemanticExtractionResult.from_dict(data)


@pytest.mark.parametrize('unsafe', [
    {'workspace_id': 'foreign'}, {'source_ref': 'fabricated'},
    {'field_bindings': [{'field': 'owner', 'char_start': 0}]},
    {'title_binding': {'quote': 'fabricated'}},
])
def test_field_stage_cannot_forge_host_identity_or_provenance(unsafe):
    with pytest.raises(PermissionError, match='Host'):
        execute([core(A), core(B)], [unsafe, annotations(B)], a=artifact(A+B))


def test_resource_failure_keeps_all_core_tasks_and_stops_later_annotation_requests():
    from linkloom.semantic_ingestion.product_task_stages import AnnotationBudgetUnavailable
    a, _, provider, _, pipeline, result = execute([core(A), core(B)],
        [AnnotationBudgetUnavailable('offline exhausted reservation')], a=artifact(A+B))
    assert len(provider.requests) == 2
    observations = [r.candidate.task_observation for r in result.candidate_results]
    assert [o.raw_text for o in observations] == [A, B]
    assert all(o.owner is None and o.title_needs_edit and o.verify(a) for o in observations)
    assert all('FIELD_STAGE_FAILED' in o.validation_issues for o in observations)
    assert result.extraction.task_stages['stage_two'][1] == {
        'claim_index': 1, 'skipped': True, 'failure_code': 'FIELD_STAGE_FAILED'}
    assert SemanticExtractionResult.from_dict(result.extraction.to_dict()) == result.extraction


@pytest.mark.parametrize('stage', [1, 2])
def test_revocation_inside_request_guard_prevents_external_provider_call(stage):
    a = artifact()
    admitted = [True]
    context = SemanticExtractionContext(a.workspace_id, FrozenRelationResolver(('uses vendor',)),
        product_task_fields=ProductTaskFieldContext(a, current_source=lambda: admitted[0]))
    provider = Stages([core()], annotations())
    guards = []
    def guard(request):
        guards.append(request)
        if len(guards) == stage:
            admitted[0] = False
        return ProviderRequestAuthorization.approve(request, guard_id='guard',
            reservation_id=str(len(guards)), estimated_cost_upper_bound_usd=0)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='offline',
        request_guard=guard, request_guard_id='guard')
    with pytest.raises(PermissionError, match='revoked'):
        extractor.extract(parse_source_text(a).segments[0], context)
    assert len(provider.requests) == stage - 1


@pytest.mark.parametrize('error', [TypeError('bad permission object'), ValueError('bad guard state')])
def test_field_guard_internal_failure_is_security_failure_not_quality_fallback(error):
    a=artifact()
    context=SemanticExtractionContext(a.workspace_id, FrozenRelationResolver(('uses vendor',)),
        product_task_fields=ProductTaskFieldContext(a))
    provider=Stages([core(A),core(B)], annotations(A), annotations(B))
    def guard(request):
        if request.agent_id == 'product-task-fields-stage2-v2':
            raise error
        return ProviderRequestAuthorization.approve(request, guard_id='guard',
            reservation_id='one', estimated_cost_upper_bound_usd=0)
    extractor=ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='offline',
        request_guard=guard, request_guard_id='guard')
    with pytest.raises(PermissionError, match='authorization failed'):
        extractor.extract(parse_source_text(a).segments[0], context)
    assert len(provider.requests) == 1


def test_two_stage_receipt_cannot_drop_audit_and_masquerade_as_historical_record():
    _, _, _, _, _, result=execute([core()], [annotations()])
    data=result.extraction.to_dict()
    data.pop('task_stages')
    data['record_version']='semantic-extraction-result/v2'
    with pytest.raises(ValueError, match='complete stage audit'):
        SemanticExtractionResult.from_dict(data)


def test_cold_two_stage_record_requires_matching_host_policy_and_never_replays_as_v1():
    from linkloom.semantic_ingestion.task_observation import PRODUCT_TASK_FIELDS_VERSION
    a, context, provider, _, pipeline, result=execute([core()], [annotations()])
    old_context=replace(context,
        product_task_fields=ProductTaskFieldContext(a, version=PRODUCT_TASK_FIELDS_VERSION))
    replayed=pipeline.replay_extraction(result.extraction.to_dict(),
        parse_source_text(a).segments[0], a, old_context)
    assert all(r.candidate is None for r in replayed.candidate_results)
    assert len(provider.requests) == 2


def test_public_extractor_keeps_explicit_type_errors_before_profile_dispatch():
    a=artifact()
    context=SemanticExtractionContext(a.workspace_id, FrozenRelationResolver(('uses vendor',)))
    extractor=ProviderNeutralSemanticExtractor(Stages([]), provider_id='offline', model_id='offline',
        request_guard=lambda request: None, request_guard_id='guard')
    with pytest.raises(TypeError, match='parsed source segment'):
        extractor.extract(None, context)
    with pytest.raises(TypeError, match='SemanticExtractionContext'):
        extractor.extract(parse_source_text(a).segments[0], None)


def test_field_guard_budget_exhaustion_is_local_and_halts_annotation_inventory():
    from linkloom.semantic_ingestion.product_task_stages import AnnotationBudgetUnavailable
    a=artifact(A+B)
    context=SemanticExtractionContext(a.workspace_id, FrozenRelationResolver(('uses vendor',)),
        product_task_fields=ProductTaskFieldContext(a))
    provider=Stages([core(A),core(B)])
    def guard(request):
        if request.agent_id == 'product-task-fields-stage2-v2':
            raise AnnotationBudgetUnavailable('offline exhausted')
        return ProviderRequestAuthorization.approve(request, guard_id='guard',
            reservation_id='one', estimated_cost_upper_bound_usd=0)
    extractor=ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='offline',
        request_guard=guard, request_guard_id='guard')
    result=SemanticIngestionPipeline(extractor, CandidateValidator()).process_segment_with_trace(
        parse_source_text(a).segments[0], a, context)
    assert len(provider.requests) == 1
    assert len(result.candidate_results) == 2
    assert all(r.candidate.task_observation.verify(a) for r in result.candidate_results)
    assert all('FIELD_STAGE_FAILED' in r.candidate.task_observation.validation_issues
        for r in result.candidate_results)
