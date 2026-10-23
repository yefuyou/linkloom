"""Offline byte verification only. Does not extract, import SDK or call Provider."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'EVIDENCE_MANIFEST.json').read_text(encoding='utf8'))
sha = lambda raw: hashlib.sha256(raw).hexdigest()
archive = root / manifest['archive']['path']
assert sha(archive.read_bytes()) == manifest['archive']['sha256'], 'archive hash mismatch'
with ZipFile(archive) as z:
    assert set(z.namelist()) == set(manifest['payload_files']), 'archive member population mismatch'
    for name, expected in manifest['payload_files'].items():
        assert sha(z.read(name)) == expected, name
    live_seal = json.loads(z.read('live/output_seal.json'))
    for name, expected in live_seal['files'].items():
        assert sha(z.read('live/' + name)) == expected, 'live seal: ' + name
    historical_seal = json.loads(z.read('historical-ami/output_seal.json'))
    for name, expected in historical_seal['files'].items():
        assert sha(z.read('historical-ami/' + name)) == expected, 'historical seal: ' + name
    plan = json.loads(z.read('live/plan.json'))
    for name, expected in plan['code_hashes'].items():
        assert sha(z.read('frozen-source/' + name)) == expected, 'frozen source: ' + name
    requests = [n for n in z.namelist() if n.startswith('live/attempts/') and n.endswith('.request.json')]
    terminals = [json.loads(z.read(n.replace('.request.', '.terminal.'))) for n in requests]
    assert len(requests) == 28 and all(t['outcome'] == 'SENT_SUCCEEDED' for t in terminals)
    for request_name in requests:
        request = json.loads(z.read(request_name))
        terminal = json.loads(z.read(request_name.replace('.request.', '.terminal.')))
        assert all(request[k] == terminal[k] for k in ('run_id', 'sequence', 'request_sha256'))
for name, expected in manifest['local_files'].items():
    assert sha((root / name).read_bytes()) == expected, name
print(f'PASS: {len(manifest["payload_files"])} ZIP members, 28 attributable terminals, '
      f'{len(manifest["local_files"])} local files; zero Provider calls')
