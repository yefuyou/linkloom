"""Real B -> P2 -> P4 calls over isolated synthetic sources."""
from linkloom.semantic_ingestion.task_relation_candidates import discover_candidates, revalidate_candidate
from linkloom.semantic_ingestion.task_relation_context import assess_relation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef
from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from tests.task_candidate_support import load_case, public_cases


def test_c02_space_reference_is_verified_in_both_directions(tmp_path):
    case = next(c for c in public_cases() if c['case_id'] == 'C02')
    artifacts, observations, registry, _, anchor = load_case(case)
    candidates = discover_candidates(case['workspace_id'], observations=observations,
        artifacts=artifacts, source_registry=registry, anchor_ids=(anchor.observation_id,))
    candidate = next(c for c in candidates.candidates if c.predicate_hint == 'RELATED_TO')
    assert candidate.to_dict()['relation_status'] == 'UNCONFIRMED'
    assert revalidate_candidate(candidate, case['workspace_id'], observations=observations,
        artifacts=artifacts, source_registry=registry)
    endpoints = {ObservationRef.from_observation(o): o for o in observations}
    left, right = endpoints[candidate.subject], endpoints[candidate.object]
    args = dict(artifacts=artifacts, source_registry=registry,
        context_observations=observations, evidence=candidate.evidence)
    directions = [assess_relation('RELATED_TO', a, b, **args) for a, b in ((left, right), (right, left))]
    assert sorted(r.verdict for r in directions) == ['SUPPORTED', 'UNKNOWN']
    verified = next(r for r in directions if r.verdict == 'SUPPORTED')
    path = tmp_path / 'relations.sqlite'
    with TaskRelationStore(path) as store:
        store.put_assessment(verified, evidence=candidate.evidence)
    query = RelationQuery(path, artifacts=artifacts, observations=observations, source_registry=registry)
    assert query.get(case['workspace_id'], verified.assessment_id).current_supported


def test_c10_missing_candidate_does_not_mean_refuted():
    from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
    case = next(c for c in public_cases() if c['case_id'] == 'C10')
    artifacts, observations, registry, labels, anchor = load_case(case)
    result = discover_candidates(case['workspace_id'], observations=observations,
        artifacts=artifacts, source_registry=registry, anchor_ids=(anchor.observation_id,))
    old = next(o for o in observations if labels[o.observation_id] == 'O:old')
    assert not result.candidates
    result = assess_same_task(anchor, old, artifacts=artifacts, source_registry=registry,
        context_observations=observations)
    assert result.verdict == 'UNKNOWN'
