"""Offline real API composition; synthetic sources and isolated sidecar only."""
from argparse import ArgumentParser
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from scripts.run_m2_final import write, digest
from tests.task_candidate_support import load_case, public_cases, five_line_safety_trial
from tests.task_relation_lifecycle_support import _revision_case
from linkloom.semantic_ingestion.task_relation_candidates import discover_candidates, revalidate_candidate, DiscoveryLimits
from linkloom.semantic_ingestion.task_relation_context import assess_relation
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_relation_models import ObservationRef
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    case = next(c for c in public_cases() if c['case_id']=='C02')
    artifacts, observations, registry, _, anchor = load_case(case)
    workspace = case['workspace_id']
    discovery = discover_candidates(workspace, observations=observations, artifacts=artifacts,
        source_registry=registry, anchor_ids=(anchor.observation_id,))
    candidate = next(c for c in discovery.candidates if c.predicate_hint=='RELATED_TO')
    valid = revalidate_candidate(candidate, workspace, observations=observations,
        artifacts=artifacts, source_registry=registry)
    endpoints = {ObservationRef.from_observation(o):o for o in observations}
    left, right = endpoints[candidate.subject], endpoints[candidate.object]
    common = dict(artifacts=artifacts, source_registry=registry, context_observations=observations)
    related = [assess_relation('RELATED_TO', a,b,evidence=candidate.evidence,**common)
               for a,b in ((left,right),(right,left))]
    identity = assess_same_task(left,right,**common)
    verified = next(r for r in related if r.verdict=='SUPPORTED')
    path = args.output/'candidate.sqlite'
    with TaskRelationStore(path) as store:
        store.put_assessment(verified, evidence=candidate.evidence)
    before_hash = digest(path)
    with RelationQuery(path, artifacts=artifacts, observations=observations, source_registry=registry) as query:
        before = query.get(workspace,verified.assessment_id).to_dict()
        confirmed_before = len(query.confirmed(workspace))
        readonly = query._connection.execute('PRAGMA query_only').fetchone()[0] == 1
    assert digest(path)==before_hash
    with TaskRelationStore(path) as store:
        previous = store.last_event(workspace,verified.assessment_id)
        store.append_event(RelationEvent.create(workspace_id=workspace,assessment_id=verified.assessment_id,
            event_type='RETRACTED',reason='synthetic explicit review revocation',actor='integration-reviewer',
            previous_event_id=previous.event_id,recorded_at=previous.recorded_at+timedelta(seconds=1)))
    with RelationQuery(path, artifacts=artifacts, observations=observations, source_registry=registry) as query:
        revoked = query.get(workspace,verified.assessment_id).to_dict()
        confirmed_revoked = len(query.confirmed(workspace))
    # Separate unrevoked sidecar demonstrates source invalidation independently.
    stale_path = args.output/'source-change.sqlite'
    with TaskRelationStore(stale_path) as store:
        store.put_assessment(verified,evidence=candidate.evidence)
    old_source = next(a for a in artifacts if a.artifact_id=='receipt.txt')
    changed = replace(old_source,content=old_source.content+'\n来源已变更。')
    for dependency in verified.dependencies:
        span = dependency.span
        if span.artifact_id==old_source.artifact_id:
            registry.register_evidence(workspace,span.evidence_ref,
                content_hash=changed.content_hash,source_version=changed.artifact_version_id)
    updated = tuple(changed if a==old_source else a for a in artifacts)
    candidate_after = revalidate_candidate(candidate,workspace,observations=observations,
        artifacts=updated,source_registry=registry)
    with RelationQuery(stale_path, artifacts=(*artifacts,changed),observations=observations,source_registry=registry) as query:
        stale = query.get(workspace,verified.assessment_id).to_dict()
        confirmed_stale = len(query.confirmed(workspace))
    write(args.output/'C02.json',dict(candidate=candidate.to_dict(),valid_before=valid,
        related_directions=[r.to_dict() for r in related],same_task=identity.to_dict(),
        persisted_read=before,confirmed_before=confirmed_before,query_only=readonly,query_db_bytes_unchanged=True,
        after_retraction=revoked,confirmed_after_retraction=confirmed_revoked,
        candidate_valid_after_source_change=candidate_after,after_source_change=stale,confirmed_after_source_change=confirmed_stale))
    case = next(c for c in public_cases() if c['case_id']=='C10')
    artifacts,observations,registry,labels,anchor = load_case(case)
    result = discover_candidates(case['workspace_id'],observations=observations,artifacts=artifacts,source_registry=registry)
    old = next(o for o in observations if labels[o.observation_id]=='O:old')
    fixed = assess_same_task(anchor,old,artifacts=artifacts,source_registry=registry,context_observations=observations)
    write(args.output/'C10.json',dict(discovery=result.to_dict(),fixed_pair=fixed.to_dict(),
        interpretation='NO_CANDIDATE_IS_NOT_A_NEGATIVE_RELATION; extraction excerpt omits the reference'))
    d04 = _revision_case('D04')
    discovery = discover_candidates(d04.case.workspace_id, observations=tuple(d04.case.observations.values()),
        artifacts=d04.case.artifacts,source_registry=d04.case.registry)
    write(args.output/'D04.json',dict(discovery=discovery.to_dict(),same_task=d04.identity.to_dict(),
        revision=d04.revision.to_dict(),comparison=d04.comparison.to_dict()))
    write(args.output/'D07-original.json',five_line_safety_trial())
    case = next(c for c in public_cases() if c['case_id']=='C01')
    artifacts, observations,registry,_,anchor = load_case(case)
    result = discover_candidates(case['workspace_id'], observations=observations,artifacts=artifacts,
        source_registry=registry,limits=DiscoveryLimits(max_candidates=1,max_per_observation=1))
    write(args.output/'budget.json',result.to_dict())
    assert before['current_supported'] and confirmed_before==1
    assert not revoked['current_supported'] and confirmed_revoked==0
    assert not stale['current_supported'] and confirmed_stale==0 and not candidate_after
    print('C02 persisted/reopened, revoked and stale; C10, D04, D07, budget recorded')


if __name__=='__main__':
    main()
