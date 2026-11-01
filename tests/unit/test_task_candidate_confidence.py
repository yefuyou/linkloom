"""Extractor-neutral confidence extensions preserve old serialized identities."""
from dataclasses import replace
import hashlib
import json

import pytest

from linkloom.tasks.models import ConfidenceMetadata, TaskCandidateInput, canonical_json
from linkloom.tasks.review_models import CandidateSnapshot, digest
from tests.unit.test_task_models import make_candidate


def test_missing_confidence_keeps_original_wire_hash_and_snapshot_history():
    original = make_candidate()
    wire = original.to_dict()
    assert 'confidence' not in wire and 'confidence_reason' not in wire
    assert TaskCandidateInput.from_dict(wire) == original
    assert original.fingerprint == hashlib.sha256(canonical_json(wire).encode('utf8')).hexdigest()
    snapshot = CandidateSnapshot(original, digest('original-context'), '{}')
    old = json.loads(canonical_json(snapshot.to_dict()))
    assert CandidateSnapshot.from_dict(old).to_dict() == old
    assert replace(original, confidence=None, confidence_reason=None).fingerprint == original.fingerprint


@pytest.mark.parametrize('label', ['HIGH', 'LOW'])
def test_confidence_and_reason_are_optional_paired_semantic_identity(label):
    old = make_candidate()
    candidate = replace(old, confidence=label, confidence_reason='明确的原文支持程度。')
    assert candidate.to_dict()['confidence'] == label
    assert TaskCandidateInput.from_dict(candidate.to_dict()) == candidate
    assert candidate.fingerprint != old.fingerprint
    assert replace(candidate, confidence_reason='需要额外核对。').fingerprint != candidate.fingerprint
    snapshot = CandidateSnapshot(candidate, digest('context'), '{}')
    assert CandidateSnapshot.from_dict(snapshot.to_dict()) == snapshot


@pytest.mark.parametrize('label,reason', [
    ('UNKNOWN', '理由'), ('low', '理由'), (False, '理由'), (None, '理由'),
    ('HIGH', None), ('LOW', ''), ('LOW', '  '), ('HIGH', 'bad\x00reason'),
])
def test_invalid_confidence_pairs_fail_closed(label, reason):
    with pytest.raises(ValueError):
        ConfidenceMetadata(label, reason)
    with pytest.raises(ValueError):
        replace(make_candidate(), confidence=label, confidence_reason=reason)


@pytest.mark.parametrize('field,value', [('confidence', 'LOW'), ('confidence_reason', '理由')])
def test_partial_or_unknown_wire_metadata_is_not_silently_ignored(field, value):
    wire = make_candidate().to_dict()
    wire[field] = value
    with pytest.raises(ValueError):
        TaskCandidateInput.from_dict(wire)
    wire['unsupported_confidence'] = 'HIGH'
    with pytest.raises(ValueError):
        TaskCandidateInput.from_dict(wire)


@pytest.mark.parametrize('raw', ['  before the next review  ', '\t2026年10月12日前\n'])
def test_source_deadline_text_preserves_whitespace_but_not_empty_values(raw):
    candidate = replace(make_candidate(), deadline=None, deadline_text=raw)
    assert candidate.deadline_text == raw
    assert TaskCandidateInput.from_dict(candidate.to_dict()).deadline_text == raw
    with pytest.raises(ValueError):
        replace(candidate, deadline_text=' \t ')
