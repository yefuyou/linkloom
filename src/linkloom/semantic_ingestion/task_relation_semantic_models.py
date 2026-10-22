"""Untrusted semantic proposals and explicit, bounded authorization snapshots."""
from __future__ import annotations

from dataclasses import asdict,dataclass,field
from typing import TypeAlias

from linkloom.agents.model_adapter import ModelUsage
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import EvidenceSpan,RawArtifact
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef,ProjectRef,ChangeRef,canonical_hash

Endpoint:TypeAlias=ObservationRef|ProjectRef|ChangeRef
PREDICATES=frozenset({'SAME_TASK','RELATED_TO','AFFECTED_BY','REVISES_PLAN','IN_PROJECT','PROJECT_ALIAS'})
VERDICTS=frozenset({'SUPPORTED','REFUTED','UNKNOWN','CONFLICT'})
PROMPT_VERSION='task-relation-semantics/v1'


@dataclass(frozen=True,slots=True)
class SemanticLimits:
    max_artifacts:int=32
    max_observations:int=64
    max_endpoints:int=80
    max_allowed_chars:int=12000
    max_input_bytes:int=24000
    max_input_tokens:int=32768
    max_output_tokens:int=8192
    max_proposals:int=24

    def __post_init__(self):
        for name,value in asdict(self).items():
            if type(value) is not int or not 1<=value<=1048576:
                raise ValueError('invalid semantic limit: '+name)


@dataclass(frozen=True,slots=True)
class SemanticQuery:
    predicate:str
    subject:str
    object:str

    def __post_init__(self):
        if self.predicate not in PREDICATES or not self.subject or not self.object:
            raise ValueError('invalid semantic query')


@dataclass(frozen=True,slots=True)
class SemanticContext:
    workspace_id:str
    question:str
    artifacts:tuple[RawArtifact,...]
    observations:tuple[TaskObservation,...]
    endpoints:tuple[tuple[str,Endpoint],...]
    allowed_spans:tuple[tuple[str,EvidenceSpan],...]
    source_registry:SourceReferenceRegistry
    authorized_artifact_versions:frozenset[str]
    inventory_complete:bool
    truncated:bool
    queries:tuple[SemanticQuery,...]=()
    revoked_observation_ids:frozenset[str]=frozenset()
    revoked_evidence_refs:frozenset[str]=frozenset()
    limits:SemanticLimits=field(default_factory=SemanticLimits)

    def __post_init__(self):
        if (not isinstance(self.workspace_id,str) or not isinstance(self.question,str)
            or not isinstance(self.limits,SemanticLimits) or not isinstance(self.source_registry,SourceReferenceRegistry)
            or type(self.inventory_complete) is not bool or type(self.truncated) is not bool):
            raise ValueError('invalid semantic context contract')
        for values,kind in ((self.artifacts,RawArtifact),(self.observations,TaskObservation),(self.queries,SemanticQuery)):
            if not isinstance(values,tuple) or any(not isinstance(v,kind) for v in values):
                raise ValueError('invalid context inventory')
        for values,kinds in ((self.endpoints,(ObservationRef,ProjectRef,ChangeRef)),(self.allowed_spans,(EvidenceSpan,))):
            if (not isinstance(values,tuple) or any(not isinstance(v,tuple) or len(v)!=2
                or not isinstance(v[0],str) or not isinstance(v[1],kinds) for v in values)):
                raise ValueError('invalid context bindings')
        for values in (self.authorized_artifact_versions,self.revoked_observation_ids,self.revoked_evidence_refs):
            if not isinstance(values,frozenset) or any(not isinstance(v,str) or not v for v in values):
                raise ValueError('invalid context scope set')

    @property
    def source_snapshot_hash(self):
        return canonical_hash(dict(workspace_id=self.workspace_id,
            sources=sorted((a.artifact_id,a.artifact_version_id,a.content_hash) for a in self.artifacts),
            observations=sorted(canonical_hash(ObservationRef.from_observation(o).to_dict()) for o in self.observations),
            endpoints=sorted((name,canonical_hash(ref.to_dict())) for name,ref in self.endpoints),
            spans=sorted((name,canonical_hash(asdict(span))) for name,span in self.allowed_spans),
            authorized=sorted(self.authorized_artifact_versions),revoked_observations=sorted(self.revoked_observation_ids),
            revoked_evidence=sorted(self.revoked_evidence_refs),
            inventory_complete=self.inventory_complete,truncated=self.truncated))

    def model_input(self):
        """Expose only explicitly permitted original spans, not complete artifacts."""
        observations={ObservationRef.from_observation(o):o for o in self.observations}
        bindings=[]
        for handle,reference in self.endpoints:
            row=dict(handle=handle,reference=reference.to_dict())
            if isinstance(reference,ObservationRef) and reference in observations:
                o=observations[reference]
                row['task']=dict(raw_text=o.raw_text,description=o.description,owner=o.owner,
                    project=o.project,deadline=o.deadline,deadline_text=o.deadline_text,
                    deadline_precision=o.deadline_precision,deadline_role=o.deadline_role)
            bindings.append(row)
        return dict(workspace_id=self.workspace_id,question=self.question,source_snapshot_hash=self.source_snapshot_hash,
            endpoints=bindings,queries=[asdict(q) for q in self.queries],
            allowed_evidence=[dict(span_id=name,source=asdict(span)) for name,span in self.allowed_spans],
            inventory_complete=self.inventory_complete,truncated=self.truncated,
            scope='Only the supplied bounded snapshot; not exhaustive discovery.')


@dataclass(frozen=True,slots=True)
class SemanticCitation:
    span:EvidenceSpan
    quote:str

    def to_dict(self):
        return dict(span=asdict(self.span),quote=self.quote)


@dataclass(frozen=True,slots=True)
class SemanticProposal:
    predicate:str
    subject:Endpoint
    object:Endpoint
    proposed_verdict:str
    supporting_evidence:tuple[SemanticCitation,...]
    counter_evidence:tuple[SemanticCitation,...]
    missing_evidence:tuple[str,...]
    reason_codes:tuple[str,...]
    changed_fields:tuple[str,...]
    model_id:str
    actual_model_version:str|None
    prompt_version:str
    source_snapshot_hash:str
    usage:ModelUsage

    @property
    def stage(self):
        return 'MODEL_PROPOSED'

    @property
    def proposal_id(self):
        data=self.to_dict()
        data.pop('usage')
        return 'semantic_proposal_v1_'+canonical_hash(data)

    def to_dict(self):
        return dict(schema='task-semantic-proposal/v1',stage=self.stage,execution_status='EXECUTED',
            authority='SUPPORTING_ONLY',predicate=self.predicate,subject=self.subject.to_dict(),object=self.object.to_dict(),
            proposed_verdict=self.proposed_verdict,supporting_evidence=[q.to_dict() for q in self.supporting_evidence],
            counter_evidence=[q.to_dict() for q in self.counter_evidence],missing_evidence=list(self.missing_evidence),
            reason_codes=list(self.reason_codes),changed_fields=list(self.changed_fields),model_id=self.model_id,
            actual_model_version=self.actual_model_version,prompt_version=self.prompt_version,
            source_snapshot_hash=self.source_snapshot_hash,usage=self.usage.to_dict())


@dataclass(frozen=True,slots=True)
class SemanticResult:
    status:str
    proposals:tuple[SemanticProposal,...]=()
    errors:tuple[str,...]=()
    usage:ModelUsage=field(default_factory=ModelUsage)
    provider_calls:int=0
    request_sha256:str|None=None
    prompt_sha256:str|None=None
    source_snapshot_hash:str|None=None
    elapsed_ms:float|None=None
    schema_sha256:str|None=None
    response_sha256:str|None=None
    provider_request_id:str|None=None
    provider_response_id:str|None=None
    untrusted_output:dict|None=None

    def to_dict(self):
        return dict(schema='task-semantic-result/v1',status=self.status,proposals=[p.to_dict() for p in self.proposals],
            errors=list(self.errors),usage=self.usage.to_dict(),provider_calls=self.provider_calls,
            request_sha256=self.request_sha256,prompt_sha256=self.prompt_sha256,
            source_snapshot_hash=self.source_snapshot_hash,elapsed_ms=self.elapsed_ms,
            schema_sha256=self.schema_sha256,response_sha256=self.response_sha256,
            provider_request_id=self.provider_request_id,provider_response_id=self.provider_response_id,
            untrusted_output=self.untrusted_output)
