"""P2-A work-witness identity, independent from existing regression modules."""
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
from linkloom.semantic_ingestion.task_field_binding import FIELD_ROLES
from linkloom.semantic_ingestion.task_observation import TaskObservation, TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef
from tests.integration.test_p2_work_identity_http import SOURCE, TEXT, WORK, task
from tests.unit.test_task_observation_discovery import setup


def source():
    return RawArtifact('public-workspace', 'p2-work', 'timestamped_text', SOURCE)


def observe(artifact, row, **kwargs):
    return build_task_observation(TaskProposal.from_dict(row['task']),
        parse_source_text(artifact).segments[0], artifact, **kwargs)


def discover(artifact, rows):
    service, _, _, _ = setup(artifact, rows)
    service.task_profile = TaskDiscoveryProfile.TASK_RECALL_V3
    return service.discover((artifact,), workspace_id=artifact.workspace_id)


def released(result):
    return (*result.valid_observations, *result.partial_observations)


def test_shared_raw_text_has_distinct_verified_work_ids_and_unknown_fields_survive():
    artifact = source()
    rows = [task(WORK[0], unknown=True), task(WORK[1])]
    observations = released(discover(artifact, rows))
    assert len(observations) == 2
    assert len({o.observation_id for o in observations}) == 2
    assert {o.raw_text for o in observations} == {TEXT}
    by_work = {o.description: o for o in observations}
    assert by_work[WORK[0]].owner is by_work[WORK[0]].deadline is None
    assert by_work[WORK[1]].owner == '乔雨' and by_work[WORK[1]].deadline == '2037-08-19'
    for o in observations:
        assert o.verify(artifact) and TaskObservation.from_dict(o.to_dict()) == o
        assert o.authority == 'SUPPORTING_ONLY' and o.relation_status == 'UNCONFIRMED'
        assert ObservationRef.from_dict(ObservationRef.from_observation(o).to_dict()) == ObservationRef.from_observation(o)


def test_candidate_order_and_reimport_time_do_not_change_work_identity():
    artifact = source()
    rows = [task(w) for w in WORK]
    first, second = discover(artifact, rows), discover(artifact, list(reversed(rows)))
    refs = lambda result: {o.description: ObservationRef.from_observation(o) for o in released(result)}
    assert len(refs(first)) == 2 and refs(first) == refs(second)
    later = replace(artifact, ingestion_time=artifact.ingestion_time.replace(year=2039))
    assert observe(artifact, rows[0]).observation_id == observe(later, rows[0]).observation_id
    assert ObservationRef.from_observation(observe(artifact, rows[0])) == ObservationRef.from_observation(observe(later, rows[0]))


def test_partial_work_and_fully_populated_valid_work_share_one_raw_fragment():
    text = '项目：砾石；供应商：汀云。' + TEXT
    artifact = replace(source(), content='[2037-08-01T09:00:00Z] 记录员: ' + text)
    partial, full = task(WORK[0], unknown=True), task(WORK[1])
    for row in (partial, full):
        row['task']['text'] = text
    full['task'].update(project='砾石', vendor='汀云')
    for binding in full['task']['field_bindings']:
        value = full['task']['deadline_text' if binding['field'] == 'deadline' else binding['field']]
        binding.update(value=value, role=FIELD_ROLES[binding['field']], state='ASSERTED', quote=text)
    result = discover(artifact, [partial, full])
    assert len(result.valid_observations) == len(result.partial_observations) == 1
    complete, incomplete = result.valid_observations[0], result.partial_observations[0]
    assert complete.description == WORK[1] and not complete.unknown_fields
    assert incomplete.description == WORK[0] and incomplete.owner is incomplete.deadline is None
    assert complete.raw_text == incomplete.raw_text and complete.observation_id != incomplete.observation_id
    assert complete.verify(artifact) and incomplete.verify(artifact)


def test_duplicate_work_is_one_observation_even_if_description_part_is_repeated():
    artifact = source()
    row = task(WORK[0])
    duplicate = json.loads(json.dumps(row))
    duplicate['task']['description_parts'] *= 2
    result = discover(artifact, [row, row, duplicate, task(WORK[1])])
    assert {o.description for o in released(result)} == set(WORK)
    assert len(released(result)) == 2
    assert any(f.reason == 'DUPLICATE_TASK_PROPOSAL' for f in result.failures)


def test_conflicting_same_work_is_quarantined_without_losing_other_work():
    artifact = source()
    original, conflict = task(WORK[0]), task(WORK[0])
    conflict['task']['field_bindings'][0]['role'] = 'HISTORICAL_OWNER'
    assert observe(artifact, original).observation_id == observe(artifact, conflict).observation_id
    for rows in ([original, conflict, task(WORK[1])], [task(WORK[1]), conflict, original]):
        result = discover(artifact, rows)
        assert [o.description for o in released(result)] == [WORK[1]]
        assert any(f.reason == 'OBSERVATION_EXTRACTION_CONFLICT' for f in result.failures)
        released_ids = {o.observation_id for o in released(result)}
        assert all(set(g.observation_ids) <= released_ids for g in result.groups)
        assert not result.coverage_complete


def test_work_witness_is_independent_of_raw_window_and_field_values():
    artifact = source()
    row = task(WORK[0])
    original = observe(artifact, row)
    row['task']['text'] = WORK[0]
    cropped = observe(artifact, row)
    assert original.observation_id == cropped.observation_id
    unknown = observe(artifact, task(WORK[0], unknown=True))
    assert unknown.observation_id == original.observation_id
    assert ObservationRef.from_observation(unknown) != ObservationRef.from_observation(original)


def test_work_evidence_order_is_canonical_and_not_a_new_task():
    artifact = source()
    row = task(WORK[0])
    row['task']['description_parts'] = list(WORK)
    original = observe(artifact, row)
    row['task']['description_parts'].reverse()
    reordered = observe(artifact, row)
    assert original == reordered and original.verify(artifact)


def test_ambiguous_work_occurrence_cannot_be_bound_to_first_match():
    artifact = replace(source(), content=SOURCE + WORK[0])
    row = task(WORK[0])
    row['task']['text'] = TEXT + WORK[0]
    result = discover(artifact, [row])
    assert not released(result)
    assert any('AMBIGUOUS' in f.reason for f in result.failures)


def test_v2_cannot_forge_location_or_identity_version_or_move_work_outside_raw():
    artifact = source()
    observation = observe(artifact, task(WORK[0]))
    assert observation.observation_id.startswith('task_observation_v2_')
    for changed_id in ('task_observation_v2_' + '0' * 64, 'task_observation_v3_' + '0' * 64,
                       'task_observation_v1_' + '0' * 64):
        with pytest.raises(ValueError, match='identity'):
            replace(observation, observation_id=changed_id)
    proof = next(e for e in observation.field_evidence if e.field == 'description')
    with pytest.raises(ValueError):
        replace(observation, field_evidence=tuple(replace(e, basis='SPEAKER') if e == proof else e
                                                 for e in observation.field_evidence))
    other = observe(artifact, task(WORK[1]))
    with pytest.raises(ValueError):
        replace(other, field_evidence=tuple(proof if e.field == 'description' else e for e in other.field_evidence))


def test_baseline_v1_payload_and_external_ref_are_exactly_preserved():
    fixture = json.loads((Path(__file__).parents[1] / 'fixtures' / 'p2_work_identity_v1.json').read_text(encoding='utf8'))
    data = fixture['source']
    artifact = RawArtifact(**dict(data, ingestion_time=datetime.fromisoformat(data['ingestion_time'])))
    for saved in fixture['observations']:
        o = TaskObservation.from_dict(saved['observation'])
        assert o.verify(artifact) and o.to_dict() == saved['observation']
        assert ObservationRef.from_observation(o).to_dict() == saved['ref']
        raw = next(e for e in o.field_evidence if e.field == 'raw_text')
        expected = [o.provenance.workspace_id, o.provenance.artifact_version_id,
                    o.provenance.evidence_ref, raw.char_start, raw.char_end]
        assert o.observation_id == 'task_observation_v1_' + hashlib.sha256(json.dumps(expected, ensure_ascii=False).encode()).hexdigest()
        with pytest.raises(ValueError, match='identity'):
            replace(o, observation_id='task_observation_v1_' + 'f' * 64)
        assert not o.verify(replace(artifact, content=artifact.content + '更新'))
    old_bound = TaskObservation.from_dict(fixture['observations'][1]['observation'])
    new = observe(artifact, task(WORK[0]))
    assert new.observation_id != old_bound.observation_id
    assert ObservationRef.from_observation(new) != ObservationRef.from_dict(fixture['observations'][1]['ref'])


def test_v2_does_not_inherit_or_rewrite_persisted_v1_p4_relations(tmp_path):
    from linkloom.semantic_ingestion.task_relation_query import RelationQuery
    from tests.unit.test_task_relation_query import seed
    path = tmp_path / 'p4.sqlite'
    case, observations, artifacts, _, rows, args = seed(path)
    assessment_id = rows[0]['same_task']['assessment_id']
    before = path.read_bytes()
    with RelationQuery(path, **args) as query:
        assert query.get(case['workspace_id'], assessment_id).current_supported
    new = []
    for old in observations.values():
        artifact = artifacts[old.provenance.artifact_id]
        segment = next(s for s in parse_source_text(artifact).segments if s.evidence_span == old.provenance)
        proposal = TaskProposal(old.raw_text, tuple(e.quote for e in old.field_evidence if e.field == 'description'),
            owner=old.owner, deadline_text=old.deadline_text, project=old.project, vendor=old.vendor)
        current = build_task_observation(proposal, segment, artifact, identity_version='v2')
        assert current.verify(artifact) and current.observation_id != old.observation_id
        new.append(current)
    with RelationQuery(path, **dict(args, observations=tuple(new))) as query:
        assert not query.get(case['workspace_id'], assessment_id).current_supported
    assert path.read_bytes() == before


@pytest.mark.parametrize('change', ['update', 'revoke', 'foreign', 'during_call'])
def test_work_profile_source_checks_remain_fail_closed(change):
    artifact = source()
    service, provider, registry, current = setup(artifact, [task(w) for w in WORK])
    service.task_profile = TaskDiscoveryProfile.TASK_RECALL_V3
    incoming = artifact
    if change == 'update':
        current[0] = replace(artifact, content=SOURCE + '更新。')
    elif change == 'revoke':
        registry.unregister_evidence(artifact.workspace_id, parse_source_text(artifact).segments[0].evidence_span.evidence_ref)
    elif change == 'foreign':
        incoming = replace(artifact, workspace_id='foreign')
    else:
        provider.hook = lambda: current.__setitem__(0, replace(artifact, content=SOURCE + '更新。'))
    result = service.discover((incoming,), workspace_id=artifact.workspace_id)
    assert not released(result) and not result.coverage_complete
    assert len(provider.requests) == (1 if change == 'during_call' else 0)
