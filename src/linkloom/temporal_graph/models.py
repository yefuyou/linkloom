"""Graph contracts. A plan never establishes a relation or grants access."""
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
import hashlib
import json

EDGE_TYPES=frozenset({'owner_of','reports_to','supersedes','alias_of','same_task','decided_in','assigned_in','derived_from'})
NODE_TYPES=frozenset({'person','project','plan','task','task_observation','alias','decision_record','meeting'})


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf8')).hexdigest()


def required(value, name, limit=512):
    if not isinstance(value,str) or not value.strip() or len(value)>limit or '\x00' in value:
        raise ValueError('INVALID_'+name.upper())
    return value.strip()


def parse_time(value):
    if value is None: return None
    if not isinstance(value,str): raise ValueError('INVALID_TIME')
    if len(value)==10: return date.fromisoformat(value)
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.utcoffset() is None: raise ValueError('TIMEZONE_REQUIRED')
    return parsed.astimezone(UTC)


def interval(value):
    parsed=parse_time(value)
    if isinstance(parsed,datetime): return parsed,parsed
    if isinstance(parsed,date):
        start=datetime(parsed.year,parsed.month,parsed.day,tzinfo=UTC)
        return start,start+timedelta(days=1)
    raise ValueError('TIME_REQUIRED')


@dataclass(frozen=True,slots=True)
class Node:
    workspace_id: str
    kind: str
    key: str
    label: str
    scope: str=''

    def __post_init__(self):
        if self.kind not in NODE_TYPES: raise ValueError('INVALID_NODE_TYPE')
        for name in ('workspace_id','key','label'): required(getattr(self,name),name)
        if not isinstance(self.scope,str) or len(self.scope)>512: raise ValueError('INVALID_SCOPE')

    @property
    def node_id(self):
        return 'node_'+digest([self.workspace_id,self.kind,self.key,self.scope])[:32]

    def to_dict(self): return dict(node_id=self.node_id,**asdict(self))


@dataclass(frozen=True,slots=True)
class Evidence:
    workspace_id: str
    evidence_id: str
    source_id: str
    source_version_id: str
    content_sha256: str
    quote: str
    quote_sha256: str
    line_start: int
    line_end: int
    role: str='source'

    def __post_init__(self):
        for name in ('workspace_id','evidence_id','source_id','source_version_id'):
            required(getattr(self,name),name)
        if (not isinstance(self.quote,str) or not self.quote or
            hashlib.sha256(self.quote.encode('utf8')).hexdigest()!=self.quote_sha256):
            raise ValueError('QUOTE_HASH_MISMATCH')
        if len(self.content_sha256)!=64 or any(c not in '0123456789abcdef' for c in self.content_sha256):
            raise ValueError('INVALID_CONTENT_HASH')
        if type(self.line_start) is not int or type(self.line_end) is not int or not 1<=self.line_start<=self.line_end:
            raise ValueError('INVALID_EVIDENCE_POSITION')
        if self.role not in {'source','human_identity','blocker'}: raise ValueError('INVALID_EVIDENCE_ROLE')

    def to_dict(self): return asdict(self)


@dataclass(frozen=True,slots=True)
class Edge:
    source: Node
    target: Node
    edge_type: str
    valid_from: str | None
    valid_to: str | None
    valid_time_kind: str
    source_record_id: str
    confirmation_ref: str
    evidence: tuple[Evidence,...]
    blocker: bool=False
    time_basis: str='CONFIRMED_VALID_TIME'

    def __post_init__(self):
        if self.edge_type not in EDGE_TYPES: raise ValueError('INVALID_EDGE_TYPE')
        if self.source.workspace_id!=self.target.workspace_id: raise ValueError('CROSS_WORKSPACE_EDGE')
        a,b=self.source.kind,self.target.kind
        allowed={'owner_of':a=='person' and b in {'project','plan','task'},
          'reports_to':a==b=='person','supersedes':a==b and a in {'plan','task','decision_record'},
          'alias_of':a=='alias' and b in {'project','plan','task'},
          'same_task':a=='task_observation' and b=='task',
          'decided_in':a=='decision_record' and b=='meeting',
          'assigned_in':a=='task' and b=='meeting','derived_from':a=='task' and b=='decision_record'}
        if not allowed[self.edge_type]: raise ValueError('EDGE_ENDPOINT_TYPE_MISMATCH')
        if self.source.scope and self.target.scope and self.source.scope!=self.target.scope:
            raise ValueError('CROSS_BUSINESS_SCOPE_EDGE')
        required(self.source_record_id,'record_id'); required(self.confirmation_ref,'confirmation_ref')
        if (not isinstance(self.evidence,tuple) or not self.evidence or
            any(e.workspace_id!=self.source.workspace_id for e in self.evidence)):
            raise ValueError('VERIFIED_EVIDENCE_REQUIRED')
        if self.valid_time_kind not in {'interval','snapshot','atemporal'}: raise ValueError('INVALID_TIME_KIND')
        if self.valid_time_kind=='atemporal':
            if self.valid_from is not None or self.valid_to is not None: raise ValueError('INVALID_ATEMPORAL_BOUND')
        elif self.valid_from is None: raise ValueError('TIME_NOT_CONFIRMED')
        else:
            start=parse_time(self.valid_from)
            if self.valid_to is not None:
                end=parse_time(self.valid_to)
                if type(start)!=type(end) or (end<=start and self.valid_time_kind!='snapshot') or end<start:
                    raise ValueError('INVALID_VALIDITY_INTERVAL')

    @property
    def edge_id(self): return 'edge_'+digest(self.to_dict(include_id=False))[:32]

    def to_dict(self,include_id=True):
        result=dict(source=self.source.to_dict(),target=self.target.to_dict(),edge_type=self.edge_type,
          valid_from=self.valid_from,valid_to=self.valid_to,valid_time_kind=self.valid_time_kind,
          source_record_id=self.source_record_id,confirmation_ref=self.confirmation_ref,
          evidence=[e.to_dict() for e in self.evidence],blocker=self.blocker,time_basis=self.time_basis)
        return dict(edge_id=self.edge_id,**result) if include_id else result

    def time_match(self,as_of):
        if self.valid_time_kind=='atemporal': return 'VALID'
        query=parse_time(as_of); start=parse_time(self.valid_from)
        if self.valid_time_kind=='snapshot':
            if type(query)!=type(start):
                qday=query.date() if isinstance(query,datetime) else query
                sday=start.date() if isinstance(start,datetime) else start
                return 'PRECISION_MISMATCH' if qday==sday else 'OUTSIDE'
            return 'VALID' if query==start else 'OUTSIDE'
        left,right=interval(as_of); first,_=interval(self.valid_from)
        last=interval(self.valid_to)[0] if self.valid_to else None
        if isinstance(query,date) and not isinstance(query,datetime):
            if first>=right or (last is not None and last<=left): return 'OUTSIDE'
            if first>left or (last is not None and last<right): return 'PRECISION_MISMATCH'
            return 'VALID'
        return 'VALID' if first<=left and (last is None or left<last) else 'OUTSIDE'


@dataclass(frozen=True,slots=True)
class GraphSnapshot:
    workspace_id: str
    edges: tuple[Edge,...]
    exclusions: tuple[dict,...]=()
    catalogue: tuple[Node,...]=()
    records: tuple[dict,...]=()

    @property
    def nodes(self):
        return tuple(sorted({n.node_id:n for n in (*self.catalogue,*(n for e in self.edges for n in (e.source,e.target)))}.values(),key=lambda n:n.node_id))


@dataclass(frozen=True,slots=True)
class GraphLimits:
    max_hops: int=3
    max_subgoals: int=12
    max_relation_queries: int=8
    max_fanout: int=4
    max_edges: int=2048
    max_evidence_bytes: int=131072
    max_nodes: int=128

    def __post_init__(self):
        for name,ceiling in [('max_hops',3),('max_subgoals',12),('max_relation_queries',8),('max_fanout',4),('max_edges',10000),
                             ('max_evidence_bytes',262144),('max_nodes',128)]:
            value=getattr(self,name)
            if type(value) is not int or not 1<=value<=ceiling: raise ValueError('INVALID_LIMIT_'+name)


@dataclass(frozen=True,slots=True)
class GraphPlan:
    steps: tuple[dict,...]
    outputs: tuple[dict,...]
    as_of: str | None=None

    @classmethod
    def from_dict(cls,data,*,nodes=None,limits=None):
        limits=limits or GraphLimits()
        if not isinstance(data,dict) or set(data)!={'steps','outputs','as_of'}: raise ValueError('INVALID_GRAPH_PLAN')
        parse_time(data['as_of'])
        rows,outputs=data['steps'],data['outputs']
        if not isinstance(rows,list) or not 1<=len(rows)<=min(limits.max_subgoals,limits.max_relation_queries): raise ValueError('SUBGOAL_LIMIT')
        known={n.node_id:n for n in nodes} if nodes is not None else None
        prior={}; depths={}; types={}; signatures=set(); children={}
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'id','input','edge_type','direction','as_of'}: raise ValueError('INVALID_STEP_FIELDS')
            key=required(row['id'],'step_id',64)
            if key!=row['id'] or not key.isidentifier() or key in prior: raise ValueError('INVALID_STEP_ID')
            if row['edge_type'] not in EDGE_TYPES or row['direction'] not in {'in','out'}: raise ValueError('INVALID_RELATION_DIRECTION')
            parse_time(row['as_of'])
            inp=row['input']
            if not isinstance(inp,dict) or len(inp)!=1: raise ValueError('INVALID_STEP_INPUT')
            if set(inp)=={'node'}:
                if not isinstance(inp['node'],str) or (known is not None and inp['node'] not in known): raise ValueError('UNKNOWN_NODE')
                depths[key]=1
            elif set(inp)=={'step'} and inp['step'] in prior:
                depths[key]=depths[inp['step']]+1
                children[inp['step']]=children.get(inp['step'],0)+1
                if children[inp['step']]>limits.max_fanout: raise ValueError('FANOUT_LIMIT_EXCEEDED')
            else: raise ValueError('PRIOR_DEPENDENCY_REQUIRED')
            shapes={'owner_of':({'person'},{'project','plan','task'}),
                'reports_to':({'person'},{'person'}),'supersedes':({'plan','task','decision_record'},{'plan','task','decision_record'}),
                'alias_of':({'alias'},{'project','plan','task'}),'same_task':({'task_observation'},{'task'}),
                'decided_in':({'decision_record'},{'meeting'}),'assigned_in':({'task'},{'meeting'}),
                'derived_from':({'task'},{'decision_record'})}
            left,right=shapes[row['edge_type']]
            if row['direction']=='in': left,right=right,left
            if 'node' in inp and known is not None:
                root=known[inp['node']]
                duplicates=[n for n in known.values() if n.kind==root.kind and n.label.casefold()==root.label.casefold()]
                if len(duplicates)>1: raise ValueError('AMBIGUOUS_ROOT_ENTITY')
                incoming={root.kind}
            elif 'step' in inp: incoming=types[inp['step']]
            else: incoming=left
            if not incoming & left: raise ValueError('RELATION_TYPES_DO_NOT_CONNECT')
            types[key]=incoming & left if row['edge_type']=='supersedes' else right
            signature=digest([inp,row['edge_type'],row['direction'],row['as_of']])
            if signature in signatures: raise ValueError('DUPLICATE_STEP')
            signatures.add(signature)
            if depths[key]>limits.max_hops: raise ValueError('HOP_LIMIT_EXCEEDED')
            if row in list(prior.values()): raise ValueError('DUPLICATE_STEP')
            prior[key]=row
        if not isinstance(outputs,list) or not outputs or len(outputs)>limits.max_subgoals*3: raise ValueError('INVALID_OUTPUTS')
        for row in outputs:
            if (not isinstance(row,dict) or set(row)!={'step','field'} or row['step'] not in prior
                or row['field'] not in {'node','valid_from','valid_to'}): raise ValueError('INVALID_OUTPUT_SELECTOR')
        return cls(tuple(json.loads(json.dumps(rows))),tuple(json.loads(json.dumps(outputs))),data['as_of'])

    def to_dict(self): return dict(steps=list(self.steps),outputs=list(self.outputs),as_of=self.as_of)
