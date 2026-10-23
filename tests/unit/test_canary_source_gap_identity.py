"""Historical declarations must agree with both expected and available bytes."""
import hashlib
import json
from zipfile import ZipFile

from scripts import analyze_sprint_trust_v2 as analyzer


def test_rewritten_runner_declaration_cannot_qualify_a_missing_early_version(tmp_path):
    archive = tmp_path / 'identity.zip'
    raw = b'final runner bytes'
    with ZipFile(archive, 'w') as z:
        z.writestr(analyzer.FINAL_RUNNER_MEMBER, raw)
        root, expected = analyzer.EARLY_RUNNER_GAPS[0]
        recorded = hashlib.sha256(raw).hexdigest()
        assert recorded != expected
        z.writestr(f'work/{root}/authorization.json', json.dumps({'script_sha256': recorded}))
    with ZipFile(archive) as z:
        gaps = analyzer._source_gaps(z, set(z.namelist()))
    assert gaps[0]['status'] == 'NOT_VERIFIED'


def test_adapter_bytes_do_not_override_a_conflicting_recorded_declaration(tmp_path, monkeypatch):
    archive = tmp_path / 'identity.zip'
    raw = b'initial adapter bytes'
    monkeypatch.setattr(analyzer, 'EARLY_ADAPTER_SHA256', hashlib.sha256(raw).hexdigest())
    with ZipFile(archive, 'w') as z:
        z.writestr(analyzer.FINAL_ADAPTER_MEMBER, raw)
        z.writestr('work/ce-d-20261023/selection.json', json.dumps({'adapter_sha256': 'different-declaration'}))
    with ZipFile(archive) as z:
        gaps = analyzer._source_gaps(z, set(z.namelist()))
    assert gaps[-1]['status'] == 'NOT_VERIFIED'
