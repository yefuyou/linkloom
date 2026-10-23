"""Validate frozen offline evidence before constructing a paid client."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from linkloom.evaluation.canary_trust import save_new, sha

REQUIRED = ('quote', 'runner', 'safety')


def junit_pass(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    if not suites or sum(int(s.get('tests', 0)) for s in suites) == 0:
        raise ValueError('empty test evidence')
    if any(int(s.get(k, 0)) for s in suites for k in ('failures', 'errors', 'skipped')):
        raise ValueError('test evidence contains failure/error/skip')


def validate_gate(path, identity):
    gate = json.loads(path.read_text(encoding='utf8'))
    if gate['frozen_sha'] != identity['frozen_sha'] or gate['code_hashes'] != identity['code_hashes']:
        raise ValueError('gate source identity drift')
    if gate['runtime'] != identity['runtime']:
        raise ValueError('gate runtime identity drift')
    if any(gate[key] != identity[key] for key in ('source_hashes', 'input_files')):
        raise ValueError('gate input source identity drift')
    for key in REQUIRED:
        check = gate['tests'][key]
        path = Path(check['path'])
        if sha(path.read_bytes()) != check['sha256']:
            raise ValueError('test evidence changed')
        junit_pass(path)
        tested = json.loads(path.with_suffix('.identity.json').read_text(encoding='utf8'))
        from scripts.run_canary_checks import SUITES
        if (tested['frozen_sha'] != identity['frozen_sha'] or tested['code_hashes'] != identity['code_hashes']
                or tested['report_sha256'] != check['sha256'] or tested['suite'] != key
                or tested['tests'] != SUITES[key] or tested['runtime'] != identity['runtime']):
            raise ValueError('test report does not belong to frozen source/suite')
    replay_path = Path(gate['replay']['path'])
    if sha(replay_path.read_bytes()) != gate['replay']['sha256']:
        raise ValueError('replay receipt changed')
    replay = json.loads(replay_path.read_text(encoding='utf8'))
    if (replay['frozen_sha'] != identity['frozen_sha'] or replay['code_hashes'] != identity['code_hashes']
            or replay['provider_calls'] != 0 or replay['replayed_outputs'] != 28
            or replay['runtime'] != identity['runtime']
            or replay['task_profile'] is not None or replay['status'] != 'PASS'):
        raise ValueError('offline Discovery regression not verified')
    if (replay['complete_meetings'] != {'ES2002a': 8, 'ES2005c': 20}
            or any(replay[key] != identity[key] for key in ('source_hashes', 'input_files'))
            or sha(Path(replay['historical_seal_path']).read_bytes()) != replay['historical_seal_sha256']):
        raise ValueError('offline Discovery source/seal mismatch')
    if sha((replay_path.parent / 'discovery.json').read_bytes()) != replay['discovery_sha256']:
        raise ValueError('offline Discovery output changed')
    batches = json.loads((replay_path.parent / 'discovery.json').read_text(encoding='utf8'))
    counts = {m: sum(len(b['result']['extractions']) for b in batches if b['meeting_id'] == m)
              for m in ('ES2002a', 'ES2005c')}
    if counts != replay['complete_meetings']:
        raise ValueError('incomplete offline Discovery structure')


def create(output, tests, replay):
    from scripts.run_ami_canary_v2 import code_identity, head, runtime_identity
    identity = dict(frozen_sha=head(), code_hashes=code_identity(), runtime=runtime_identity())
    replay_data = json.loads(replay.read_text(encoding='utf8'))
    identity.update({key: replay_data[key] for key in ('source_hashes', 'input_files')})
    evidence = lambda p: dict(path=str(p.resolve()), sha256=sha(p.read_bytes()))
    gate = dict(**identity, tests={k: evidence(v) for k, v in zip(REQUIRED, tests)}, replay=evidence(replay))
    save_new(output, gate)
    validate_gate(output, identity)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in REQUIRED:
        parser.add_argument('--' + key, type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    create(args.output, [args.quote, args.runner, args.safety], args.replay)
