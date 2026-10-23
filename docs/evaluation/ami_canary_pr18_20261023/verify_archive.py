"""Verify blocked-canary evidence without extraction, SDK or model calls."""
from pathlib import Path
from zipfile import ZipFile
import hashlib,json,io
root=Path(__file__).resolve().parent
manifest=json.loads((root/'EVIDENCE_MANIFEST.json').read_text(encoding='utf8'))
archive=root/'evidence.zip'
assert hashlib.sha256(archive.read_bytes()).hexdigest()==manifest['archive_sha256']
with ZipFile(archive) as z:
    assert len(z.namelist())==len(set(z.namelist()))
    assert set(z.namelist())==set(manifest['files'])
    for name,record in manifest['files'].items():
        data=z.read(name)
        assert len(data)==record['bytes'] and hashlib.sha256(data).hexdigest()==record['sha256'],name
    historical=json.loads(z.read('historical-pr16/live/output_seal.json'))
    for name,expected in historical['files'].items():
        assert hashlib.sha256(z.read('historical-pr16/live/'+name)).hexdigest()==expected
    with ZipFile(io.BytesIO(z.read('source/runner-693ab81.zip'))) as source:
        identity=json.loads(source.read('SOURCE_IDENTITY.json'))
        for name,expected in identity['code_hashes'].items():
            assert hashlib.sha256(source.read(name)).hexdigest()==expected
    baseline=[json.loads(s) for s in z.read('historical-pr16/live/actual_requests.jsonl').decode('utf8').splitlines()]
    planned=json.loads(z.read('current/tonight-plan-01/planned_requests.json'))
    assert baseline==planned and len(planned)==28
    replay=json.loads(z.read('current/tonight-replay-01/summary.json'))
    assert replay['discovery_partial']==4 and replay['f_task_proposals']==5
    blocker=json.loads(z.read('current/tonight-blocker-reproduction.json'))
    assert blocker['real_provider_calls']==0 and blocker['non_asserted'] is False
    assert len(blocker['actual_discovery']['partial_observations'])==1
status=json.loads((root/'STATUS.json').read_text(encoding='utf8'))
assert status['status']=='BLOCKED_OFFLINE_GATE' and status['live']['provider_calls']==0
print(json.dumps(dict(status='PASS',files=len(manifest['files']),planned_requests=28,
    provider_calls=0,canary_status=status['status'],historical_reply_replay='PASS'),indent=2))
