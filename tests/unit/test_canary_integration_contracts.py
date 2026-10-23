"""Independent-review counterexamples for the joint Canary integration."""
import hashlib
import json
from zipfile import ZipFile

import pytest

from scripts.analyze_sprint_trust_v2 import analyze_archive
from scripts.ami_canary_sources import load_sources
from scripts.analyze_canary_v2 import replay
from scripts import run_ami_canary_v2 as runner
from test_ami_canary_v2 import inputs
from test_sprint_trust_v2 import _write_zip


def archive_with(tmp_path, updates):
    original = tmp_path / 'original.zip'
    _write_zip(original)
    with ZipFile(original) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    members.update({name: json.dumps(value).encode() for name, value in updates.items()})
    result = tmp_path / 'changed.zip'
    with ZipFile(result, 'w') as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    return result


def test_wire_provider_rejection_is_sent_failed(tmp_path):
    archive = archive_with(tmp_path, {'work/ce-b-20261023/wire/001-response.json':
        {'response': {'error': {'code': 400, 'message': 'Provider rejected request'}}}})
    result = analyze_archive(archive)
    attempt = next(row for row in result['attempts'] if row['phase'] == 'B')
    assert attempt['outcome'] == 'SENT_FAILED'


def test_prewire_label_cannot_override_same_attempt_wire_error(tmp_path):
    archive = archive_with(tmp_path, {'work/ce-d-20261023/wire/002-error.json':
        {'exception_type': 'ConnectError', 'status_code': None}})
    result = analyze_archive(archive)
    attempt = next(row for row in result['attempts'] if row['phase'] == 'D' and row['sequence'] == 2)
    assert attempt['outcome'] == 'OUTCOME_UNKNOWN'


def test_mismatched_reservation_counts_are_rejected(tmp_path):
    archive = archive_with(tmp_path, {'work/ce-b-20261023/closed.json':
        {'requests': 2, 'reserved_usd': .5}})
    with pytest.raises(ValueError, match='reservation count'):
        analyze_archive(archive)


@pytest.mark.parametrize('offsets', [(-8, 1), (False, 1), (0, True), (1, 2), (0, 9)])
def test_chunk_offsets_are_integer_bounded_and_contiguous(tmp_path, offsets):
    root = inputs(tmp_path)
    path = root / 'transcripts/ES2002a.json'
    data = json.loads(path.read_text())
    data['canonical_text'] = 'a' * 8
    data['canonical_sha256'] = hashlib.sha256(b'a' * 8).hexdigest()
    data['chunks'] = [dict(text='a', start=i, end=i+1, sha256=hashlib.sha256(b'a').hexdigest())
                      for i in range(8)]
    data['chunks'][0].update(start=offsets[0], end=offsets[1])
    path.write_text(json.dumps(data), encoding='utf8')
    manifest_path = root / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['files']['transcripts/ES2002a.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    with pytest.raises(ValueError, match='chunk'):
        load_sources(root)


@pytest.mark.parametrize('request_count,response_count', [(29, 29), (28, 29), (29, 28)])
def test_replay_cannot_ignore_extra_historical_rows(tmp_path, request_count, response_count):
    root = inputs(tmp_path)
    history = tmp_path / 'history'
    history.mkdir()
    (history / 'plan.json').write_text('{"task_profile": null}')
    (history / 'output_seal.json').write_text('{"files": {}}')
    for filename, count in [('actual_requests.jsonl', request_count), ('model_responses.jsonl', response_count)]:
        (history / filename).write_text('{}\n' * count)
    with pytest.raises(ValueError, match='population'):
        replay(root, history, tmp_path / 'replay')


def test_launch_identity_includes_loaded_pytest_helper():
    helper = runner.ROOT / 'scripts/pytest_windows_tempdir.py'
    hashes = runner.code_identity()
    assert hashes.get('scripts/pytest_windows_tempdir.py') == hashlib.sha256(helper.read_bytes()).hexdigest()
