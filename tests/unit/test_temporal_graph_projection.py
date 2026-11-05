import hashlib,json,sqlite3
from copy import deepcopy
import pytest
from tests.graph_eval_support import confirm_candidates
from linkloom.temporal_graph.store import SQLiteGraphStore
from linkloom.temporal_graph.query import graph_query


def fixture_public():
    docs=[dict(workspace_id='original',path='a.md',content='As of 2026-04-01 Ada is the accountable owner of Bay.'),
          dict(workspace_id='original',path='b.md',content='As of 2026-04-01 Ada reports directly to Bo.'),
          dict(workspace_id='original',path='c.md',content='Ada reports directly to Cora.')]
    facts=[dict(relation='owner_of',subject='bay',value='ada',valid_on='2026-04-01',temporal_kind='snapshot',project_scope=None),
           dict(relation='direct_leader_of',subject='ada',value='bo',valid_on='2026-04-01',temporal_kind='snapshot',project_scope=None),
           dict(relation='direct_leader_of',subject='ada',value='cora',valid_on=None,temporal_kind=None,project_scope=None)]
    return dict(split='dev',cases=[dict(case_id='DEV',workspace_id='original',documents=docs)],candidates=[
        dict(case_id='DEV',workspace_id='original',source_path=d['path'],candidate_id='c'+str(i),original_candidate=f)
        for i,(d,f) in enumerate(zip(docs,facts))])


def build(tmp_path):
    path=tmp_path/'eval.sqlite'; public=fixture_public(); before=deepcopy(public)
    confirmed=confirm_candidates(public,path)
    assert before==public and len(confirmed['confirmed'])==3
    workspace=confirmed['workspaces'][('DEV','original')]
    store=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True)
    return path,workspace,store


def test_every_candidate_unchanged_but_missing_time_not_a_trusted_edge(tmp_path,monkeypatch):
    path,workspace,store=build(tmp_path)
    import linkloom.agents.claim_grounding as legacy
    monkeypatch.setattr(legacy,'_source_facts',lambda *a:(_ for _ in ()).throw(AssertionError('raw parse forbidden')))
    before=hashlib.sha256(path.read_bytes()).hexdigest(); snapshot=store.load_edges(workspace)
    assert len(snapshot.edges)==2 and snapshot.exclusions[0]['reason']=='TIME_NOT_CONFIRMED'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM semantic_candidate").fetchone()[0]==3
        assert db.execute("SELECT count(*) FROM semantic_materialization_receipt").fetchone()[0]==2
        assert not db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'll_graph_%'").fetchall()
        original=json.loads(db.execute("SELECT reason FROM semantic_candidate_event WHERE event_type='HUMAN_APPROVAL' AND reason LIKE '%c2%'").fetchone()[0])
        assert original['original_candidate']['valid_on'] is None
    project=next(n for n in snapshot.nodes if n.kind=='project')
    rows=[dict(id='o',input={'node':project.node_id},edge_type='owner_of',direction='in',as_of=None),
          dict(id='l',input={'step':'o'},edge_type='reports_to',direction='out',as_of=None)]
    result=graph_query(store,rows,'2026-04-01',workspace)
    assert result['status']=='FOUND' and result['outputs'][0]['values'][0]['label']=='bo'


def test_simulation_uses_formal_production_receipts_and_gold_input_is_rejected(tmp_path):
    path,workspace,_=build(tmp_path)
    production=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True)
    assert len(production.load_edges(workspace).edges)==2
    with sqlite3.connect(path) as db:
        assert all(r[0].startswith(('candidate-decision-fact/','agent-memory-candidate/')) for r in db.execute('SELECT payload_version FROM semantic_candidate'))
    public=fixture_public(); public['expected']={}
    with pytest.raises(ValueError,match='GOLD'): confirm_candidates(public,tmp_path/'rejected.sqlite')
    assert not (tmp_path/'rejected.sqlite').exists()


def test_authorization_precedes_time_and_never_leaks_other_workspace(tmp_path):
    path,workspace,_=build(tmp_path)
    denied=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:False)
    snap=denied.load_edges(workspace,'2026-04-01')
    assert snap.edges==() and snap.exclusions==()
    with pytest.raises(PermissionError): denied.load_edges('other')


def test_source_and_confirmation_changes_visible_on_next_read_without_notifications(tmp_path):
    path,workspace,store=build(tmp_path)
    assert len(store.load_edges(workspace).edges)==2
    with sqlite3.connect(path) as db: db.execute("UPDATE semantic_candidate SET workflow_state='INVALIDATED' WHERE candidate_id=(SELECT candidate_id FROM semantic_candidate_event WHERE event_type='HUMAN_APPROVAL' AND reason LIKE '%c0%')")
    assert len(store.load_edges(workspace).edges)==1
    with sqlite3.connect(path) as db: db.execute("UPDATE ll_source_version SET content='tampered'")
    assert store.load_edges(workspace).edges==()


def test_real_human_approval_receipt_projects_production_records_without_raw_relation_parsing(tmp_path,monkeypatch):
    from datetime import UTC,datetime
    from linkloom.ui.product import ProductRepository
    from linkloom.decision_memory.store import TemporalDecisionStore
    from linkloom.decision_memory.sources import SourceReferenceRegistry
    from linkloom.semantic_ingestion.artifacts import RawArtifact,source_episode_id_for
    from linkloom.semantic_ingestion.document_text import parse_source_text
    from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor,ProviderRequestAuthorization,SemanticExtractionContext
    from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
    from linkloom.semantic_ingestion.relations import FrozenRelationResolver
    from linkloom.semantic_ingestion.validation import CandidateValidator
    from linkloom.semantic_ingestion.materialization import SemanticDecisionMaterializer
    from tests.unit.test_task_legacy_adapter import RecordedProvider
    path=tmp_path/'real.sqlite'; repository=ProductRepository(path)
    workspace=repository.create_workspace('confirmed')['workspace_id'];now=datetime(2026,11,2,tzinfo=UTC)
    text='Ada reports_to Bo; effective from 2026-04-01.'
    artifact=RawArtifact(workspace,'relation.md','document',text,ingestion_time=now)
    segments=parse_source_text(artifact).segments
    repository.create_source(workspace,artifact.artifact_id,'source',artifact,tuple(s.evidence_span for s in segments))
    registry=SourceReferenceRegistry();registry.register_episode(workspace,source_episode_id_for(artifact))
    for segment in segments:registry.register_evidence(workspace,segment.evidence_span.evidence_ref,
        content_hash=artifact.content_hash,source_version=artifact.artifact_version_id)
    claim=dict(claim_type='FACT',subject='Ada',relation='reports_to',value='Bo',temporal_status='EXPLICIT',
               valid_from='2026-04-01',valid_to=None,confidence=dict.fromkeys(('claim_type','entity','relation','temporal','overall'),.95))
    extractor=ProviderNeutralSemanticExtractor(RecordedProvider(claim),provider_id='offline',model_id='recorded',
      request_guard=lambda r:ProviderRequestAuthorization.approve(r,guard_id='offline',reservation_id='offline',estimated_cost_upper_bound_usd=0),
      request_guard_id='offline',max_attempts=1)
    try:
        with TemporalDecisionStore(path,source_registry=registry) as temporal:
            materializer=SemanticDecisionMaterializer(temporal)
            results=SemanticIngestionPipeline(extractor,CandidateValidator()).process_segment(segments[0],artifact,
                SemanticExtractionContext(workspace,FrozenRelationResolver(('reports_to',))))
            assert len(results)==1 and results[0].candidate is not None
            materializer.capture(results[0],expected_workspace_id=workspace,now=now)
            cid=results[0].candidate.candidate_id
            store=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True)
            assert store.load_edges(workspace).edges==()
            materializer.approve(workspace,cid,reviewer_id='human-reviewer',expected_candidate_fingerprint=materializer.candidate_fingerprint(workspace,cid),reason='checked original',now=now)
            materializer.materialize(workspace,cid,now=now)
            import linkloom.agents.claim_grounding as legacy
            monkeypatch.setattr(legacy,'_source_facts',lambda *a:(_ for _ in ()).throw(AssertionError('raw relation regex forbidden')))
            snapshot=store.load_edges(workspace)
            assert len(snapshot.edges)==1,snapshot.exclusions
            assert snapshot.edges[0].source.label=='Ada' and snapshot.edges[0].target.label=='Bo'
            assert snapshot.edges[0].confirmation_ref.startswith('semantic_receipt_')
            with repository._connection:
                repository._connection.execute("UPDATE ll_source_evidence SET quote=?,quote_sha256=? WHERE workspace_id=?",
                    ('effective from 2026-04-01.',hashlib.sha256(b'effective from 2026-04-01.').hexdigest(),workspace))
            assert store.load_edges(workspace).edges==()
            with repository._connection:
                repository._connection.execute("UPDATE ll_source_evidence SET quote=?,quote_sha256=? WHERE workspace_id=?",
                    (snapshot.edges[0].evidence[0].quote,snapshot.edges[0].evidence[0].quote_sha256,workspace))
                repository._connection.execute("UPDATE decision SET valid_from='2026-03-01T00:00:00+00:00' WHERE workspace_id=?",(workspace,))
            assert store.load_edges(workspace).edges==()

    finally:repository.close()


def test_simulated_confirmation_does_not_accept_same_source_quote_redirection(tmp_path):
    path,workspace,store=build(tmp_path)
    assert len(store.load_edges(workspace).edges)==2
    text='Ada'
    with sqlite3.connect(path) as db:
        db.execute('UPDATE ll_source_evidence SET quote=?,quote_sha256=? WHERE workspace_id=?',
                   (text,hashlib.sha256(text.encode()).hexdigest(),workspace))
    assert store.load_edges(workspace).edges==()


def test_real_formal_typed_records_connect_new_replacement_owner_combination(tmp_path):
    docs=[dict(workspace_id='w',path='replacement.md',content='New supersedes_plan Old; effective from 2026-04-01.'),
          dict(workspace_id='w',path='owner.md',content='Ada is the accountable owner_of_plan of Old; effective from 2026-04-01.')]
    facts=[dict(relation='supersedes_plan',subject='New',value='Old',valid_on='2026-04-01',temporal_kind='event',project_scope=None),
           dict(relation='owner_of_plan',subject='Old',value='Ada',valid_on='2026-04-01',temporal_kind='event',project_scope=None)]
    public=dict(split='dev',cases=[dict(case_id='typed',workspace_id='w',documents=docs)],candidates=[
        dict(case_id='typed',workspace_id='w',source_path=doc['path'],candidate_id='typed-'+str(i),original_candidate=fact)
        for i,(doc,fact) in enumerate(zip(docs,facts))])
    path=tmp_path/'typed.sqlite';confirmed=confirm_candidates(public,path);workspace=confirmed['workspaces'][('typed','w')]
    assert all(r['materialized'] for r in confirmed['confirmed'])
    store=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True)
    snapshot=store.load_edges(workspace)
    old_nodes=[n for n in snapshot.nodes if n.label=='Old']
    assert len(old_nodes)==1 and old_nodes[0].kind=='plan'
    root=next(n for n in snapshot.nodes if n.label=='New')
    rows=[dict(id='prior',input={'node':root.node_id},edge_type='supersedes',direction='out',as_of=None),
          dict(id='owner',input={'step':'prior'},edge_type='owner_of',direction='in',as_of=None)]
    result=graph_query(store,rows,'2026-04-01',workspace)
    assert result['status']=='FOUND' and result['outputs'][0]['values'][0]['label']=='Ada'


@pytest.mark.parametrize('invalid',['prior_approval','new_approval','prior_source','new_source','time_gap','receipt_lineage','none'])
def test_confirmed_record_lineage_checks_both_approvals_sources_and_time(tmp_path,invalid,monkeypatch):
    docs=[dict(workspace_id='w',path='first.md',content='Bay uses_vendor VendorA; effective from 2026-04-01.'),
          dict(workspace_id='w',path='second.md',content='Bay uses_vendor VendorB; effective from 2026-05-01.')]
    facts=[dict(relation='uses_vendor',subject='Bay',value='VendorA',valid_on='2026-04-01',temporal_kind='event',project_scope=None),
           dict(relation='uses_vendor',subject='Bay',value='VendorB',valid_on='2026-05-01',temporal_kind='event',project_scope=None)]
    public=dict(split='dev',cases=[dict(case_id='lineage',workspace_id='w',documents=docs)],candidates=[
        dict(case_id='lineage',workspace_id='w',source_path=doc['path'],candidate_id='record-'+str(i),original_candidate=fact)
        for i,(doc,fact) in enumerate(zip(docs,facts))])
    path=tmp_path/'lineage.sqlite';confirmed=confirm_candidates(public,path);workspace=confirmed['workspaces'][('lineage','w')]
    assert all(r['materialized'] for r in confirmed['confirmed'])
    store=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True)
    snapshot=store.load_edges(workspace)
    assert len(snapshot.edges)==1,snapshot.exclusions
    edge=snapshot.edges[0]
    assert edge.edge_type=='supersedes' and edge.source.kind==edge.target.kind=='decision_record'
    assert len(edge.evidence)==2
    with sqlite3.connect(path) as db:
        if invalid in {'prior_approval','new_approval'}:
            index=0 if invalid=='prior_approval' else 1
            # Production audit is append-only. Inject the missing-approval read
            # boundary rather than weaken or drop the immutable history trigger.
            import linkloom.temporal_graph.projection as projection
            original_rows=projection._rows
            candidate_id=confirmed['confirmed'][index]['formal_candidate_id']
            def no_approval(db,table,ws):
                rows=original_rows(db,table,ws)
                return [r for r in rows if not (table=='semantic_candidate_event' and
                    r['candidate_id']==candidate_id and r['event_type']=='HUMAN_APPROVAL')]
            monkeypatch.setattr(projection,'_rows',no_approval)
        elif invalid in {'prior_source','new_source'}:
            db.execute('UPDATE ll_source SET active=0 WHERE name=?',('first.md' if invalid=='prior_source' else 'second.md',))
        elif invalid=='time_gap':db.execute("UPDATE decision SET valid_to='2026-04-30T00:00:00+00:00' WHERE supersedes_id IS NULL")
        elif invalid=='receipt_lineage':db.execute('UPDATE decision SET supersedes_id=NULL WHERE supersedes_id IS NOT NULL')
    assert bool(store.load_edges(workspace).edges)==(invalid=='none')


@pytest.mark.parametrize('ambiguous',[False,True])
def test_formal_agent_identity_is_retained_and_same_label_cannot_join_people(tmp_path,monkeypatch,ambiguous):
    from linkloom.agents.memory_candidate import WorkspaceSubject
    original_create=WorkspaceSubject.create;person_count=[0]
    def create(cls,workspace,label,**kwargs):
        if label.casefold()=='ada':
            person_count[0]+=1
            return WorkspaceSubject('person-'+str(person_count[0] if ambiguous else 1),label)
        return original_create(workspace,label,**kwargs)
    monkeypatch.setattr(WorkspaceSubject,'create',classmethod(create))
    public=fixture_public();public['cases'][0]['documents'][2]['content']='As of 2026-04-02 Ada reports directly to Cora.'
    public['candidates'][2]['original_candidate'].update(valid_on='2026-04-02',temporal_kind='snapshot')
    path=tmp_path/'identity.sqlite';confirmed=confirm_candidates(public,path);workspace=confirmed['workspaces'][('DEV','original')]
    snapshot=SQLiteGraphStore(path,authorized_workspace=workspace,authorize_source=lambda *a:True).load_edges(workspace)
    if ambiguous:
        assert not any(e.edge_type in {'owner_of','reports_to'} for e in snapshot.edges)
        assert any(e['reason']=='ENDPOINT_IDENTITY_NOT_UNIQUE' for e in snapshot.exclusions)
    else:
        assert len([e for e in snapshot.edges if e.edge_type in {'owner_of','reports_to'}])==3
        ada=[n for n in snapshot.nodes if n.label=='ada']
        assert len(ada)==1 and ada[0].key=='person-1'
