"""Formal fixture construction contracts; synthetic public facts only, no frozen QA."""
from copy import deepcopy
import hashlib,json,sqlite3
import pytest
from tests.meeting_graph_eval_support import build_meetings,build_meridian,task_candidate_source
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.temporal_graph.query import graph_query


def public_fixture():
    return dict(workspace='unit-meetings',topic='测试项目',meetings=[
        dict(id='M1',name='早会',date='2026-04-01',lines=['1 决定采用甲方案。','2 核对报告由安然负责，4/10 前完成。']),
        dict(id='M2',name='评审',date='2026-04-05',lines=['1 决定改用乙方案。','2 核对报告改由白宁负责，延期到4/15。','3 建议再做一个看板。'])],
        confirmed_decisions=[dict(id='D1',text='甲方案',meeting='M1',evidence='M1#1',valid_from='2026-04-01',superseded_by='D2'),
            dict(id='D2',text='乙方案',meeting='M2',evidence='M2#1',valid_from='2026-04-05',supersedes='D1')],
        confirmed_tasks=[dict(id='T1',title='核对报告',source_decision='D1',history=[
            dict(meeting='M1',evidence='M1#2',owner='安然',deadline='4/10'),
            dict(meeting='M2',evidence='M2#2',owner='白宁',deadline='4/15',via='人工合并')])],
        unconfirmed=[dict(evidence='M2#3',text='看板',status='未确认')])


def test_formal_fixtures_keep_exact_source_and_business_date_is_not_merge_operation_time(tmp_path):
    given=public_fixture();before=deepcopy(given);path=tmp_path/'meetings.sqlite'
    result=build_meetings(given,path,recorded_at='2026-11-03T00:00:00+08:00')
    assert given==before
    store=SQLiteGraphStore(path,authorized_workspace=result['workspace_id'],authorize_source=lambda *a:True,task_candidate_source=task_candidate_source())
    snapshot=store.load_edges(result['workspace_id'])
    assert len([e for e in snapshot.edges if e.edge_type=='decided_in'])==2
    assert len([e for e in snapshot.edges if e.edge_type=='derived_from'])==1
    assert sum(r['pending_count'] for r in snapshot.records if r['kind']=='meeting')==1
    task=next(n for n in snapshot.nodes if n.kind=='task')
    step=dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)
    earlier=graph_query(store,[step],'2026-04-02',result['workspace_id'],outputs=[dict(step='task',field='owner')])
    later=graph_query(store,[step],'2026-04-06',result['workspace_id'],outputs=[dict(step='task',field='owner')])
    assert earlier['outputs'][0]['values']==['安然']
    assert later['outputs'][0]['values']==['白宁']
    assert later['steps'][0]['records'][0]['current']['business_valid_from']=='2026-04-05'
    assert later['steps'][0]['records'][0]['current']['system_recorded_at'].startswith('2026-11-')
    with sqlite3.connect(path) as db:
        actual={r[0] for r in db.execute('SELECT content FROM ll_source_version')}
        assert actual=={'\n'.join(m['lines']) for m in given['meetings']}
        assert db.execute('SELECT count(*) FROM semantic_materialization_receipt').fetchone()[0]==2


def test_fixture_confirmation_rejects_expected_or_question_input_before_database_creation(tmp_path):
    given=public_fixture();given['expected']={}
    with pytest.raises(ValueError,match='FORBIDDEN'):build_meetings(given,tmp_path/'bad.sqlite',recorded_at='2026-11-03T00:00:00+08:00')
    assert not (tmp_path/'bad.sqlite').exists()


def test_meridian_formal_subject_identity_respects_reverse_superseded_by_mapping(tmp_path):
    given=dict(workspace='meridian-unit',sources=[dict(id='m1',text='On 2025-02-14 New Plan replaced Old Plan.'),
        dict(id='m2',text='As of 2025-02-15 Ada is the accountable owner of New Plan.'),
        dict(id='m3',text='As of 2025-02-16 Ada reports directly to Bo.')],confirmed_edges=[
        dict(type='supersedes',**{'from':'New Plan','to':'Old Plan'},valid_from='2025-02-14',evidence='m1'),
        dict(type='owner_of',**{'from':'Ada','to':'New Plan'},valid_from='2025-02-15',evidence='m2'),
        dict(type='reports_to',**{'from':'Ada','to':'Bo'},valid_from='2025-02-16',evidence='m3')])
    path=tmp_path/'meridian.sqlite';result=build_meridian(given,path);workspace=result['workspaces'][('meridian','meridian-unit')]
    store=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True)
    snapshot=store.load_edges(workspace)
    assert len([e for e in snapshot.edges if e.edge_type in {'supersedes','owner_of','reports_to'}])==3,snapshot.exclusions
    new=[n for n in snapshot.nodes if n.kind=='plan' and n.label=='New Plan']
    assert len(new)==1


def test_same_task_explicit_business_date_is_not_merge_operation_time_and_undo_keeps_history(tmp_path):
    from datetime import UTC,datetime,timedelta
    from linkloom.tasks.store import TaskStore
    from linkloom.tasks.review import TaskReviewService
    given=public_fixture();path=tmp_path/'business-identity.sqlite'
    built=build_meetings(given,path,recorded_at='2026-11-03T00:00:00+08:00');workspace=built['workspace_id']
    source=task_candidate_source();graph=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True,task_candidate_source=source)
    before=[e for e in graph.load_edges(workspace,'2026-04-04').edges if e.edge_type=='same_task']
    later=[e for e in graph.load_edges(workspace,'2026-04-06').edges if e.edge_type=='same_task']
    assert not before and len(later)==1
    assert later[0].valid_from=='2026-04-05' and later[0].time_basis=='CONFIRMED_VALID_TIME'
    with sqlite3.connect(path) as db:
        system_time=db.execute("SELECT created_at FROM task_merge_operation WHERE operation='merge'").fetchone()[0]
        assert system_time.startswith('2026-11-')
    tasks=TaskStore(path)
    try:
        review=TaskReviewService(tasks,source);task_id=built['tasks']['T1']
        current=review.get_task(workspace,task_id,now=datetime(2026,11,4,tzinfo=UTC))
        merge_id=next(m['id'] for m in current['merge_history'] if m['operation']=='merge' and m['undone_by'] is None)
        preview=review.preview_undo_merge(workspace,task_id,merge_id,now=datetime(2026,11,4,tzinfo=UTC))
        review.undo_merge(workspace,task_id,merge_id,expected_review_revision=preview['review_revision'],expected_preview_token=preview['preview_token'],reviewer='human',reason='撤销合并',now=datetime(2026,11,4,tzinfo=UTC))
        assert len([e for e in graph.load_edges(workspace,'2026-04-06').edges if e.edge_type=='same_task'])==1
        assert not [e for e in graph.load_edges(workspace,'2026-11-05').edges if e.edge_type=='same_task']
        with sqlite3.connect(path) as db:assert db.execute("SELECT created_at FROM task_merge_operation WHERE operation='merge'").fetchone()[0]==system_time
    finally:tasks.close()


@pytest.mark.parametrize('declaration',['missing','bad_date','bad_hash','bad_endpoint'])
def test_same_task_business_declaration_is_explicit_and_exact(tmp_path,monkeypatch,declaration):
    import linkloom.temporal_graph.projection as projection
    path=tmp_path/'identity-validation.sqlite'
    built=build_meetings(public_fixture(),path,recorded_at='2026-11-03T00:00:00+08:00');ws=built['workspace_id']
    graph=SQLiteGraphStore(path,authorized_workspace=ws,authorize_source=lambda *a:True,task_candidate_source=task_candidate_source())
    original=next(e for e in graph.load_edges(ws).edges if e.edge_type=='same_task');read=projection._rows
    def altered(db,table,workspace):
        rows=read(db,table,workspace)
        for row in rows:
            if table=='task_review_event' and row['id']==original.confirmation_ref:
                audit=json.loads(row['reason'])
                if declaration=='missing':audit.pop('graph_valid_time');audit.pop('meeting')
                elif declaration=='bad_date':audit['graph_valid_time']['value']='2026-04-03'
                elif declaration=='bad_hash':audit['meeting']['content_sha256']='f'*64
                else:audit['meeting']['document_id']='different-source'
                row['reason']=json.dumps(audit)
        return rows
    monkeypatch.setattr(projection,'_rows',altered)
    edges=[e for e in graph.load_edges(ws).edges if e.edge_type=='same_task']
    if declaration=='missing':
        assert len(edges)==1 and edges[0].valid_from.startswith('2026-11-')
        assert edges[0].time_basis=='HUMAN_IDENTITY_ASSERTION'
    else:assert not edges


def test_same_business_date_values_are_conflicting_not_ordered_by_random_receipt_id(tmp_path):
    from linkloom.agents.graph_result import project_meeting_result
    given=public_fixture();given['confirmed_decisions']=[]
    given['meetings'][1]['date']='2026-04-01';given['confirmed_tasks'][0]['source_decision']=None
    path=tmp_path/'same-date.sqlite';built=build_meetings(given,path,recorded_at='2026-11-03T00:00:00+08:00');ws=built['workspace_id']
    graph=SQLiteGraphStore(path,authorized_workspace=ws,authorize_source=lambda *a:True,task_candidate_source=task_candidate_source())
    snapshot=graph.load_edges(ws);task=next(n for n in snapshot.nodes if n.kind=='task')
    step=dict(id='owner',operation='inspect',input={'node':task.node_id},as_of=None)
    result=graph_query(graph,[step],'2026-04-02',ws,outputs=[dict(step='owner',field='owner')])
    assert result['status']=='CONFLICT' and result['steps'][0]['evidence']
    assert {s['fields']['owner'] for s in result['steps'][0]['candidates']}=={'安然','白宁'}
    view,evidence=project_meeting_result(result)
    assert '安然' in view['answer'] and '白宁' in view['answer'] and evidence
