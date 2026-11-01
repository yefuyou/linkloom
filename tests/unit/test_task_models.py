from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json

import pytest

from linkloom.tasks.models import EvidenceReference, TaskCandidateInput


MEETING = datetime(2026, 10, 8, 2, tzinfo=UTC)


def make_evidence(source='meeting-a', version='version-a', quote='联调报告由李明负责。', *, workspace='ws-a', line=1):
    return EvidenceReference(workspace, source, version, quote,
                             hashlib.sha256(quote.encode('utf8')).hexdigest(),
                             line_start=line, line_end=line, meeting_time=MEETING)


def make_candidate(identifier='observation-a', *, workspace='ws-a', **values):
    data = dict(candidate_id=identifier, workspace_id=workspace, title='联调报告', owner='李明',
                deadline='2026-10-12', deadline_text='10/12 前完成', meeting_time=MEETING,
                evidence=(make_evidence(workspace=workspace),))
    data.update(values)
    return TaskCandidateInput(**data)


def test_extractor_neutral_candidate_preserves_ordered_multi_source_evidence_and_fingerprint():
    first = make_evidence()
    second = replace(make_evidence('meeting-b', 'version-b', '延期到 10/16。', line=8),
                     message_id='speech-b', char_start=10, char_end=10 + len('延期到 10/16。'))
    candidate = make_candidate(evidence=(first, second), candidate_version='future-v2/multi-span')
    payload = json.loads(json.dumps(candidate.to_dict(), ensure_ascii=False))
    assert isinstance(payload['evidence'], list)
    restored = TaskCandidateInput.from_dict(payload)
    assert restored == candidate and restored.fingerprint == candidate.fingerprint
    assert [e.source_version_id for e in restored.evidence] == ['version-a', 'version-b']
    assert restored.evidence[1].char_start == 10 and restored.evidence[1].message_id == 'speech-b'
    assert replace(candidate, evidence=(second, first)).fingerprint != candidate.fingerprint
    assert replace(candidate, owner='王芳').fingerprint != candidate.fingerprint
    assert replace(candidate, candidate_version='future-v3').fingerprint != candidate.fingerprint


@pytest.mark.parametrize('changes', [
    {'quote_sha256': '0' * 64},
    {'quote_sha256': 'not-a-hash'},
    {'line_start': None},
    {'line_start': 0},
    {'line_end': True},
    {'line_end': -1},
    {'char_start': 0, 'char_end': 1},
    {'char_start': 1},
    {'source_sha256': 'broken'},
])
def test_evidence_rejects_tampered_hashes_and_incomplete_or_invalid_positions(changes):
    with pytest.raises(ValueError):
        replace(make_evidence(), **changes)


def test_message_only_locator_is_supported_but_unlocated_or_cross_workspace_evidence_is_rejected():
    evidence = replace(make_evidence(), line_start=None, line_end=None, message_id='speech-42')
    assert EvidenceReference.from_dict(evidence.to_dict()) == evidence
    with pytest.raises(ValueError, match='message identifier or source line'):
        replace(evidence, message_id=None)
    with pytest.raises(ValueError, match='another workspace'):
        make_candidate(evidence=(replace(evidence, workspace_id='foreign-workspace'),))


def test_missing_year_and_meeting_time_stay_unknown_in_the_input_boundary():
    candidate = make_candidate(deadline=None, deadline_text='10/12 前完成', meeting_time=None)
    restored = TaskCandidateInput.from_dict(candidate.to_dict())
    assert restored.deadline is None and restored.deadline_text == '10/12 前完成'
    assert restored.meeting_time is None
    with pytest.raises(ValueError):
        replace(candidate, deadline='10/12')
    with pytest.raises(ValueError, match='timezone-aware'):
        replace(candidate, meeting_time=datetime(2026, 10, 8))
    with pytest.raises(ValueError, match='evidence'):
        replace(candidate, evidence=())
