"""Local, request-scoped retrieval over the Host-verified graph directory only."""
from __future__ import annotations
from dataclasses import dataclass,asdict,replace
from threading import RLock
import math,os,unicodedata
from linkloom.temporal_graph.models import digest,required,interval
from linkloom.temporal_graph.grounding import folded


@dataclass(frozen=True)
class LocatorConfig:
    min_score:float=2
    min_margin:float=0.1
    min_identity_similarity:float=0.4
    def __post_init__(self):
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in asdict(self).values()):raise ValueError('FINITE_LOCATOR_THRESHOLDS_REQUIRED')
        if self.min_margin<0 or not -1<=self.min_identity_similarity<=1:raise ValueError('INVALID_LOCATOR_THRESHOLDS')


def mention_text(value,question=None):
    value=required(value,'mention',160)
    while value and (value[0].isspace() or unicodedata.category(value[0]).startswith('P')):value=value[1:]
    while value and (value[-1].isspace() or unicodedata.category(value[-1]).startswith('P')):value=value[:-1]
    if not value or question is not None and value not in question:raise ValueError('MENTION_NOT_LITERAL_IN_QUESTION')
    return value


def directory_rows(snapshot,kind,when,temporal_mode='current'):
    from linkloom.temporal_graph.meeting_query import _valid,_selected,_task_states,_meetings_exceed_as_of
    from linkloom.temporal_graph.dates import date_tokens,date_matches
    records={r['node_id']:r for r in snapshot.records};rows=[]
    for node in snapshot.nodes:
        if node.workspace_id!=snapshot.workspace_id:raise PermissionError('FOREIGN_WORKSPACE_NODE')
        if node.kind!=kind:continue
        record=records.get(node.node_id)
        if record:
            valid=not _meetings_exceed_as_of((record,),when) if kind=='meeting' else interval(record['valid_from'])[0]<=interval(when)[0] if kind=='decision_record' and temporal_mode=='history' else _valid(record,when)
            if not valid:continue
        else:
            incident=[e for e in snapshot.edges if node.node_id in {e.source.node_id,e.target.node_id}]
            if not any(e.time_match(when) in {'VALID','PRECISION_MISMATCH'} for e in incident):continue
        selected=_selected(record,when) if record else None
        if kind=='task' and record:
            titles=sorted({state['fields']['title'] for state in _task_states(record,when) if state.get('fields',{}).get('title')})
            if not titles:continue
            identity=' / '.join(titles)
        else:identity=(record.get('value') if kind=='decision_record' and record else None) or node.label
        context=[]
        if kind=='task' and selected and selected.get('source_decision'):context.append(selected['source_decision'].get('value',''))
        text='\n'.join([identity,*filter(None,context)])
        rows.append(dict(node_id=node.node_id,kind=kind,identity=identity,text=text,record=record))
    return rows


class LocalMentionLocator:
    """Reuse #53's BM25/vector/RRF and local base INT8, never remote rerank."""
    def __init__(self,config=None,*,embedder=None,reranker=None,model_dir=None,onnx_path=None):
        self.config=config or LocatorConfig();self.embedder=embedder;self.reranker=reranker
        self.model_dir=model_dir;self.onnx_path=onnx_path;self._lock=RLock()
        self._owns_reranker=reranker is None

    def _resources(self):
        if self.embedder is None:
            from linkloom.indexing import SentenceTransformerEmbedder
            self.embedder=SentenceTransformerEmbedder(os.environ.get('LINKLOOM_GRAPH_EMBEDDING_MODEL_DIR') or 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',local_files_only=True)
        if self.reranker is None:
            from huggingface_hub import snapshot_download
            from linkloom.rerank.model_cache import BASE_MODEL,REVISIONS,artifact_directory,artifact_identity,cache_root,valid_artifact
            from linkloom.rerank.local import LocalCrossEncoder
            from linkloom.rerank.pipeline import RerankPipeline
            model_dir=self.model_dir or os.environ.get('LINKLOOM_GRAPH_RERANK_MODEL_DIR')
            onnx_path=self.onnx_path or os.environ.get('LINKLOOM_GRAPH_RERANK_ONNX_PATH')
            if onnx_path is None:
                directory=artifact_directory(BASE_MODEL)
                if not valid_artifact(directory,artifact_identity(BASE_MODEL)):raise ValueError('LOCAL_RERANK_NOT_PREPARED')
                onnx_path=str(directory/'model-int8.onnx')
            if model_dir is None:model_dir=snapshot_download(BASE_MODEL,revision=REVISIONS[BASE_MODEL],cache_dir=str(cache_root()/'hub'),local_files_only=True)
            self.reranker=RerankPipeline('local',local=LocalCrossEncoder(BASE_MODEL,model_dir=model_dir,onnx_path=onnx_path,device='cpu',engine='onnx'))
            self.reranker._backend().prepare(timeout_s=30)
        if getattr(self.reranker,'mode',None)!='local':raise ValueError('LOCAL_RERANK_REQUIRED')

    def prepare(self):
        with self._lock:
            self._resources();self.embedder.encode(['local graph directory'])
            backend=self.reranker._backend()
            prepare=getattr(backend,'prepare',None)
            return prepare() if callable(prepare) else {}

    def rank(self,snapshot,text,kind,when,temporal_mode='current'):
        """Return all eligible scored identities; configuration cannot select facts."""
        from linkloom.temporal_graph.dates import date_tokens,date_matches
        from linkloom.indexing import BM25Index,IndexDocument,VectorIndex
        from linkloom.retrieval_v2.models import RetrievalQuery
        from linkloom.retrieval_v2.retrievers import BM25Retriever,DenseRetriever,HybridRetriever
        from linkloom.rerank.base import RerankCandidate,score_candidates,finite_score
        import numpy as np
        text=mention_text(text);rows=directory_rows(snapshot,kind,when,temporal_mode)
        tokens=date_tokens(text) if kind=='meeting' else []
        if len(tokens)>1:raise ValueError('ONE_MEETING_DATE_PER_MENTION')
        if tokens:
            literal=tokens[0][0]
            rows=[r for r in rows if r['record'] and date_matches(r['record']['meeting_date'],literal)]
        identity=digest(dict(nodes=[n.to_dict() for n in snapshot.nodes],records=list(snapshot.records),edges=[e.to_dict() for e in snapshot.edges]))
        if not rows:return dict(catalogue_sha256=identity,candidates=[],coverage='complete',local_backend='not_needed')
        with self._lock:
            self._resources()
            by_id={r['node_id']:r for r in rows}
            documents=[IndexDocument(snapshot.workspace_id,r['node_id'],r['node_id'],'/graph/'+r['node_id'],r['text'],r['node_id']) for r in rows]
            lexical=BM25Index();lexical.build(documents)
            dense=VectorIndex(self.embedder);dense.build([replace(d,content=by_id[d.resource_id]['identity']) for d in documents])
            hybrid=HybridRetriever(BM25Retriever(lexical),DenseRetriever(dense))
            # Request-local full directory coverage; no top-20 uniqueness claim.
            hybrid.candidate_k=len(documents)
            hits=hybrid.retrieve(RetrievalQuery(snapshot.workspace_id,text,top_k=len(documents)))
            vectors=np.asarray(self.embedder.encode([text,*[r['identity'] for r in rows]]),dtype=float)
            norms=np.linalg.norm(vectors,axis=1)
            if vectors.ndim!=2 or len(vectors)!=len(rows)+1 or not np.isfinite(vectors).all() or (norms==0).any():raise ValueError('INVALID_LOCAL_EMBEDDINGS')
            vectors=vectors/norms[:,None]
            similarities={r['node_id']:float(vectors[0]@vectors[index]) for index,r in enumerate(rows,1)}
            # Unrecalled identities could not silently compete for a unique bind.
            omitted=set(by_id)-{h.resource_id for h in hits}
            if any(similarities[key]>=self.config.min_identity_similarity for key in omitted):raise ValueError('INCOMPLETE_RETRIEVAL_COVERAGE')
            candidates=[RerankCandidate(hit,by_id[hit.resource_id]['text']) for hit in hits]
            scores={}
            for start in range(0,len(candidates),20):
                batch=candidates[start:start+20];ranked=self.reranker.rerank(text,batch)
                trace=self.reranker.last_trace
                if trace.get('status')!='success' or trace.get('backend')!='local':raise ValueError('LOCAL_RERANK_UNAVAILABLE')
                if len(ranked)!=len(batch) or {h.evidence_id for h in ranked}!={c.evidence.evidence_id for c in batch}:raise ValueError('INVALID_LOCAL_RERANK_IDENTITIES')
                for hit in ranked:scores[hit.evidence_id]=finite_score(hit.rerank_score)
            ranked=score_candidates(candidates,[scores[c.evidence.evidence_id] for c in candidates])
            return dict(catalogue_sha256=identity,coverage='complete',local_backend='local',candidates=[dict(node_id=h.resource_id,score=h.rerank_score,rrf_score=h.score,identity_similarity=similarities[h.resource_id],identity=by_id[h.resource_id]['identity'],identity_sha256=digest(by_id[h.resource_id]),exact=folded(text)==folded(by_id[h.resource_id]['identity'])) for h in ranked])

    def locate(self,snapshot,text,kind,when,temporal_mode='current'):
        text=mention_text(text)
        try:ranking=self.rank(snapshot,text,kind,when,temporal_mode)
        except PermissionError:raise
        except Exception as error:
            return dict(status='BLOCKED',mention=text,kind=kind,node_ids=[],reason='LOCAL_ENTITY_RETRIEVAL_UNAVAILABLE',failure_type=type(error).__name__,thresholds=asdict(self.config))
        result=dict(ranking,mention=text,kind=kind,thresholds=asdict(self.config),thresholds_sha256=digest(asdict(self.config)))
        candidates=[r for r in ranking['candidates'] if r['identity_similarity']>=self.config.min_identity_similarity]
        if not candidates or candidates[0]['score']<self.config.min_score:return dict(result,status='NOT_FOUND',node_ids=[],reason='NO_CONFIRMED_ENTITY_ABOVE_THRESHOLD')
        top=candidates[0];close=[r for r in candidates if top['score']-r['score']<=self.config.min_margin]
        # Same normalized identity is never broken by a relevance score difference.
        twins=[r for r in ranking['candidates'] if folded(r['identity'])==folded(top['identity'])]
        close=list({r['node_id']:r for r in close+twins}.values())
        if len(close)>4:return dict(result,status='LIMIT_EXCEEDED',node_ids=[],reason='ROOT_CANDIDATE_FANOUT_EXCEEDED')
        return dict(result,status='AMBIGUOUS' if len(close)>1 else 'FOUND',node_ids=sorted(r['node_id'] for r in close),reason='CLOSE_CONFIRMED_CANDIDATES' if len(close)>1 else None)

    def close(self):
        with self._lock:
            if self._owns_reranker and self.reranker is not None:self.reranker.close()


_DEFAULT=None
_DEFAULT_LOCK=RLock()
def default_locator():
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:_DEFAULT=LocalMentionLocator()
        return _DEFAULT


def validate_definition(value):
    from hashlib import sha256
    from linkloom.temporal_graph.models import NODE_TYPES
    if not isinstance(value,dict) or set(value)!={'text','kind','reference','temporal_mode','source'}:raise ValueError('INVALID_ENTITY_MENTION')
    if value['kind'] not in NODE_TYPES or value['reference'] not in {'direct','previous'} or value['temporal_mode'] not in {'current','history'}:raise ValueError('INVALID_ENTITY_MENTION')
    if value['reference']=='previous' and value['kind']!='meeting':raise ValueError('PREVIOUS_REQUIRES_MEETING')
    source=value['source']
    if not isinstance(source,dict) or set(source)!={'question','start','end','sha256'}:raise ValueError('MENTION_SOURCE_REQUIRED')
    required(source['question'],'question',2048);question=source['question']
    if type(source['start']) is not int or type(source['end']) is not int or not 0<=source['start']<source['end']<=len(question):raise ValueError('INVALID_MENTION_SOURCE_RANGE')
    if sha256(question.encode('utf8')).hexdigest()!=source['sha256'] or question[source['start']:source['end']]!=value['text'] or mention_text(value['text'],question)!=value['text']:raise ValueError('MENTION_SOURCE_MISMATCH')
    from linkloom.temporal_graph.dates import validate_date_mention
    validate_date_mention(question,source['start'],source['end'])
    return value


def resolve_mention_row(store,snapshot,definition,when,row,by_node,records,limits):
    from copy import deepcopy
    from linkloom.temporal_graph.meeting_query import _proof,_selected,_task_states,nearest_previous_meeting,_meetings_exceed_as_of
    definition=validate_definition(definition)
    locator=getattr(store,'mention_locator',None) or default_locator()
    result=locator.locate(snapshot,definition['text'],definition['kind'],when,definition['temporal_mode'])
    if definition['reference']=='previous' and result['status']=='FOUND':
        anchor=result['node_ids'][0];previous=nearest_previous_meeting(records,anchor)
        if _meetings_exceed_as_of([records[anchor],*[records[key] for key in previous['candidates']]],when):
            result=dict(result,status='NO_VALID_RECORD_AT_TIME',node_ids=[],reason='COMPARISON_EXCEEDS_AS_OF')
        else:
            result=dict(result,status=previous['status'],node_ids=previous['candidates'],reason=previous['reason'],anchor=anchor,baseline_scope='workspace',previous_verification=previous)
            row['evidence']+=_proof(records[anchor])
    eligible={r['node_id'] for r in directory_rows(snapshot,definition['kind'],when,definition['temporal_mode'])}
    if any(key not in eligible for key in result['node_ids']):raise ValueError('LOCATOR_RETURNED_INELIGIBLE_NODE')
    if len(result['node_ids'])>limits.max_fanout:result=dict(result,status='LIMIT_EXCEEDED',node_ids=[],reason='ROOT_CANDIDATE_FANOUT_EXCEEDED')
    row.update(status=result['status'],reason_code=result['reason'],root_resolution=result,requested_mention=deepcopy(definition))
    for key in result['node_ids']:
        if key not in by_node or by_node[key].kind!=definition['kind']:raise ValueError('HOST_LOCATOR_IDENTITY_MISMATCH')
        row['output_nodes'].append(by_node[key].to_dict());record=records.get(key)
        if record:
            selected=_selected(record,when)
            if selected is None:
                row.update(status='CONFLICT',reason_code='TIME_OR_FIELD_CONFLICT');row.setdefault('candidates',[]).extend(deepcopy(_task_states(record,when)) if record['kind']=='task' else [])
                row['evidence'] += [e for state in _task_states(record,when) for e in state['evidence']] if record['kind']=='task' else _proof(record)
            else:row['evidence']+=_proof(selected)
        incident=[e for e in snapshot.edges if key in {e.source.node_id,e.target.node_id} and e.time_match(when) in {'VALID','PRECISION_MISMATCH'}]
        if any(e.blocker or e.time_match(when)=='PRECISION_MISMATCH' for e in incident):
            row.update(status='CONFLICT',reason_code='CONFIRMED_COUNTER_EVIDENCE' if any(e.blocker for e in incident) else 'TIME_OR_FIELD_CONFLICT')
        if not record or row['status']=='CONFLICT':row['evidence'] += [p.to_dict() for edge in incident for p in edge.evidence]
    if row['status']=='NOT_FOUND':
        row['requested_entity']=dict(text=definition['text'],kind=definition['kind'])
        lookup=getattr(store,'unconfirmed_context',None)
        row['unconfirmed_context']=lookup(snapshot.workspace_id,definition['text']) if callable(lookup) else dict(count=0,evidence=[],candidate_refs=[])
        row['evidence']+=row['unconfirmed_context']['evidence']
    return row
