"""Unmodified M3-S ambiguous source/F/G receipts, not new semantic Gold."""
import json
from pathlib import Path
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticContext

SOURCE=('[2034-03-05T09:00:00Z] 纪要: 棠原项目的页面巡检已经列入本轮工作，韩桥只负责报进度，实际执行人尚未定。2034-03-05是讨论会议日期，交付日期尚无约定。\n'
        '[2034-03-06T09:00:00Z] 纪要: 页面巡检这件事待确认由韩桥还是易秋执行；交付也有2034-03-12和2034-03-15两种说法，均未定案。只是讨论选项，巡检任务本身照常推进。')
QUESTION='页面巡检现在由谁执行，交付日期定了吗？两次会议的安排有什么变化？'

def saved_attempt():
    path=Path(__file__).resolve().parents[1]/'docs/requirements/m3-s-task-field-grounding/evidence/product-attempts.json'
    return next(a for a in json.loads(path.read_text(encoding='utf8'))['attempts']
                if a['campaign']=='m3s-live-phase3-20261022')

def saved_context():
    attempt=saved_attempt();discovery=attempt['discovery']
    observations=tuple(TaskObservation.from_dict(o) for o in (*discovery['valid_observations'],*discovery['partial_observations']))
    first=observations[0].provenance
    artifact=RawArtifact(first.workspace_id,first.artifact_id,'timestamped_text',SOURCE,source_uri=first.source_ref)
    assert all(o.verify(artifact) and o.owner is None and o.deadline_text is None for o in observations)
    spans=tuple((f'source_0:{i}',s.evidence_span) for i,s in enumerate(parse_source_text(artifact).segments,1))
    registry=SourceReferenceRegistry()
    for _,s in spans:
        registry.register_evidence(first.workspace_id,s.evidence_ref,content_hash=s.content_hash,source_version=s.artifact_version_id)
    return SemanticContext(first.workspace_id,QUESTION,(artifact,),observations,
        tuple((f'task_{i}',ObservationRef.from_observation(o)) for i,o in enumerate(observations)),
        spans,registry,frozenset({artifact.artifact_version_id}),True,False)

def saved_arguments():
    return saved_attempt()['planning'][0]['untrusted_output']['tool_calls'][0]['arguments']
