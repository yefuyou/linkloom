"""Independent synthetic protocol cases, not the pending external evaluation set."""
from types import SimpleNamespace

import pytest

from linkloom.tasks.same_task_hints import SameTaskHintConfig, SameTaskHintService
from linkloom.tasks.models import TaskStatus
from linkloom.tasks.query import TaskQueryError
from linkloom.tasks.review_models import CandidateUnavailableError
from linkloom.ui.task_api import TaskProductAPI
from tests.unit.test_task_legacy_adapter import app, close, seed, TEXT
from tests.unit.test_task_rebind import revise


def confirm(value, identifier=None, title=None):
    identifier = identifier or value.observation.observation_id
    raw = value.service.preview_candidate(value.workspace, identifier)
    return value.service.confirm(value.workspace, identifier, expected_candidate_fingerprint=raw['fingerprint'],
        patch={'title': title} if title else {}, reviewer='测试审核人', reason='仅合成流程测试')


def incoming(value):
    text = '由王芳负责联调那份报告，2026年10月16日前完成。'
    _, candidate = revise(value, content='[2026-10-10T10:00:00+08:00] 评审人: ' + text,
        text=text, parts=('联调那份报告',), source_id='new-meeting.txt')
    identifier = candidate.task_observation.observation_id
    return identifier, value.service.preview_candidate(value.workspace, identifier)['fingerprint']


def hit(task_id, score=3.0, rrf=0.0327, dense=0.96, title_similarity=0.96):
    return SimpleNamespace(resource_id=task_id, rerank_score=score, score=rrf,
                           metadata={'channel_scores': {'dense': dense}, 'title_similarity': title_similarity})


def service(value, monkeypatch, ranking):
    result = SameTaskHintService(value.service, SameTaskHintConfig(True))
    monkeypatch.setattr(result, '_rank', ranking)
    return result


def test_default_off_has_identical_list_and_never_constructs_local_models(app, monkeypatch):
    monkeypatch.delenv('LINKLOOM_SAME_TASK_HINT', raising=False)
    monkeypatch.setenv('LINKLOOM_SAME_TASK_HINT_MIN_LOCAL_SCORE', 'not-a-number')
    api = TaskProductAPI(app.path)
    try:
        original = app.service.list_candidates_page(app.workspace)
        value, status = api.handle('GET', '/api/product/task-candidates?workspace_id=' + app.workspace)
        assert status == 200 and value == original
        value, status = api.handle('GET', '/api/product/task-candidates/x/same-task-hints?workspace_id=' + app.workspace)
        assert status == 404 and api._hints is None
    finally:
        api.close()


@pytest.mark.parametrize('name,value', [('MIN_LOCAL_SCORE','nan'),('MIN_RRF_SCORE','1'),('MIN_RRF_DENSE_SCORE','-2')])
def test_invalid_thresholds_cannot_open_the_gate(monkeypatch, name, value):
    monkeypatch.setenv('LINKLOOM_SAME_TASK_HINT', 'on')
    monkeypatch.setenv('LINKLOOM_SAME_TASK_HINT_' + name, value)
    with pytest.raises(ValueError):
        SameTaskHintConfig.from_env()


def test_authorized_original_quotes_are_returned_and_human_values_are_not_fabricated(app, monkeypatch):
    task = confirm(app, title='联调报告')['task']
    identifier, fingerprint = incoming(app)
    def rank(snapshot):
        assert [row['task']['id'] for row in snapshot['tasks']] == [task['id']]
        assert '联调报告' in snapshot['tasks'][0]['text']
        assert '联调那份报告' in snapshot['candidate']['title']
        return [hit(task['id'])], 'local', None
    result = service(app, monkeypatch, rank).get_hints(app.workspace, identifier, fingerprint)
    hint = result['hints'][0]
    assert hint['task']['title'] == '联调报告' and hint['score'] == 3
    assert hint['candidate_evidence'][0]['quote'].startswith('由王芳负责联调那份报告')
    assert hint['task_evidence'][0]['quote'] == TEXT
    assert hint['match_kind'] == 'semantic' and hint['score_kind'] == 'local'


@pytest.mark.parametrize('status', ['done','cancelled'])
def test_finished_tasks_never_reach_retrieval(app, monkeypatch, status):
    task = confirm(app)['task']
    app.store.set_status(app.workspace,task['id'],status,actor='测试',reason='测试')
    identifier, fingerprint = incoming(app)
    result = service(app, monkeypatch, lambda snapshot: pytest.fail('empty pool must not be retrieved'))
    assert result.get_hints(app.workspace, identifier, fingerprint)['hints'] == []


def test_foreign_workspace_text_never_reaches_rank_or_response(app, tmp_path, monkeypatch):
    # Both workspaces live in the same real catalogue/database.
    foreign = seed(app.path, text='由李明负责绝密预算报告，2026年10月12日前完成。',
        content='[2026-10-08T10:00:00+08:00] 主持人: 由李明负责绝密预算报告，2026年10月12日前完成。',parts=('绝密预算报告',))
    try:
        confirm(foreign)
        local = confirm(app)['task']
        identifier, fingerprint = incoming(app)
        def rank(snapshot):
            assert '绝密' not in str(snapshot)
            assert all(row['task']['workspace_id'] == app.workspace for row in snapshot['tasks'])
            return [hit(local['id'])], 'local', None
        result = service(app, monkeypatch, rank).get_hints(app.workspace,identifier,fingerprint)
        assert '绝密' not in str(result)
        with pytest.raises(TaskQueryError):
            service(app, monkeypatch, rank).get_hints(foreign.workspace,identifier,fingerprint)
    finally:
        close(foreign)


def test_unreviewed_and_inactive_task_sources_never_reach_rank(app, monkeypatch):
    task = confirm(app)['task']
    identifier, fingerprint = incoming(app)
    with app.repository._connection:
        app.repository._connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=? AND source_id=?',
            (app.workspace,app.artifact.artifact_id))
    result = service(app, monkeypatch, lambda snapshot: pytest.fail('unauthorized task must not reach rank'))
    assert result.get_hints(app.workspace,identifier,fingerprint)['hints'] == []
    assert app.service.get_task(app.workspace,task['id'])['review_status'] == 'needs_review'


@pytest.mark.parametrize('mode,score,rrf,dense,shown', [
    ('local',2.0,0.0327,0.96,True),('local',1.999,0.0327,0.96,False),
    ('local',float('nan'),0.0327,0.96,False),
    ('rrf',None,0.0326,0.95,True),('rrf',None,0.0324,0.95,False),
    ('rrf',None,0.0327,0.89,False)])
def test_conservative_mode_specific_thresholds(app,monkeypatch,mode,score,rrf,dense,shown):
    task=confirm(app)['task']
    identifier,fingerprint=incoming(app)
    value=service(app,monkeypatch,lambda snapshot:([hit(task['id'],score,rrf,dense)],mode,None))
    result=value.get_hints(app.workspace,identifier,fingerprint)
    assert bool(result['hints']) is shown


def test_exact_title_is_first_even_when_local_ranking_prefers_another_task(app,monkeypatch):
    first=confirm(app,title='联调报告')['task']
    identifier,fingerprint=incoming(app)
    second=confirm(app,identifier,title='联调那份报告')['task']
    identifier,fingerprint=incoming_variant(app)
    value=service(app,monkeypatch,lambda snapshot:([hit(first['id'],9),hit(second['id'],3)],'local',None))
    hints=value.get_hints(app.workspace,identifier,fingerprint)['hints']
    assert [item['task']['id'] for item in hints]==[second['id'],first['id']]
    assert hints[0]['match_kind']=='exact'


def incoming_variant(value):
    text='由陈宁负责联调那份报告，2026年10月20日前完成。'
    _,candidate=revise(value,content='[2026-10-12T10:00:00+08:00] 负责人: '+text,text=text,
        parts=('联调那份报告',),source_id='third-meeting.txt')
    identifier=candidate.task_observation.observation_id
    return identifier,value.service.preview_candidate(value.workspace,identifier)['fingerprint']


def test_retrieval_failure_keeps_exact_hint_and_never_calls_model_provider(app,monkeypatch):
    task=confirm(app,title='联调那份报告')['task']
    identifier,fingerprint=incoming(app)
    def fail(snapshot): raise ImportError('optional retrieval unavailable')
    result=service(app,monkeypatch,fail).get_hints(app.workspace,identifier,fingerprint)
    assert result['mode']=='unavailable'
    assert [row['task']['id'] for row in result['hints']]==[task['id']]


def test_source_changes_during_ranking_discard_the_entire_result(app,monkeypatch):
    task=confirm(app)['task']
    identifier,fingerprint=incoming(app)
    def rank(snapshot):
        app.store.set_status(app.workspace,task['id'],'done',actor='测试',reason='完成')
        return [hit(task['id'])],'local',None
    with pytest.raises(TaskQueryError,match='authorization changed'):
        service(app,monkeypatch,rank).get_hints(app.workspace,identifier,fingerprint)


def test_processed_candidate_cannot_receive_hints(app,monkeypatch):
    raw=app.service.preview_candidate(app.workspace,app.observation.observation_id)
    confirm(app)
    from linkloom.tasks.store import CandidateAlreadyReviewedError
    with pytest.raises(CandidateAlreadyReviewedError):
        service(app,monkeypatch,lambda _: pytest.fail('processed')).get_hints(
            app.workspace,app.observation.observation_id,raw['fingerprint'])


def test_same_utterance_other_work_is_not_part_of_the_task_description(tmp_path,monkeypatch):
    text='由李明负责联调报告，另由陈宁负责联调周报，2026年10月12日前完成。'
    value=seed(tmp_path/'mixed.sqlite',content='[2026-10-08T10:00:00+08:00] 主持人: '+text,
               text=text,parts=('联调报告',))
    try:
        task=confirm(value)['task']
        identifier,fingerprint=incoming(value)
        def rank(snapshot):
            row=snapshot['tasks'][0]
            assert '联调报告' in row['text'] and '联调周报' not in row['text']
            assert '联调周报' in row['evidence'][0]['quote']  # Complete provenance remains available.
            return [hit(task['id'])],'local',None
        result=service(value,monkeypatch,rank).get_hints(value.workspace,identifier,fingerprint)
        assert len(result['hints'])==1
    finally:
        close(value)


def test_model_inference_is_serialized_without_holding_the_task_transaction(app,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    value=SameTaskHintService(app.service,SameTaskHintConfig(True))
    entered,release=Event(),Event()
    active=[]
    def rank(snapshot):
        assert not app.store._connection.in_transaction
        active.append(snapshot)
        if snapshot==1:
            entered.set()
            assert release.wait(5)
        return [],'rrf',None
    monkeypatch.setattr(value,'_rank_local',rank)
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(value._rank,1)
        assert entered.wait(5)
        second=pool.submit(value._rank,2)
        assert active==[1]
        release.set()
        first.result(5)
        second.result(5)
    assert active==[1,2]


@pytest.mark.parametrize('method',['GET','POST','PUT','DELETE','HEAD'])
def test_off_route_remains_unknown_for_every_method(app,monkeypatch,method):
    monkeypatch.delenv('LINKLOOM_SAME_TASK_HINT',raising=False)
    api=TaskProductAPI(app.path)
    try:
        _,status=api.handle(method,'/api/product/task-candidates/x/same-task-hints?workspace_id='+app.workspace)
        assert status==404 and api._hints is None and api._service is None
    finally:
        api.close()


def test_enabled_api_checks_workspace_fingerprint_and_query_whitelist(app,monkeypatch):
    from urllib.parse import urlencode
    monkeypatch.setenv('LINKLOOM_SAME_TASK_HINT','on')
    task=confirm(app)['task']
    identifier,fingerprint=incoming(app)
    api=TaskProductAPI(app.path)
    try:
        api._hints.close()
        api._hints=service(app,monkeypatch,lambda snapshot:([hit(task['id'])],'local',None))
        path='/api/product/task-candidates/'+identifier+'/same-task-hints?'
        query=dict(workspace_id=app.workspace,candidate_fingerprint=fingerprint)
        result,status=api.handle('GET',path+urlencode(query))
        assert status==200 and result['hints'][0]['task']['id']==task['id']
        for changes,status_expected in [({'workspace_id':'foreign'},404),({'candidate_fingerprint':'b'*64},409),
            ({'candidate_fingerprint':'not-a-hash'},400),({'raw_text':'injected'},400)]:
            result,status=api.handle('GET',path+urlencode(query|changes))
            assert status==status_expected and 'hints' not in result
        _,status=api.handle('GET',path+urlencode(query)+'&workspace_id=other')
        assert status==400
    finally:
        api.close()


@pytest.mark.parametrize('fails',[False,True])
def test_real_hybrid_reranks_only_ten_and_failure_restores_rrf(app,fails):
    np=pytest.importorskip('numpy',reason='Real hybrid integration requires optional local retrieval dependencies')
    from linkloom.rerank.base import RerankError,score_candidates
    from linkloom.rerank.pipeline import RerankPipeline
    class Embedder:
        def encode(self,texts):
            return np.array([[1.,1./(index+1)] for index,_ in enumerate(texts)])
    class Backend:
        seen=[]
        def rerank(self,query,candidates):
            self.seen=[item.evidence.resource_id for item in candidates]
            assert len(candidates)==10
            if fails: raise RerankError('inference_timeout')
            return score_candidates(candidates,list(range(10)))
    backend=Backend()
    value=SameTaskHintService(app.service,SameTaskHintConfig(True),embedder=Embedder(),
        reranker=RerankPipeline('local',local=backend))
    snapshot=dict(candidate=dict(workspace_id=app.workspace,title='报告',owner=None),evidence=[],description=['报告'],
        tasks=[dict(task=dict(id=f'task-{i}',title=f'报告 {i}'),text=f'报告 {i}') for i in range(15)])
    hits,mode,reason=value._rank(snapshot)
    assert len(hits)==10 and len(backend.seen)==10
    ids=[item.resource_id for item in hits]
    if fails:
        assert mode=='rrf' and reason=='local_rerank_unavailable' and ids==backend.seen
    else:
        assert mode=='local' and reason is None and ids==list(reversed(backend.seen))


@pytest.mark.parametrize('similarity,shown',[(0.85,True),(0.849,False),(float('nan'),False)])
def test_related_deliverable_needs_title_similarity_even_with_high_relevance(app,monkeypatch,similarity,shown):
    task=confirm(app)['task']
    identifier,fingerprint=incoming(app)
    result=service(app,monkeypatch,lambda snapshot:([hit(task['id'],9,title_similarity=similarity)],'local',None))
    assert bool(result.get_hints(app.workspace,identifier,fingerprint)['hints']) is shown


def test_startup_preloads_in_background_and_refresh_replaces_pending_with_local(app, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    class Pipeline:
        def prepare(self, *, cancel):
            entered.set()
            assert release.wait(5)
        def close(self): pass
    monkeypatch.setenv('LINKLOOM_SAME_TASK_HINT', 'on')
    monkeypatch.setattr(SameTaskHintService, '_local_reranker', lambda self: Pipeline())
    task = confirm(app, title='联调那份报告')['task']
    identifier, fingerprint = incoming(app)
    api = TaskProductAPI(app.path)
    try:
        assert entered.wait(5)  # No hint HTTP request was needed to start preparation.
        monkeypatch.setattr(api._hints, '_rank_local', lambda snapshot: ([hit(task['id'])], 'local', None))
        pending = api._hints.get_hints(app.workspace, identifier, fingerprint)
        assert pending['status'] == 'preparing' and pending['mode'] == 'preparing'
        assert [h['match_kind'] for h in pending['hints']] == ['exact']
        release.set()
        api._hints._preload_thread.join(5)
        complete = api._hints.get_hints(app.workspace, identifier, fingerprint)
        assert complete['status'] == 'complete' and complete['mode'] == 'local'
        assert complete['hints'][0]['match_kind'] == 'exact'
    finally:
        release.set()
        api.close()


def test_semantic_preparation_is_not_a_filtered_rrf_result_and_can_be_refreshed(app, monkeypatch):
    from threading import Event
    entered, release = Event(), Event()
    task = confirm(app)['task']
    identifier, fingerprint = incoming(app)
    class Pipeline:
        def prepare(self, *, cancel):
            entered.set()
            assert release.wait(5)
    value = SameTaskHintService(app.service, SameTaskHintConfig(True), reranker=Pipeline())
    monkeypatch.setattr(value, '_rank_local', lambda snapshot: ([hit(task['id'])], 'local', None))
    value.start_preload()
    try:
        assert entered.wait(5)
        thread = value._preload_thread
        value.start_preload()
        assert value._preload_thread is thread
        pending = value.get_hints(app.workspace, identifier, fingerprint)
        assert pending['hints'] == [] and pending['status'] == 'preparing'
        release.set(); thread.join(5)
        result = value.get_hints(app.workspace, identifier, fingerprint)
        assert result['mode'] == 'local' and result['status'] == 'complete'
        assert result['hints'][0]['task']['id'] == task['id']
    finally:
        release.set(); value.close()


def test_failed_preload_preserves_existing_rrf_thresholds(app, monkeypatch):
    class Pipeline:
        def prepare(self, *, cancel): raise ImportError('local artifact unavailable')
    task = confirm(app)['task']
    identifier, fingerprint = incoming(app)
    value = SameTaskHintService(app.service, SameTaskHintConfig(True), reranker=Pipeline())
    value.start_preload(); value._preload_thread.join(5)
    assert not value.preparing and value._preload_state == 'unavailable'
    monkeypatch.setattr(value, '_rank_local', lambda snapshot: ([hit(task['id'], None, dense=.89)], 'rrf', 'local_rerank_unavailable'))
    result = value.get_hints(app.workspace, identifier, fingerprint)
    assert result['mode'] == 'rrf' and result['status'] == 'complete' and result['hints'] == []
    monkeypatch.setattr(value, '_rank_local', lambda snapshot: ([hit(task['id'], None, dense=.95)], 'rrf', 'local_rerank_unavailable'))
    assert value.get_hints(app.workspace, identifier, fingerprint)['hints'][0]['score_kind'] == 'rrf'
    value.close()


def test_disabled_service_never_starts_a_loader(app, monkeypatch):
    value = SameTaskHintService(app.service, SameTaskHintConfig(False))
    monkeypatch.setattr(value, '_local_reranker', lambda: pytest.fail('disabled loader'))
    value.start_preload()
    assert value._preload_thread is None and not value.preparing
    value.close()


def test_close_cancels_preparation_and_releases_owned_local_worker(app, monkeypatch):
    from threading import Event
    entered, closed = Event(), Event()
    class Pipeline:
        def prepare(self, *, cancel):
            entered.set()
            assert cancel.wait(5)
            raise RuntimeError('cancelled')
        def close(self): closed.set()
    value = SameTaskHintService(app.service, SameTaskHintConfig(True))
    monkeypatch.setattr(value, '_local_reranker', lambda: value.reranker)
    value.reranker = Pipeline()
    value.start_preload()
    assert entered.wait(5)
    value.close()
    assert closed.is_set() and not value._preload_thread.is_alive()
    previous = value._preload_thread
    value.start_preload()
    assert value._preload_thread is previous


def test_restarted_worker_preparing_never_publishes_filtered_rrf(app):
    np = pytest.importorskip('numpy', reason='Real hybrid integration requires optional local retrieval dependencies')
    from threading import Event
    from linkloom.rerank.base import RerankError, score_candidates
    from linkloom.rerank.pipeline import RerankPipeline
    entered, release = Event(), Event()
    class Embedder:
        def encode(self, texts): return np.array([[1., 1.] for _ in texts])
    class Backend:
        ready = False
        def prepare(self, *, cancel):
            entered.set()
            assert release.wait(5)
            self.ready = True
        def rerank(self, query, candidates):
            if not self.ready: raise RerankError('model_preparing')
            return score_candidates(candidates, [4.0] * len(candidates))
    value = SameTaskHintService(app.service, SameTaskHintConfig(True), embedder=Embedder(),
        reranker=RerankPipeline('local', local=Backend()))
    snapshot = dict(candidate=dict(workspace_id=app.workspace, title='联调那份报告', owner=None), evidence=[],
        description=[], tasks=[dict(task=dict(id='report', title='联调报告'), text='联调报告')])
    try:
        hits, mode, reason = value._rank(snapshot)
        assert hits == [] and mode == 'preparing' and reason == 'model_preparing'
        assert entered.wait(5)
        release.set(); value._preload_thread.join(5)
        hits, mode, reason = value._rank(snapshot)
        assert mode == 'local' and reason is None and hits[0].rerank_score == 4.0
    finally:
        release.set(); value.close()
