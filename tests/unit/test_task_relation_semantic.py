from copy import deepcopy
from dataclasses import replace
import json

import pytest

from linkloom.agents.model_adapter import ModelAction,ModelResponse,ModelUsage
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.tools.contracts import ToolCall
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticLimits
from linkloom.semantic_ingestion.task_relation_semantic_proposer import SemanticProposer
from linkloom.semantic_ingestion.task_relation_semantic_validation import validate_proposals,compare_proposed_revision
from tests.task_semantic_support import selected_context,public_cases,context_for,response_arguments


class FakeModel:
    def __init__(self,arguments):
        self.arguments=arguments
        self.requests=[]

    def complete(self,request):
        self.requests.append(request)
        return ModelResponse(action=ModelAction.tool(ToolCall('fake-call',request.available_tools[0].tool_id,
            deepcopy(self.arguments))),usage=ModelUsage(input_tokens=80,output_tokens=40,total_tokens=120),
            provider_metadata=dict(model='gemini-3.8-flash',model_version='gemini-3.8-flash'))


def proposer(fake):
    return SemanticProposer(fake,model_id='gemini-3.8-flash',request_guard=lambda r:
        ProviderRequestAuthorization.approve(r,guard_id='test',reservation_id='test-only',estimated_cost_upper_bound_usd=0),
        guard_id='test')


def run(context,arguments=None):
    fake=FakeModel(arguments or response_arguments(context))
    result=proposer(fake).propose(context)
    return result,fake


def test_real_quote_and_proposal_do_not_become_semantically_accepted():
    context=selected_context('independent')
    result,fake=run(context)  # deliberately wrong SAME_TASK SUPPORTED, real quotes
    assert result.status=='EXECUTED'
    assert len(fake.requests)==1 and fake.requests[0].generation_options.require_tool_call
    assert result.proposals[0].stage=='MODEL_PROPOSED'
    reports=validate_proposals(result,context)
    assert all(r.stage=='EVIDENCE_VALID' for r in reports)
    assert all(r.semantic_status=='NOT_EVALUATED' and not r.publishable for r in reports)


def test_llm_paraphrase_bypasses_old_revision_phrase_grammar():
    context=selected_context()
    result,_=run(context)
    reports=validate_proposals(result,context)
    assert all(r.stage=='EVIDENCE_VALID' for r in reports)
    comparison=compare_proposed_revision(result.proposals[1],result,context)
    assert comparison['status']=='COMPUTABLE' and comparison['delta_calendar_days']==4
    assert comparison['conditional_on_model_semantics'] and not comparison['publishable']


def test_fake_quote_stale_source_and_revocation_are_rejected():
    context=selected_context()
    args=response_arguments(context)
    args['proposals'][0]['supporting_evidence'][0]['quote']='fabricated quote'
    result,_=run(context,args)
    assert validate_proposals(result,context)[0].stage=='EVIDENCE_INVALID'
    result,_=run(context)
    revoked=replace(context,revoked_observation_ids=frozenset([context.observations[0].observation_id]))
    assert all(r.stage=='EVIDENCE_INVALID' for r in validate_proposals(result,revoked))
    span=context.observations[0].provenance
    context.source_registry.register_evidence(context.workspace_id,span.evidence_ref,content_hash='0'*64,source_version='updated')
    assert all(r.stage=='EVIDENCE_INVALID' for r in validate_proposals(result,context))


def test_admission_rejects_scope_budget_and_unauthorized_sources_before_call():
    context=selected_context()
    for broken in (replace(context,workspace_id='foreign'),
                   replace(context,authorized_artifact_versions=frozenset()),
                   replace(context,authorized_artifact_versions=frozenset(
                       [context.artifacts[0].artifact_version_id])),
                   replace(context,limits=SemanticLimits(max_input_tokens=1))):
        result,fake=run(broken)
        assert result.status=='BLOCKED' and not fake.requests


def test_unknown_handles_and_forged_assessment_fields_fail_closed():
    context=selected_context()
    for mutate in (lambda a:a['proposals'][0].update(subject='nonexistent'),
                   lambda a:a['proposals'][0].update(lifecycle='ACTIVE')):
        args=response_arguments(context)
        mutate(args)
        result,_=run(context,args)
        assert result.status=='OUTPUT_INVALID' and not result.proposals


def test_missing_year_does_not_use_event_time_and_conflict_stays_blocked():
    context=selected_context('missing-year')
    result,_=run(context)
    comparison=compare_proposed_revision(result.proposals[1],result,context)
    assert comparison['status']=='NOT_COMPARABLE' and comparison['delta_calendar_days'] is None
    context=selected_context('conflict')
    args=response_arguments(context)
    args['proposals'][1].update(proposed_verdict='CONFLICT',counter_evidence=deepcopy(args['proposals'][1]['supporting_evidence']),changed_fields=[])
    result,_=run(context,args)
    assert compare_proposed_revision(result.proposals[1],result,context)['status']=='BLOCKED'


def test_all_new_dev_inputs_are_verified_m1_and_never_read_gold():
    for case in public_cases():
        context=context_for(case)
        result,fake=run(context)
        assert result.status=='EXECUTED',case['case_id']
        prompt=fake.requests[0].user_input
        assert 'required_sources' not in prompt and 'rationale' not in prompt and '/gold/' not in prompt
        assert all(o.verify(next(a for a in context.artifacts if a.artifact_version_id==o.provenance.artifact_version_id))
                   for o in context.observations)


def test_incomplete_inventory_and_truncation_block_calls():
    for context in (replace(selected_context(),inventory_complete=False),replace(selected_context(),truncated=True),
                    replace(selected_context(),queries=())):
        result,fake=run(context)
        assert result.status=='BLOCKED' and not fake.requests


def test_overlapping_quote_occurrences_are_ambiguous():
    case=deepcopy(public_cases()[0])
    case['sources'][0]['content']=case['sources'][0]['content'].rstrip('\n')+' aaa\n'
    context=context_for(case)
    args=response_arguments(context)
    args['proposals'][0]['supporting_evidence'][0]['quote']='aa'
    result,_=run(context,args)
    report=validate_proposals(result,context)[0]
    assert report.stage=='EVIDENCE_INVALID'
    assert 'QUOTE_NOT_UNIQUE_EXACT_AUTHORIZED_SUBSTRING' in report.errors


def test_source_changes_during_call_fail_closed_without_retry():
    context=selected_context()
    class ChangingModel(FakeModel):
        def complete(self,request):
            response=super().complete(request)
            span=context.allowed_spans[0][1]
            context.source_registry.register_evidence(context.workspace_id,span.evidence_ref,
                content_hash='1'*64,source_version='next')
            return response
    fake=ChangingModel(response_arguments(context))
    result=proposer(fake).propose(context)
    assert result.status=='BLOCKED' and result.provider_calls==1 and len(fake.requests)==1


def test_source_claim_does_not_acquire_formal_authority_and_receipt_is_required():
    context=selected_context('causality')
    result,_=run(context)
    reports=validate_proposals(result,context)
    assert reports and all(r.authority=='SOURCE_CLAIM_ONLY' and not r.publishable for r in reports)
    handles=list(context.endpoints)
    from linkloom.semantic_ingestion.task_relation_models import ChangeRef
    handles=[(h,replace(ref,kind='REVIEWED_DECISION',decision_id='invented',candidate_id='invented',receipt_id='invented'))
             if isinstance(ref,ChangeRef) else (h,ref) for h,ref in handles]
    result,fake=run(replace(context,endpoints=tuple(handles)))
    assert result.status=='BLOCKED' and not fake.requests


def test_missing_output_is_not_interpreted_as_refuted_and_guard_binds_exact_request():
    context=selected_context()
    args=response_arguments(context)
    args['proposals'].pop()
    result,_=run(context,args)
    assert result.status=='OUTPUT_INVALID'
    fake=FakeModel(response_arguments(context))
    from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
    denied=SemanticProposer(fake,model_id='gemini-3.8-flash',guard_id='right',request_guard=lambda r:
        ProviderRequestAuthorization.approve(r,guard_id='wrong',reservation_id='test',estimated_cost_upper_bound_usd=0))
    assert denied.propose(context).status=='BLOCKED' and not fake.requests


def test_unicode_offsets_are_codepoints_and_quotes_are_not_normalized():
    context=selected_context()
    result,_=run(context)
    for report in validate_proposals(result,context):
        for quote in report.supporting_evidence:
            artifact=next(a for a in context.artifacts if a.artifact_version_id==quote.span.artifact_version_id)
            assert artifact.content[quote.char_start:quote.char_end]==quote.quote
    args=response_arguments(context)
    args['proposals'][0]['supporting_evidence'][0]['quote']=' '+args['proposals'][0]['supporting_evidence'][0]['quote']
    result,_=run(context,args)
    assert validate_proposals(result,context)[0].stage=='EVIDENCE_INVALID'


def test_existing_gemini_adapter_accepts_new_schema_without_tool_execution():
    from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
    from linkloom.semantic_ingestion.task_relation_semantic_proposer import TOOL_ID
    context=selected_context()
    class SDK:
        def __init__(self):
            self.requests=[]
        def generate_content(self,**request):
            self.requests.append(request)
            return dict(function_calls=[dict(id='emit-1',name=TOOL_ID,args=response_arguments(context))],
                finish_reason='STOP',model_version='gemini-3.8-flash',
                usage_metadata=dict(prompt_token_count=100,candidates_token_count=50,total_token_count=150))
    sdk=SDK()
    result=proposer(GeminiProviderAdapter(sdk)).propose(context)
    assert result.status=='EXECUTED',result.to_dict()
    assert result.proposals[0].actual_model_version=='gemini-3.8-flash'
    assert len(sdk.requests)==1 and sdk.requests[0]['model']=='gemini-3.8-flash'
    assert not GeminiProviderAdapter(sdk).capability.provider_executes_tools


def test_rejected_tool_and_final_outputs_remain_auditable():
    context=selected_context()
    class WrongTool(FakeModel):
        def complete(self,request):
            return ModelResponse(action=ModelAction.tool(ToolCall('wrong','other_tool',dict(unexpected='actual output'))))
    result=proposer(WrongTool({})).propose(context)
    assert result.status=='OUTPUT_INVALID'
    assert result.untrusted_output['tool_calls'][0]==dict(tool_id='other_tool',arguments=dict(unexpected='actual output'))
    class FinalText(FakeModel):
        def complete(self,request):
            return ModelResponse(action=ModelAction.final('public unstructured model output'))
    result=proposer(FinalText({})).propose(context)
    assert result.status=='OUTPUT_INVALID'
    assert result.untrusted_output['final_answer']=='public unstructured model output'
