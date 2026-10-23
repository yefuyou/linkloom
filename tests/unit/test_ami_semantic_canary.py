"""Canary input/cost boundaries; synthetic assertions are not semantic scores."""
import hashlib
import json

import pytest

from scripts import run_ami_semantic_canary as runner


def inputs(tmp_path):
    root = tmp_path / 'inputs'
    (root / 'transcripts').mkdir(parents=True)
    manifest = {'files': {}}
    for meeting, count in zip(runner.MEETINGS, (8, 20)):
        chunks, cursor = [], 0
        for index in range(count):
            text = f'[{meeting} speaker A {index}] Please review component {index}.\n'
            chunks.append(dict(text=text, start=cursor, end=cursor+len(text),
                               sha256=hashlib.sha256(text.encode()).hexdigest()))
            cursor += len(text)
        canonical = ''.join(c['text'] for c in chunks)
        transcript = dict(chunks=chunks, canonical_text=canonical, utterances=[],
                          canonical_sha256=hashlib.sha256(canonical.encode()).hexdigest())
        path = root / 'transcripts' / f'{meeting}.json'
        path.write_text(json.dumps(transcript), encoding='utf8')
        manifest['files'][f'transcripts/{meeting}.json'] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / 'manifest.json').write_text(json.dumps(manifest), encoding='utf8')
    return root


def test_preflight_never_constructs_client_or_reads_official_labels(tmp_path, monkeypatch):
    root = inputs(tmp_path)
    secret = 'OFFICIAL_GOLD_MUST_NOT_REACH_A_MODEL_98765'
    for filename in ('queries.jsonl', 'qrels.jsonl', 'summary.xml'):
        (root / filename).write_text(secret, encoding='utf8')

    def forbidden(*args, **kwargs):
        raise AssertionError('no SDK construction in free preflight')

    monkeypatch.setattr(runner, 'build_gemini_sdk_client', forbidden)
    output = tmp_path / 'preflight'
    runner.run(root, output)
    requests = json.loads((output / 'planned_requests.json').read_text(encoding='utf8'))
    assert len(requests) == 28
    assert secret not in json.dumps(requests)
    assert not (output / 'actual_requests.jsonl').exists()
    assert json.loads((output / 'plan.json').read_text())['estimated_cost_upper_bound_usd'] <= 3
    with pytest.raises(FileExistsError):
        runner.run(root, output)


def test_source_tampering_is_rejected_before_any_request_or_client(tmp_path):
    root = inputs(tmp_path)
    with (root / 'transcripts' / 'ES2002a.json').open('a', encoding='utf8') as stream:
        stream.write(' ')
    with pytest.raises(ValueError, match='manifest mismatch'):
        runner.run(root, tmp_path / 'tampered')
