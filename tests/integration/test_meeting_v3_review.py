"""Product import -> LOW inbox -> explicit review; fake provider only."""
import json

import pytest

from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.semantic_ingestion.task_extraction import TaskExtractionConfig
from tests.unit.test_meeting_tasks_v3 import Adapter, proposal
from tests.unit.test_meeting_tasks_v2 import ROSTER, source


def app_for(path, adapter):
    return ProductApplication(database_path=path, extractor=None,
        task_extraction_config=TaskExtractionConfig(task_extraction_mode='meeting_v3'),
        meeting_adapter=adapter, meeting_speakers=ROSTER, meeting_request_guard=lambda r: True)


def import_source(app, content=None):
    app._start_ingestion=lambda w,s,a: app._ingest(w,s,a)
    app.handle_post('/api/workspaces',{'name':'Test workspace'})
    result,_=app.handle_post('/api/sources/import',{'name':'meeting.txt','content':content or source().content})
    return result['source']['source_id']


def test_low_candidates_survive_cold_restart_and_need_explicit_review(tmp_path):
    path=tmp_path/'product.sqlite'
    adapter=Adapter([proposal(confidence='LOW',confidence_reason='The commitment is casual.',adopted=False)])
    app=app_for(path,adapter)
    sid=import_source(app)
    result=app.handle_get('/api/review/candidates')
    item,=result['meeting_tasks']
    assert item['confidence']=='LOW' and item['status']=='PENDING_REVIEW'
    assert item['authority']=='UNCONFIRMED' and item['review_required']
    assert item['evidence'][0]['span']['quote'] in source().content
    assert app.handle_get('/api/sources')['sources'][0]['review_required_count']==1
    assert not app.store.list_decisions(app._workspace_id())
    app.close()
    app=app_for(path,Adapter())
    try:
        assert len(app.handle_get('/api/review/candidates')['meeting_tasks'])==1
        assert app.handle_get('/api/sources')['sources'][0]['review_required_count']==1
        with pytest.raises(ProductApplicationError,match='Provide'):
            app.handle_post('/api/review/meeting-tasks/'+item['candidate_id'],{'action':'CONFIRM'})
        response,_=app.handle_post('/api/review/meeting-tasks/'+item['candidate_id'],{
            'action':'CONFIRM','reviewer':'human','note':'Checked source.', 'source_version':item['source_version']})
        assert response=={'status':'CONFIRMED_BY_HUMAN','authority':'UNCONFIRMED'}
        assert not app.handle_get('/api/review/candidates')['meeting_tasks']
        assert app.handle_get('/api/sources')['sources'][0]['review_required_count']==0
        assert not app.store.list_decisions(app._workspace_id())
    finally:
        app.close()


def test_source_change_and_workspace_change_hide_and_block_old_candidates(tmp_path):
    app=app_for(tmp_path/'product.sqlite',Adapter([proposal(confidence='LOW',confidence_reason='Casual.')]))
    try:
        sid=import_source(app)
        item,=app.handle_get('/api/review/candidates')['meeting_tasks']
        workspace=app._workspace_id()
        app.handle_post('/api/workspaces',{'name':'Other workspace'})
        assert not app.handle_get('/api/review/candidates')['meeting_tasks']
        with pytest.raises(ProductApplicationError,match='Refresh'):
            app.handle_post('/api/review/meeting-tasks/'+item['candidate_id'],{})
        app.handle_post('/api/workspaces/current',{'workspace_id':workspace})
        app.meeting_tasks.extractor.adapter=Adapter()
        app.handle_put('/api/sources/'+sid,{'name':'meeting.txt','content':source([('X','Different work.')]).content})
        assert not app.handle_get('/api/review/candidates')['meeting_tasks']
        with pytest.raises(ProductApplicationError,match='Refresh'):
            app.handle_post('/api/review/meeting-tasks/'+item['candidate_id'],{})
    finally:app.close()


def test_low_bad_reference_is_not_queued(tmp_path):
    app=app_for(tmp_path/'product.sqlite',Adapter([proposal(confidence='LOW',confidence_reason='Casual.',
        evidence=[{'message_ordinal':999,'supports':['work']}])]))
    try:
        import_source(app)
        assert not app.handle_get('/api/review/candidates')['meeting_tasks']
        assert app.handle_get('/api/sources')['sources'][0]['ingestion_status']=='PARTIAL'
    finally:app.close()


def test_deleted_queued_source_stops_before_any_provider_call(tmp_path):
    adapter=Adapter()
    app=app_for(tmp_path/'product.sqlite',adapter)
    try:
        sid=import_source(app)
        artifact=app._source_artifact(app.repository.get_source(app._workspace_id(),sid))
        adapter.requests.clear()
        app.handle_delete('/api/sources/'+sid)
        with pytest.raises(ValueError,match='SOURCE_VERSION_CHANGED'):
            app.meeting_tasks.ingest(artifact)
        assert not adapter.requests
    finally:app.close()


def test_source_revoked_during_first_window_stops_remaining_windows(tmp_path):
    from dataclasses import replace
    adapter=Adapter()
    app=app_for(tmp_path/'product.sqlite',adapter)
    try:
        sid=import_source(app,source([('X','x'*3500) for _ in range(24)]).content)
        artifact=app._source_artifact(app.repository.get_source(app._workspace_id(),sid))
        adapter.requests.clear()
        app.meeting_tasks.extractor.config=replace(app.task_extraction_config,max_input_bytes=32768,window_body_bytes=19000)
        original=adapter.complete
        def revoke(request):
            response=original(request)
            app.handle_delete('/api/sources/'+sid)
            return response
        adapter.complete=revoke
        with pytest.raises(ValueError,match='SOURCE_VERSION_CHANGED'):
            app.meeting_tasks.ingest(artifact)
        assert len(adapter.requests)==1
    finally:app.close()


def test_retry_changed_model_reason_does_not_duplicate_or_reopen_review(tmp_path):
    adapter=Adapter([proposal(confidence='LOW',confidence_reason='Tentative one.')])
    app=app_for(tmp_path/'product.sqlite',adapter)
    try:
        sid=import_source(app)
        artifact=app._source_artifact(app.repository.get_source(app._workspace_id(),sid))
        first,=app.handle_get('/api/review/candidates')['meeting_tasks']
        adapter.rows=[proposal(confidence='LOW',confidence_reason='Tentative two.',reason='Another wording.')]
        app.meeting_tasks.ingest(artifact)
        second,=app.handle_get('/api/review/candidates')['meeting_tasks']
        assert first['candidate_id']==second['candidate_id']
        assert app.handle_get('/api/sources')['sources'][0]['candidate_count']==1
        app.handle_post('/api/review/meeting-tasks/'+first['candidate_id'],{'action':'REJECT','reviewer':'human',
            'note':'Not accepted.','source_version':first['source_version']})
        adapter.rows=[proposal(confidence='LOW',confidence_reason='Tentative three.')]
        app.meeting_tasks.ingest(artifact)
        assert not app.handle_get('/api/review/candidates')['meeting_tasks']
        assert app.handle_get('/api/sources')['sources'][0]['review_required_count']==0
    finally:app.close()


@pytest.mark.parametrize('initial,next_confidence', [('HIGH','LOW'),('LOW','HIGH')])
def test_pending_retry_refreshes_fields_without_promoting_low(tmp_path,initial,next_confidence):
    adapter=Adapter([proposal(confidence=initial,confidence_reason='Original certainty.',
        deadline={'raw_text':None,'kind':'UNSPECIFIED'},
        evidence=[{'message_ordinal':0,'supports':['work','adoption','owner']}])])
    app=app_for(tmp_path/'product.sqlite',adapter)
    try:
        sid=import_source(app)
        artifact=app._source_artifact(app.repository.get_source(app._workspace_id(),sid))
        first,=app.handle_get('/api/review/candidates')['meeting_tasks']
        adapter.rows=[proposal(confidence=next_confidence,confidence_reason='Retry certainty.')]
        app.meeting_tasks.ingest(artifact)
        second,=app.handle_get('/api/review/candidates')['meeting_tasks']
        assert second['candidate_id']==first['candidate_id']
        assert second['confidence']=='LOW'
        assert second['confidence_reason']==('Original certainty.' if initial=='LOW' else 'Retry certainty.')
        assert second['deadline']['raw_text']=='before the next workshop'
        assert len(second['evidence'])==2
        assert app.handle_get('/api/sources')['sources'][0]['candidate_count']==1
    finally:app.close()
