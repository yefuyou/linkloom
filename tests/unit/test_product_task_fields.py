"""Product field declarations; all Providers and sources below are synthetic/offline."""
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
import json

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.candidate_persistence import deserialize_candidate, serialize_candidate
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import (
    ProductTaskFieldContext, SemanticExtractionContext, ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization, _EXTRACTION_SCHEMA_SHA256,
)
from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.semantic_ingestion.task_field_binding import FIELD_ROLES
from linkloom.semantic_ingestion.task_observation import (
    TaskProposal, TaskObservation, build_task_observation, PRODUCT_TASK_FIELDS_VERSION,
)
from linkloom.semantic_ingestion.validation import CandidateValidator
from tests.unit.test_task_observation_discovery import FakeModel, claim


A = '联调报告由李明负责，10/12 前完成。'
B = '联调报告改由王芳负责，延期到 10/16。'


def artifact(text=A, *, preceding=False):
    content = '[2032-01-01T09:00:00Z] 记录人: ' + text
    if preceding:
        content = '[2032-01-01T08:00:00Z] 其他: 本条仅提供另一个编号。\n' + content
    return RawArtifact('product-field-ws', 'minutes.txt', 'timestamped_text', content,
                       ingestion_time=datetime(2032, 1, 2, tzinfo=UTC))


def proposal(text=A, *, owner='李明', deadline='10/12', description=None,
             title=None, obj='联调报告', action='负责', ordinal=0):
    part = description or text
    row = claim(text, part, owner=owner, deadline_text=deadline)
    row['task'].update(grounding_policy_version=PRODUCT_TASK_FIELDS_VERSION,
        work_state='ASSERTED', field_bindings=[dict(field=name, value=value,
            role=FIELD_ROLES[name] if value else 'UNKNOWN', state='ASSERTED' if value else 'NOT_STATED',
            quote=text if value else '', task_quote=part, basis='SOURCE_TEXT', message_ordinal=ordinal)
            for name, value in [('owner', owner), ('deadline', deadline), ('project', None), ('vendor', None)]],
        title_binding=dict(text=title or text, state='COMPLETE', object_quote=obj, action_quote=action))
    return row


def run(rows, a=None, *, product=True, segment_index=-1):
    a = a or artifact()
    context = SemanticExtractionContext(a.workspace_id, FrozenRelationResolver(('uses vendor',)),
        product_task_fields=ProductTaskFieldContext(a, version=PRODUCT_TASK_FIELDS_VERSION) if product else None)
    provider = FakeModel({'claims': deepcopy(rows)})
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='field-test',
        request_guard=lambda request: ProviderRequestAuthorization.approve(request,
            guard_id='offline-only', reservation_id='one', estimated_cost_upper_bound_usd=0),
        request_guard_id='offline-only', max_attempts=1)
    segment = parse_source_text(a).segments[segment_index]
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    result = pipeline.process_segment_with_trace(segment, a, context)
    return a, context, provider, extractor, result


@pytest.mark.parametrize('text,owner,token', [(A, '李明', '10/12'), (B, '王芳', '10/16')])
def test_original_sentences_keep_declared_fields_without_phrase_rules_or_inferred_year(text, owner, token):
    a, _, provider, _, result = run([proposal(text, owner=owner, deadline=token)], artifact(text))
    o = result.candidate_results[0].candidate.task_observation
    assert o.owner == owner and o.deadline_text == token
    assert o.deadline is None and o.deadline_precision == 'MONTH_DAY'
    assert o.deadline_role == 'TASK_DEADLINE' and 'DEADLINE_YEAR_MISSING' in o.validation_issues
    assert o.observation_id.startswith('task_observation_v2_') and o.verify(a)
    assert o.title == text and o.title_needs_edit is False
    assert o.authority == 'SUPPORTING_ONLY' and o.relation_status == 'UNCONFIRMED'
    assert len(provider.requests) == 1
    assert all(a.content[e.char_start:e.char_end] == e.quote for e in o.field_evidence)
    saved = TaskObservation.from_dict(o.to_dict())
    assert saved == o and saved.verify(a)


def test_mixed_profile_preserves_decision_extraction_and_canonical_nonzero_ordinal():
    text = A + ' We decided to use Vendor A for launch.'
    a = artifact(text, preceding=True)
    decision = dict(claim_type='DECISION', subject='launch', relation='uses vendor', value='Vendor A',
        temporal_status='SOURCE_EVENT_TIME', valid_from=None, valid_to=None,
        confidence=dict.fromkeys(('claim_type', 'entity', 'relation', 'temporal', 'overall'), .95), task=None)
    _, context, provider, extractor, result = run([proposal(ordinal=1), decision], a)
    candidates = [r.candidate for r in result.candidate_results]
    assert candidates[0].task_observation.owner == '李明'
    assert candidates[1].claim_type.value == 'DECISION'
    assert candidates[1].valid_from == parse_source_text(a).segments[1].event_time
    assert candidates[1].task_observation is None
    req = provider.requests[0]
    data = json.loads(req.user_input.split('Input:\n', 1)[1])
    assert data['product_task_fields']['message_ordinal'] == 1
    assert data['source_segment']['text'] == text
    assert '其他' not in req.user_input and '本条仅提供' not in req.user_input
    task_schema = req.available_tools[0].input_schema['properties']['claims']['items']['properties']['task']
    assert {'field_bindings', 'work_state', 'grounding_policy_version', 'title_binding'} <= set(task_schema['required'])
    replayed = SemanticIngestionPipeline(extractor, CandidateValidator()).replay_extraction(
        result.extraction.to_dict(), parse_source_text(a).segments[1], a, context)
    assert replayed.candidate_results == result.candidate_results
    assert len(provider.requests) == 1
    encoded, digest = serialize_candidate(candidates[0])
    assert serialize_candidate(deserialize_candidate(encoded)) == (encoded, digest)


@pytest.mark.parametrize('title,description,obj,action', [
    ('负责', '负责', '联调报告', '负责'),
    ('负责', '负责', '负责', '负责'),
    ('联调报告由李明负责', A, '联调报告', '联调报告由李明负责'),
])
def test_incomplete_title_falls_back_without_repairing_or_discarding_the_candidate(title, description, obj, action):
    a, _, _, _, result = run([proposal(description=description, title=title, obj=obj, action=action)])
    o = result.candidate_results[0].candidate.task_observation
    assert o.description == description
    assert o.title == A and o.title_needs_edit is True and o.verify(a)
    assert o.owner == '李明' and o.deadline_text == '10/12'


def test_short_valid_source_work_is_not_dropped_by_a_character_threshold():
    a = artifact('验收。')
    row = proposal('验收。', owner=None, deadline=None, description='验收', title='验收', obj='', action='验收')
    row['task']['title_binding']['state'] = 'INCOMPLETE'
    _, _, _, _, result = run([row], a)
    o = result.candidate_results[0].candidate.task_observation
    assert o.description == '验收' and o.title == '验收。' and o.title_needs_edit
    assert o.verify(a)


@pytest.mark.parametrize('mutation', [
    lambda t: t.update(text='编造的整句'),
    lambda t: t.update(description_parts=['原文中不存在的工作']),
    lambda t: t.update(work_state='HISTORICAL'),
    lambda t: t.update(work_state='NEGATED'),
])
def test_title_fallback_cannot_rescue_an_ungrounded_or_nonasserted_task(mutation):
    row = proposal(description='负责', title='负责')
    mutation(row['task'])
    _, _, _, _, result = run([row])
    assert all(r.candidate is None for r in result.candidate_results)


def test_duplicate_work_is_rejected_without_losing_the_existing_unique_binding_requirements():
    text = '提交报告；提交报告；乔雨负责，10/16 到期。'
    row = proposal(text, owner='乔雨', deadline='10/16', description='提交报告', obj='报告', action='提交')
    _, _, _, _, result = run([row], artifact(text))
    assert all(r.candidate is None for r in result.candidate_results)


@pytest.mark.parametrize('change', [dict(quote='李明'), dict(quote='不存在的原文'),
    dict(task_quote='别的工作'), dict(message_ordinal=1), dict(state='HISTORICAL'), dict(role='CANDIDATE_OWNER')])
def test_wrong_owner_evidence_stays_unknown_not_silently_phrase_matched(change):
    row = proposal()
    row['task']['field_bindings'][0].update(change)
    a, _, _, _, result = run([row])
    o = result.candidate_results[0].candidate.task_observation
    assert o.owner is None and o.verify(a)
    assert any(issue.startswith('OWNER_') for issue in o.validation_issues)


@pytest.mark.parametrize('token', ['13/16', '2/30'])
def test_invalid_slash_calendar_never_becomes_deadline_text(token):
    text = f'导出报告由乔雨负责，交付落在 {token}。'
    row = proposal(text, owner='乔雨', deadline=token, obj='导出报告', action='负责')
    a, _, _, _, result = run([row], artifact(text))
    o = result.candidate_results[0].candidate.task_observation
    assert o.deadline is None and o.deadline_text is None and o.deadline_role == 'UNKNOWN'
    assert 'DEADLINE_INVALID' in o.validation_issues and o.verify(a)


def test_old_bound_policy_does_not_acquire_new_calendar_acceptance_or_new_payload_keys():
    row = proposal()
    new = TaskProposal.from_dict(row['task'])
    old = replace(new, grounding_policy_version=None, title_binding=None)
    a = artifact(); segment = parse_source_text(a).segments[0]
    observed = build_task_observation(old, segment, a)
    assert observed.owner == '李明' and observed.deadline_text is None
    assert 'DEADLINE_INVALID' in observed.validation_issues
    encoded = observed.to_dict()
    assert not {'grounding_policy_version', 'title_binding', 'title', 'title_needs_edit'} & encoded.keys()
    assert TaskObservation.from_dict(encoded).to_dict() == encoded
    assert TaskObservation.from_dict(encoded).verify(a)
    legacy = replace(old, field_bindings=None, work_state=None)
    legacy_dict = legacy.to_dict()
    assert set(legacy_dict) == {'text', 'description_parts', 'owner', 'deadline_text', 'project', 'vendor'}
    assert TaskProposal.from_dict(legacy_dict).to_dict() == legacy_dict


def test_default_profile_hash_stays_frozen_and_injected_product_contract_is_rejected():
    _, _, provider, _, result = run([proposal()], product=False)
    assert result.extraction.failure_code is not None
    assert not result.extraction.claims and len(provider.requests) == 1
    assert result.extraction.receipt.schema_sha256 == _EXTRACTION_SCHEMA_SHA256
    assert _EXTRACTION_SCHEMA_SHA256 == 'e1833a9cb7f9f892f1246c6cea89d624ec2b6a0c0de916fbd70cb21dd68e8698'
    row = proposal()
    row['task'].pop('grounding_policy_version'); row['task'].pop('title_binding')
    row['task'].pop('field_bindings'); row['task'].pop('work_state')
    _, _, provider, _, result = run([row])
    assert result.extraction.failure_code is not None and not result.extraction.claims
    assert len(provider.requests) == 1


def test_stored_title_policy_or_source_tampering_never_verifies():
    a, _, _, _, result = run([proposal()])
    o = result.candidate_results[0].candidate.task_observation
    with pytest.raises(ValueError):
        replace(o, title='其他标题')
    with pytest.raises(ValueError):
        replace(o, grounding_policy_version='unsupported/v9')
    assert not o.verify(replace(a, content=a.content + 'source changed'))
    payload = o.to_dict(); payload['extra_untrusted_key'] = 'ignored?'
    with pytest.raises(ValueError):
        TaskObservation.from_dict(payload)


def test_context_rejects_forged_mapping_foreign_workspace_or_another_source():
    a = artifact(preceding=True); context = ProductTaskFieldContext(a, version=PRODUCT_TASK_FIELDS_VERSION)
    segment = parse_source_text(a).segments[1]
    with pytest.raises(ValueError): context.input_for(replace(segment, speaker='伪造'), a.workspace_id)
    with pytest.raises(ValueError): context.input_for(segment, 'foreign-workspace')
    with pytest.raises(ValueError): ProductTaskFieldContext(replace(a, content=a.content+'changed'), version=PRODUCT_TASK_FIELDS_VERSION).input_for(segment, a.workspace_id)


def test_false_title_marker_preserves_old_candidate_payload_and_fingerprint_exactly():
    import hashlib
    from linkloom.tasks.models import TaskCandidateInput, canonical_json
    from tests.unit.test_task_models import make_candidate
    original = make_candidate()
    old_payload = original.to_dict()
    assert 'title_needs_edit' not in old_payload
    expected = hashlib.sha256(canonical_json(old_payload).encode('utf-8')).hexdigest()
    restored = TaskCandidateInput.from_dict(old_payload)
    assert restored.title_needs_edit is False and restored.to_dict() == old_payload
    assert restored.fingerprint == original.fingerprint == expected
    flagged = replace(original, title_needs_edit=True)
    assert flagged.to_dict() == {**old_payload, 'title_needs_edit': True}
    assert flagged.fingerprint != expected
    assert TaskCandidateInput.from_dict(flagged.to_dict()) == flagged
    with pytest.raises(ValueError):
        TaskCandidateInput.from_dict({**old_payload, 'title_needs_edit': 'true'})


def test_product_persistence_raw_deadline_merge_and_undo_without_filling_year(tmp_path):
    from datetime import timedelta
    from linkloom.ui.product import ProductApplication
    from linkloom.tasks.store import TaskStore
    from linkloom.tasks.review import TaskReviewService
    from linkloom.tasks.candidate_source import ProductTaskCandidateSource

    class ByText(FakeModel):
        def complete(self, request):
            data = json.loads(request.user_input.split('Input:\n', 1)[1])
            if request.available_tools[0].tool_id == 'emit_task_field_annotations':
                text = data['task_text']
                task = proposal(text, owner='李明' if text == A else '王芳',
                    deadline='10/12' if text == A else '10/16', description='负责', title='负责')['task']
                self.payload = {key: task[key] for key in ('field_bindings', 'title_binding')}
            else:
                text = data['source_segment']['text']
                self.payload = {'claims': [claim(text, '负责')]}
            return super().complete(request)

    provider = ByText(None)
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id='offline', model_id='product-field',
        request_guard=lambda r: ProviderRequestAuthorization.approve(r, guard_id='offline-only',
            reservation_id='fixture', estimated_cost_upper_bound_usd=0), request_guard_id='offline-only')
    path = tmp_path / 'product.sqlite'
    app = ProductApplication(database_path=path, extractor=extractor)
    store = None
    try:
        app._start_ingestion = app._ingest  # synchronous fixture; actual product ingestion remains unchanged
        workspace = app.handle_post('/api/workspaces', {'name': '字段任务'})[0]['workspace']['workspace_id']
        app.handle_post('/api/workspaces/current', {'workspace_id': workspace})
        for name, text, stamp in [('A.md', A, '2032-01-01'), ('B.md', B, '2032-01-03')]:
            reply, status = app.handle_post('/api/sources/import', {'name': name,
                'content': f'[{stamp}T09:00:00Z] 记录人: {text}'})
            source = app.repository.get_source(workspace, reply['source']['source_id'])
            assert status == 202 and source['ingestion_status'] == 'COMPLETE'
        store = TaskStore(path)
        service = TaskReviewService(store, ProductTaskCandidateSource())
        candidates = service.list_candidates(workspace)
        assert len(candidates) == 2 and len(provider.requests) == 4
        assert all(x['candidate']['title_needs_edit'] is True for x in candidates)
        first = next(x for x in candidates if x['candidate']['owner'] == '李明')
        second = next(x for x in candidates if x['candidate']['owner'] == '王芳')
        assert first['candidate']['deadline'] is None and first['candidate']['deadline_text'] == '10/12'
        assert second['candidate']['deadline'] is None and second['candidate']['deadline_text'] == '10/16'
        now = datetime(2033, 1, 1, tzinfo=UTC)
        created = service.confirm(workspace, first['candidate']['candidate_id'], expected_candidate_fingerprint=first['fingerprint'],
            reviewer='人工审核人', reason='原文核对', patch={'title': '整理联调报告'}, now=now)
        assert created['task']['title'] == '整理联调报告'
        assert created['field_sources']['title']['origin'] == 'human'
        assert created['original_candidate']['candidate']['title'] == A
        assert created['original_candidate']['candidate']['title_needs_edit'] is True
        preview = service.preview_merge(workspace, created['task']['id'], second['candidate']['candidate_id'],
            expected_candidate_fingerprint=second['fingerprint'], apply_fields=['owner', 'deadline'], now=now+timedelta(days=1))
        assert preview['differences']['deadline'] == {
            'current': {'deadline': None, 'deadline_text': '10/12'},
            'candidate': {'deadline': None, 'deadline_text': '10/16'}}
        merged = service.merge(workspace, created['task']['id'], second['candidate']['candidate_id'],
            expected_candidate_fingerprint=preview['candidate_fingerprint'],
            expected_review_revision=preview['review_revision'], expected_preview_token=preview['preview_token'],
            apply_fields=preview['apply_fields'], reviewer='人工审核人', reason='同一任务人工判断', now=now+timedelta(days=1))
        assert merged['task']['owner'] == '王芳' and merged['task']['deadline'] is None
        assert merged['task']['deadline_text'] == '10/16'
        merge_id = merged['merge_history'][-1]['id']
        undo = service.preview_undo_merge(workspace, created['task']['id'], merge_id, now=now+timedelta(days=2))
        undone = service.undo_merge(workspace, created['task']['id'], merge_id,
            expected_review_revision=undo['review_revision'], expected_preview_token=undo['preview_token'],
            reviewer='人工审核人', reason='撤销人工匹配', now=now+timedelta(days=2))
        assert undone['task']['owner'] == '李明' and undone['task']['deadline'] is None
        assert undone['task']['deadline_text'] == '10/12' and undone['task']['title'] == '整理联调报告'
        assert len(provider.requests) == 4
        # Replaying an already accepted source performs zero new extraction calls.
        source = app.repository.get_source(workspace, reply['source']['source_id'])
        app._ingest(workspace, source['source_id'], app._source_artifact(source))
        assert len(provider.requests) == 4
        store.close(); store = None
        app.close()
        app = ProductApplication(database_path=path, extractor=None)
        store = TaskStore(path)
        cold = TaskReviewService(store, ProductTaskCandidateSource()).get_task(workspace, created['task']['id'])
        assert cold['task']['deadline'] is None and cold['task']['deadline_text'] == '10/12'
        assert cold['task']['owner'] == '李明'
    finally:
        if store is not None: store.close()
        app.close()


def test_saved_contract_cannot_select_product_policy_from_model_json_without_matching_receipt():
    a, context, _, _, result = run([proposal()])
    segment = parse_source_text(a).segments[0]
    old_context = SemanticExtractionContext(a.workspace_id, context.relation_resolver)
    assert CandidateValidator().validate(result.extraction, segment, a, old_context)[0].candidate is None
    fake_receipt = replace(result.extraction.receipt, schema_sha256=_EXTRACTION_SCHEMA_SHA256)
    fake_saved = replace(result.extraction, receipt=fake_receipt)
    assert CandidateValidator().validate(fake_saved, segment, a, context)[0].candidate is None
    forged_segment = replace(segment, speaker='伪造发言人')
    assert CandidateValidator().validate(result.extraction, forged_segment, a, context)[0].candidate is None
