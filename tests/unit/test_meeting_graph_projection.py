"""Meeting provenance from actual confirmations; no frozen QA or model calls."""
from datetime import UTC,datetime,timedelta
import hashlib,json,sqlite3
import pytest
from tests.graph_eval_support import confirm_candidates
from linkloom.temporal_graph.store import SQLiteGraphStore
from tests.unit.test_task_legacy_adapter import app
from tests.unit.test_task_merge import setup,prepare,apply,undo
from tests.unit.test_task_rebind import NOW,original_task


def decision_fixture(tmp_path):
    text='Meeting date: 2026-04-01\nAda reports_to Bo; effective from 2026-04-01.'
    fact=dict(relation='reports_to',subject='Ada',value='Bo',valid_on='2026-04-01',temporal_kind='event',project_scope=None)
    public=dict(split='dev',cases=[dict(case_id='meeting',workspace_id='w',documents=[dict(workspace_id='w',path='M1.md',content=text)])],
        candidates=[dict(case_id='meeting',workspace_id='w',source_path='M1.md',candidate_id='d',original_candidate=fact)])
    path=tmp_path/'meeting.sqlite';confirmed=confirm_candidates(public,path);workspace=confirmed['workspaces'][('meeting','w')]
    store=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *args:True)
    return path,workspace,store


def test_decided_in_requires_materialization_event_and_current_source(tmp_path,monkeypatch):
    path,workspace,store=decision_fixture(tmp_path)
    snapshot=store.load_edges(workspace);membership=[e for e in snapshot.edges if e.edge_type=='decided_in']
    assert len(membership)==1,snapshot.exclusions
    assert membership[0].source.kind=='decision_record' and membership[0].target.kind=='meeting'
    assert membership[0].valid_from=='2026-04-01'
    assert membership[0].confirmation_ref.startswith('semantic_receipt')
    assert next(r for r in snapshot.records if r['kind']=='meeting')['meeting_date']=='2026-04-01'
    import linkloom.temporal_graph.meeting_projection as projection
    rows=projection._rows
    monkeypatch.setattr(projection,'_rows',lambda db,t,w:[r for r in rows(db,t,w)
        if not (t=='semantic_candidate_event' and r['event_type']=='MATERIALIZATION_COMMITTED')])
    assert not any(e.edge_type=='decided_in' for e in store.load_edges(workspace).edges)
    monkeypatch.undo()
    with sqlite3.connect(path) as db:db.execute("UPDATE ll_source_version SET content='changed'")
    assert not any(e.edge_type=='decided_in' for e in store.load_edges(workspace).edges)


def test_task_is_not_a_graph_node_before_human_create_and_assignment_has_receipt(app):
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    snapshot=store.load_edges(app.workspace)
    assert not any(n.kind=='task' for n in snapshot.nodes)
    assert any(r['kind']=='meeting' and r['pending_count']>=1 for r in snapshot.records)
    initial=original_task(app);snapshot=store.load_edges(app.workspace)
    assigned=[e for e in snapshot.edges if e.edge_type=='assigned_in']
    assert len(assigned)==1,snapshot.exclusions
    assert assigned[0].source.key==initial['task']['id']
    assert assigned[0].source_record_id in initial['review_history'][0]['task_event_ids']
    assert not any(e.edge_type=='derived_from' for e in snapshot.edges)
    assert not any(e.edge_type=='owner_of' for e in snapshot.edges)


def test_merge_assignment_is_multiple_historical_and_undo_closes_only_corresponding_edge(app):
    initial,artifact,identifier=setup(app)
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    merged=apply(app,prepare(app,initial['task']['id'],identifier))
    assigned=[e for e in store.load_edges(app.workspace).edges if e.edge_type=='assigned_in']
    assert len(assigned)==2,store.load_edges(app.workspace).exclusions
    undo(app,merged)
    historical=[e for e in store.load_edges(app.workspace,(NOW+timedelta(days=2)).isoformat()).edges if e.edge_type=='assigned_in']
    current=[e for e in store.load_edges(app.workspace,(NOW+timedelta(days=5)).isoformat()).edges if e.edge_type=='assigned_in']
    assert len(historical)==2 and len(current)==1


def test_same_source_without_confirmed_meeting_date_does_not_make_decided_in(tmp_path):
    from tests.unit.test_temporal_graph_projection import build
    _,workspace,store=build(tmp_path)
    assert not any(e.edge_type=='decided_in' for e in store.load_edges(workspace).edges)


@pytest.mark.parametrize('link_decision',[True,False])
def test_derived_from_only_uses_source_decision_in_human_approved_task_snapshot(tmp_path,link_decision):
    from linkloom.tasks.models import TaskCandidateInput,EvidenceReference
    from linkloom.tasks.review_models import CandidateSnapshot,digest
    from linkloom.tasks.store import TaskStore
    from linkloom.tasks.review import TaskReviewService
    path,workspace,graph=decision_fixture(tmp_path)
    original=graph.load_edges(workspace)
    decision=next(r for r in original.records if r['kind']=='decision_record')
    proof=decision['evidence'][0]
    evidence=EvidenceReference(workspace,proof['source_id'],proof['source_version_id'],proof['quote'],proof['quote_sha256'],
        line_start=proof['line_start'],line_end=proof['line_end'],source_sha256=proof['content_sha256'],meeting_time=datetime(2026,4,1,tzinfo=UTC))
    candidate=TaskCandidateInput('given-task',workspace,'Bo',(evidence,),owner='Ada',meeting_time=datetime(2026,4,1,tzinfo=UTC),
        source_decision_id=decision['record_id'] if link_decision else None)
    snapshot=CandidateSnapshot(candidate,digest(candidate.to_dict()),'{}')
    class GivenSource:
        def list_candidates(self,db,ws):return (snapshot,) if ws==workspace else ()
    store=TaskStore(path)
    graph=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *args:True,task_candidate_source=GivenSource())
    try:
        service=TaskReviewService(store,GivenSource())
        preview=service.preview_candidate(workspace,candidate.candidate_id)
        service.confirm(workspace,candidate.candidate_id,expected_candidate_fingerprint=preview['fingerprint'],
            reviewer='human',reason='checked source decision and task',now=datetime(2026,4,1,tzinfo=UTC))
        edges=[e for e in graph.load_edges(workspace).edges if e.edge_type=='derived_from']
        assert len(edges)==int(link_decision)
        if link_decision:
            assert edges[0].target.key==decision['record_id']
            with store._connection:store._connection.execute("UPDATE task SET source_decision_id='invented'")
            assert not any(e.edge_type=='derived_from' for e in graph.load_edges(workspace).edges)
    finally:store.close()


def test_human_edit_does_not_change_owner_before_review_time_or_claim_meeting_assignment(app):
    from tests.unit.test_task_rebind import original_task
    from linkloom.temporal_graph.query import graph_query
    initial=original_task(app);task_id=initial['task']['id']
    latest=initial['review_history'][-1]
    app.service.edit_fields(app.workspace,task_id,expected_review_revision=str(latest['sequence']),
        expected_candidate_fingerprint=latest['candidate_fingerprint'],
        reviewer='human-editor',reason='人工修改负责人',patch={'owner':'Bo'},now=NOW+timedelta(days=8))
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    snapshot=store.load_edges(app.workspace);task=next(n for n in snapshot.nodes if n.kind=='task')
    step=dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)
    before=graph_query(store,[step],(NOW+timedelta(days=2)).isoformat(),app.workspace,outputs=[dict(step='task',field='owner')])
    after=graph_query(store,[step],(NOW+timedelta(days=9)).isoformat(),app.workspace,outputs=[dict(step='task',field='owner')])
    assert before['outputs'][0]['values']==['李明']
    assert after['outputs'][0]['values']==['Bo']
    assert 'owner' in after['steps'][0]['records'][0]['current']['human_context_fields']
    assert len([e for e in snapshot.edges if e.edge_type=='assigned_in'])==1


def test_nested_merge_undo_restores_previous_confirmed_owner_state(app):
    from tests.unit.test_task_rebind import revise
    from tests.unit.test_task_merge import C_CONTENT,C_TEXT
    from linkloom.temporal_graph.query import graph_query
    initial,artifact,identifier=setup(app);task_id=initial['task']['id']
    first=apply(app,prepare(app,task_id,identifier),day=1)
    artifact,candidate=revise(app,content=C_CONTENT,text=C_TEXT,parts=('联调终版报告',),source_id='meeting-c.txt')
    value=prepare(app,task_id,candidate.task_observation.observation_id,day=2)
    second=apply(app,value,day=2)
    undo(app,second,day=4)
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    snapshot=store.load_edges(app.workspace);task=next(n for n in snapshot.nodes if n.kind=='task')
    step=dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)
    result=graph_query(store,[step],(NOW+timedelta(days=5)).isoformat(),app.workspace,outputs=[dict(step='task',field='owner')])
    assert result['status']=='FOUND' and result['outputs'][0]['values']==['王芳'],(result,snapshot.exclusions)


def test_source_churn_does_not_restore_old_task_approval_without_lazy_ui_refresh(app):
    initial=original_task(app)
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    assert any(e.edge_type=='assigned_in' for e in store.load_edges(app.workspace).edges)
    with app.repository._connection:
        app.repository._connection.execute("UPDATE ll_source SET active=0 WHERE workspace_id=?",(app.workspace,))
        app.repository._connection.execute("UPDATE ll_source SET active=1 WHERE workspace_id=?",(app.workspace,))
    # No TaskReviewService read is performed after the change. Graph must reject
    # the stale source epoch itself while remaining a read-only operation.
    snapshot=store.load_edges(app.workspace)
    assert not any(n.kind=='task' for n in snapshot.nodes)
    assert not any(e.edge_type in {'assigned_in','derived_from','same_task'} for e in snapshot.edges)


def test_invalid_latest_merge_source_cannot_extend_old_owner_as_current_without_ui_refresh(app):
    initial,artifact,identifier=setup(app)
    apply(app,prepare(app,initial['task']['id'],identifier))
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    assert any(n.kind=='task' for n in store.load_edges(app.workspace).nodes)
    with app.repository._connection:
        app.repository._connection.execute('UPDATE ll_source SET active=0 WHERE workspace_id=? AND source_id=?',(app.workspace,artifact.artifact_id))
    snapshot=store.load_edges(app.workspace)
    assert not any(n.kind=='task' for n in snapshot.nodes)
    assert not any(e.edge_type in {'assigned_in','derived_from','same_task'} for e in snapshot.edges)
    assert any(e['reason']=='NEEDS_REVALIDATION_CURRENT_TASK_BINDING' for e in snapshot.exclusions)


def test_meeting_list_keeps_original_deadline_and_marks_human_review_context(app):
    from tests.unit.test_task_legacy_adapter import preview
    from linkloom.temporal_graph.query import graph_query
    from linkloom.agents.graph_result import render_meeting_outputs,project_meeting_result
    app.service.confirm(app.workspace,app.observation.observation_id,expected_candidate_fingerprint=preview(app)['fingerprint'],
        reviewer='human',reason='修改截止日期',patch={'deadline':'2030-01-05','deadline_text':'1/5'},now=NOW)
    store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
    snapshot=store.load_edges(app.workspace);meeting=next(n for n in snapshot.nodes if n.kind=='meeting')
    row=dict(id='tasks',operation='traverse',input={'node':meeting.node_id},edge_type='assigned_in',direction='in',
        cardinality='list',selection='all',as_of=None)
    result=graph_query(store,[row],(NOW+timedelta(days=1)).isoformat(),app.workspace,outputs=[dict(step='tasks',field='record'),dict(step='tasks',field='deadline')])
    assert result['outputs'][1]['values']==['2026-10-12']
    assert '2026-10-12' in render_meeting_outputs(result)
    assert '2030-01-05' not in render_meeting_outputs(result)
    task=next(n for n in snapshot.nodes if n.kind=='task')
    inspect=dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)
    result=graph_query(store,[inspect],(NOW+timedelta(days=1)).isoformat(),app.workspace,outputs=[dict(step='task',field='deadline')])
    assert '2030-01-05' in render_meeting_outputs(result) and '人工修改' in render_meeting_outputs(result)
    assert project_meeting_result(result)[0]['evidence_level']=='HUMAN_REVIEW_WITH_SOURCE_CONTEXT'


def test_formal_same_source_rebind_retains_task_with_verified_current_revision(tmp_path):
    from tests.unit.test_task_legacy_adapter import seed,close
    from tests.unit.test_task_rebind import original_task,revise,binding_preview,apply_binding
    from linkloom.temporal_graph.store import SQLiteGraphStore
    app=seed(tmp_path/"rebind.sqlite")
    try:
        initial=original_task(app);_,candidate=revise(app)
        value=apply_binding(app,binding_preview(app,initial,candidate))
        store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
        snapshot=store.load_edges(app.workspace)
        assert any(n.kind=='task' and n.key==value['task']['id'] for n in snapshot.nodes),snapshot.exclusions
        assert any(e.edge_type=='assigned_in' and e.source.key==value['task']['id'] for e in snapshot.edges)
        # A later source revision invalidates this exact receipt again.
        revise(app,content='[2026-10-08T10:00:00+08:00] 主持人: 后续记录已变更。',extract=False)
        assert not any(n.kind=='task' for n in store.load_edges(app.workspace).nodes)
    finally:close(app)


def test_explicit_date_validity_uses_meeting_date_even_when_source_time_is_not_midnight(tmp_path):
    from tests.unit.test_task_legacy_adapter import seed,close,preview
    from tests.unit.test_task_rebind import NOW
    from linkloom.temporal_graph.store import SQLiteGraphStore
    from linkloom.temporal_graph.query import graph_query
    import json
    app=seed(tmp_path/"rebind.sqlite")
    try:
        candidate=preview(app)
        ref=candidate['candidate']['evidence'][0]
        reason=json.dumps(dict(graph_valid_time=dict(kind='date',value='2026-10-08'),
            meeting=dict(document_id=ref['source_id'],source_version_id=ref['source_version_id'],content_sha256=ref['source_sha256'],date='2026-10-08')))
        result=app.service.confirm(app.workspace,app.observation.observation_id,expected_candidate_fingerprint=candidate['fingerprint'],reviewer='human',reason=reason,now=NOW)
        store=SQLiteGraphStore(app.path,authorized_workspace=app.workspace,authorize_source=lambda *args:True)
        snapshot=store.load_edges(app.workspace);task=next(n for n in snapshot.nodes if n.kind=='task')
        step=dict(id='task',operation='inspect',input={'node':task.node_id},as_of=None)
        answer=graph_query(store,[step],'2026-10-08',app.workspace,outputs=[dict(step='task',field='owner')])
        assert answer['status']=='FOUND',answer
        record=next(r for r in snapshot.records if r['kind']=='task')
        assert record['states'][0]['valid_from']=='2026-10-08'
        assert record['states'][0]['source_event_time']=='2026-10-08T02:00:00+00:00'
        assert record['states'][0]['system_recorded_at'].startswith('2030-01-01')
        app.store.set_status(app.workspace,result['task']['id'],'done',actor='human',reason='完成',now=NOW+timedelta(days=1,hours=12))
        current=store.load_edges(app.workspace)
        assert any(n.kind=='task' for n in current.nodes)
        assert len([e for e in current.edges if e.edge_type=='owner_of'])==1
        during=graph_query(store,[step],'2030-01-02',app.workspace,outputs=[dict(step='task',field='status')])
        assert during['status']=='CONFLICT'
        after=graph_query(store,[step],'2030-01-03',app.workspace,outputs=[dict(step='task',field='status')])
        assert after['outputs'][0]['values']==['done']
    finally:close(app)


def test_task_owner_joins_existing_formally_confirmed_person_identity(tmp_path):
    from linkloom.tasks.models import TaskCandidateInput,EvidenceReference
    from linkloom.tasks.review_models import CandidateSnapshot,digest
    from linkloom.tasks.store import TaskStore
    from linkloom.tasks.review import TaskReviewService
    path,workspace,graph=decision_fixture(tmp_path)
    before=graph.load_edges(workspace);report=next(e for e in before.edges if e.edge_type=='reports_to')
    proof=report.evidence[0]
    ref=EvidenceReference(workspace,proof.source_id,proof.source_version_id,proof.quote,proof.quote_sha256,
        line_start=proof.line_start,line_end=proof.line_end,source_sha256=proof.content_sha256,meeting_time=datetime(2026,4,1,tzinfo=UTC))
    candidate=TaskCandidateInput('confirmed-task',workspace,'核对报告',(ref,),owner='Ada',meeting_time=datetime(2026,4,1,tzinfo=UTC))
    snapshot=CandidateSnapshot(candidate,digest(candidate.to_dict()),'{}')
    class Source:
        def list_candidates(self,db,ws):return (snapshot,) if ws==workspace else ()
    source=Source();tasks=TaskStore(path)
    try:
        service=TaskReviewService(tasks,source);preview=service.preview_candidate(workspace,candidate.candidate_id)
        service.confirm(workspace,candidate.candidate_id,expected_candidate_fingerprint=preview['fingerprint'],
            reviewer='human',reason=json.dumps(dict(graph_valid_time=dict(kind='date',value='2026-04-01'))),now=datetime(2026,6,1,tzinfo=UTC))
        graph=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True,task_candidate_source=source)
        current=graph.load_edges(workspace);owner=next(e for e in current.edges if e.edge_type=='owner_of')
        assert owner.source.node_id==report.source.node_id
        assert len([n for n in current.nodes if n.kind=='person' and n.label.casefold()=='ada'])==1
        from linkloom.temporal_graph.query import graph_query
        rows=[dict(id='owner',operation='traverse',input={'node':owner.target.node_id},edge_type='owner_of',direction='in',cardinality='single',selection='all',as_of=None),
            dict(id='manager',operation='traverse',input={'step':'owner'},edge_type='reports_to',direction='out',cardinality='single',selection='all',as_of=None)]
        result=graph_query(graph,rows,'2026-04-01',workspace,outputs=[dict(step='manager',field='node')])
        assert result['status']=='FOUND',result
        assert result['outputs'][0]['values'][0]['label'].casefold()=='bo'
    finally:tasks.close()
