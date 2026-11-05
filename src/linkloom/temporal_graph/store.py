"""Replaceable GraphStore reading existing confirmations; never creates tables."""
from contextlib import closing
from pathlib import Path
from typing import Protocol
import sqlite3
from linkloom.temporal_graph.models import EDGE_TYPES,GraphLimits,GraphSnapshot
from linkloom.temporal_graph.projection import project_records

class GraphStore(Protocol):
    def load_edges(self,workspace,as_of,edge_types): ...

class SQLiteGraphStore:
    def __init__(self,database_path,*,authorized_workspace,authorize_source,limits=None,task_candidate_source=None,mention_locator=None):
        self.mention_locator=mention_locator
        self.path=Path(database_path).resolve()
        if not self.path.is_file(): raise ValueError('EXISTING_DATABASE_REQUIRED')
        if not isinstance(authorized_workspace,str) or not authorized_workspace.strip() or not callable(authorize_source):
            raise ValueError('HOST_AUTHORIZATION_REQUIRED')
        self.workspace=authorized_workspace; self.authorize_source=authorize_source
        self.limits=limits or GraphLimits()
        if task_candidate_source is None:
            from linkloom.tasks.candidate_source import ProductTaskCandidateSource
            task_candidate_source=ProductTaskCandidateSource()
        self.task_candidate_source=task_candidate_source

    def load_edges(self,workspace,as_of=None,edge_types=EDGE_TYPES):
        if workspace!=self.workspace: raise PermissionError('WORKSPACE_NOT_AUTHORIZED')
        if not isinstance(edge_types,(set,frozenset,tuple,list)) or not set(edge_types)<=EDGE_TYPES:
            raise ValueError('INVALID_EDGE_TYPES')
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row; db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
            result=project_records(db,workspace,self.authorize_source,self.limits,self.task_candidate_source)
            db.rollback()
        edges=tuple(e for e in result.edges if e.edge_type in edge_types and
                    (as_of is None or e.time_match(as_of)!='OUTSIDE'))
        return GraphSnapshot(workspace,edges,result.exclusions,result.catalogue,result.records)


    def unconfirmed_context(self,workspace,entity_text):
        """Count matching pending proposals locally; never turn them into graph nodes."""
        if workspace!=self.workspace:raise PermissionError('WORKSPACE_NOT_AUTHORIZED')
        from linkloom.temporal_graph.grounding import folded
        from linkloom.temporal_graph.projection import _evidence
        from linkloom.semantic_ingestion.materialization import _candidate_view_from_row
        if not isinstance(entity_text,str) or not entity_text.strip():raise ValueError('ENTITY_TEXT_REQUIRED')
        refs=[];evidence={}
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic_candidate'").fetchone() is None:return dict(count=0,evidence=[],candidate_refs=[])
            for row in db.execute("SELECT * FROM semantic_candidate WHERE workspace_id=? AND workflow_state IN ('SUPPORTING_ONLY','PENDING_REVIEW','CONFLICT','APPROVED')",(workspace,)):
                try:
                    _,view,encoded,fingerprint=_candidate_view_from_row(dict(row))
                    if encoded!=row['payload_json'] or fingerprint!=row['candidate_fingerprint']:continue
                    if getattr(view.claim_type,'value',None)!='PROPOSAL' or not view.value or folded(entity_text) not in folded(view.value):continue
                    proofs=[_evidence(db,workspace,e.evidence_ref,self.authorize_source) for e in view.evidence]
                    if not proofs or set(view.decision_evidence_refs)!={p.evidence_id for p in proofs}:continue
                    valid=True
                    for proof,binding in zip(proofs,view.evidence):
                        if (binding.workspace_id!=workspace or proof.source_id!=binding.source_identity or proof.source_version_id!=binding.source_version or
                            proof.content_sha256!=binding.content_hash or proof.quote!=binding.quote or proof.quote_sha256!=binding.quote_sha256 or
                            proof.line_start!=binding.line_start or proof.line_end!=binding.line_end):valid=False;break
                        if binding.char_start is not None:
                            text=db.execute('SELECT content FROM ll_source_version WHERE workspace_id=? AND source_id=? AND artifact_version_id=?',(workspace,proof.source_id,proof.source_version_id)).fetchone()[0]
                            if text[binding.char_start:binding.char_end]!=proof.quote:valid=False;break
                    if not valid or not any(folded(entity_text) in folded(proof.quote) for proof in proofs):continue
                    refs.append(dict(candidate_id=row['candidate_id'],fingerprint=view.candidate_fingerprint,workflow_state=row['workflow_state']))
                    for proof in proofs:evidence[proof.evidence_id]=proof.to_dict()
                except (ValueError,TypeError,KeyError):continue
            db.rollback()
        return dict(count=len(refs),evidence=list(evidence.values()),candidate_refs=refs)
