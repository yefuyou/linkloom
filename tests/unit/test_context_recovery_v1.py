from dataclasses import replace
from datetime import UTC, datetime
import hashlib

import pytest

from linkloom.context.models import ContextResource, ResourceType
from linkloom.context.recovery import EvidenceRef, EvidenceRecovery, SourceSnapshot


def snapshot(content='支持甲。\n反对甲。\n支持甲。', *, workspace='w', current=True, authorized=True, version=1):
    resource = ContextResource(workspace, 'r', 'd', '/note.md', '/', version,
        datetime(2026, 10, 19, tzinfo=UTC), hashlib.sha256(content.encode()).hexdigest(),
        'fixture:note', ResourceType.DOCUMENT, datetime(2026, 10, 19, tzinfo=UTC), 1)
    return SourceSnapshot(resource, content, authorized=authorized, current=current)


def test_exact_same_file_passages_roundtrip_and_restore():
    source = snapshot()
    refs = tuple(EvidenceRef.from_snapshot(source, start, end) for start, end in ((0, 4), (5, 9), (10, 14)))
    assert refs[0].evidence_id != refs[2].evidence_id
    refs = tuple(EvidenceRef.from_dict(r.to_dict()) for r in refs)
    result = EvidenceRecovery(lambda w, r: source).restore(workspace_id='w', refs=refs)
    assert result.status == 'complete'
    assert [e.quote for e in result.evidence] == ['支持甲。', '反对甲。', '支持甲。']


@pytest.mark.parametrize('kind,reason', [
    ('foreign_ref', 'WORKSPACE_MISMATCH'), ('foreign_source', 'WORKSPACE_MISMATCH'),
    ('missing', 'SOURCE_NOT_FOUND'), ('forbidden', 'SOURCE_FORBIDDEN'),
    ('inactive', 'SOURCE_INACTIVE'), ('updated', 'SOURCE_VERSION_MISMATCH'),
    ('historical', 'SOURCE_NOT_CURRENT'), ('tampered_content', 'SOURCE_HASH_MISMATCH'),
    ('wrong_span', 'QUOTE_HASH_MISMATCH'), ('wrong_identity', 'EVIDENCE_IDENTITY_MISMATCH'),
    ('wrong_path', 'SOURCE_IDENTITY_MISMATCH')])
def test_recovery_fails_closed_with_explicit_incomplete(kind, reason):
    source = snapshot()
    ref = EvidenceRef.from_snapshot(source, 0, 4)
    loaded = source
    if kind == 'foreign_ref': ref = replace(ref, workspace_id='other')
    if kind == 'foreign_source': loaded = snapshot(workspace='other')
    if kind == 'missing': loaded = None
    if kind == 'forbidden': loaded = replace(source, authorized=False)
    if kind == 'inactive': loaded = replace(source, resource=replace(source.resource, active=False))
    if kind == 'updated': loaded = snapshot(version=2)
    if kind == 'historical': loaded = replace(source, current=False)
    if kind == 'tampered_content': loaded = replace(source, content='更改甲。\n反对甲。\n支持甲。')
    if kind == 'wrong_span': ref = replace(ref, char_start=5, char_end=9, line_start=2, line_end=2)
    if kind == 'wrong_identity': ref = replace(ref, evidence_id='invented')
    if kind == 'wrong_path': loaded = replace(source, resource=replace(source.resource, logical_path='/else.md'))
    result = EvidenceRecovery(lambda w, r: loaded).restore(workspace_id='w', refs=(ref,))
    assert result.status == 'incomplete'
    assert not result.evidence
    assert result.failures[0].reason == reason


def test_historical_mode_keeps_label_and_missing_key_ref_is_incomplete():
    source = snapshot(current=False)
    ref = EvidenceRef.from_snapshot(source, 0, 4)
    recovery = EvidenceRecovery(lambda w, r: source)
    result = recovery.restore(workspace_id='w', refs=(ref,), historical=True)
    assert result.evidence[0].historical
    assert recovery.restore(workspace_id='w', refs=(), required_ids=(ref.evidence_id,)).status == 'incomplete'


def test_identical_quotes_on_same_line_and_revalidation_after_permission_change():
    source = snapshot('甲乙甲乙')
    refs = (EvidenceRef.from_snapshot(source, 0, 2), EvidenceRef.from_snapshot(source, 2, 4))
    assert refs[0].evidence_id != refs[1].evidence_id
    loaded = [source]
    recovery = EvidenceRecovery(lambda w, r: loaded[0])
    assert recovery.restore(workspace_id='w', refs=refs).status == 'complete'
    loaded[0] = replace(source, authorized=False)
    result = recovery.restore(workspace_id='w', refs=refs)
    assert result.status == 'incomplete'
    assert not result.evidence


def test_recovery_source_quote_and_total_output_byte_limits():
    source = snapshot()
    refs = (EvidenceRef.from_snapshot(source, 0, 4), EvidenceRef.from_snapshot(source, 5, 9))
    for options, reason in (({'max_source_bytes': 10}, 'SOURCE_TOO_LARGE'),
                            ({'max_quote_bytes': 6}, 'QUOTE_TOO_LARGE')):
        result = EvidenceRecovery(lambda w, r: source, **options).restore(workspace_id='w', refs=refs)
        assert result.status == 'incomplete' and not result.evidence
        assert result.failures[0].reason == reason
    result = EvidenceRecovery(lambda w, r: source, max_total_quote_bytes=12).restore(workspace_id='w', refs=refs)
    assert len(result.evidence) == 1
    assert result.failures[0].reason == 'RECOVERY_BUDGET'


def test_conflicting_duplicate_releases_quote_budget_for_other_valid_evidence():
    source = snapshot()
    first = EvidenceRef.from_snapshot(source, 0, 4)
    second = EvidenceRef.from_snapshot(source, 5, 9)
    conflict = replace(first, char_start=5, char_end=9, line_start=2, line_end=2)
    result = EvidenceRecovery(lambda w, r: source, max_total_quote_bytes=12).restore(
        workspace_id='w', refs=(first, conflict, second))
    assert result.status == 'incomplete'
    assert [e.ref for e in result.evidence] == [second]
    assert [f.reason for f in result.failures] == ['EVIDENCE_REF_CONFLICT']
