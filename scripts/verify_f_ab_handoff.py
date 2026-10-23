"""Verify the local F A/B handoff using only the standard library.

No SDK imports, credential reads, Provider calls or budget writes.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DOC = Path('docs/requirements/f-cross-turn-discovery')
PACKET = DOC / 'ab-preparation-v1'
MANIFEST = DOC / 'HANDOFF_MANIFEST.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(root, name):
    return json.loads((root / name).read_bytes())


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(root):
    manifest = read(root, MANIFEST)
    for name, expected in manifest['files'].items():
        path = (root / name).resolve()
        require(path.is_relative_to(root.resolve()), 'manifest path escaped root')
        require(digest(path.read_bytes()) == expected, 'handoff hash mismatch: ' + name)
    frozen = read(root, PACKET / 'MANIFEST.json')
    for name, expected in frozen['files'].items():
        require(digest((root / PACKET / name).read_bytes()) == expected,
                'original frozen hash mismatch: ' + name)
    require(digest((root / 'scripts/prepare_f_ab.py').read_bytes()) == frozen['generator_sha256'],
            'request generator changed')
    freeze = read(root, PACKET / 'code-freeze.json')
    production = root / 'src/linkloom/semantic_ingestion/extraction.py'
    require(digest(production.read_bytes()) == freeze['candidate_sha256'], '38-line candidate changed')
    require(digest((root / PACKET / 'candidate.diff').read_bytes()) == freeze['candidate_diff_sha256'],
            'candidate patch changed')
    tree = ast.parse(production.read_text(encoding='utf-8'))
    extractor = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                     and n.name == 'ProviderNeutralSemanticExtractor')
    init = next(n for n in extractor.body if isinstance(n, ast.FunctionDef) and n.name == '__init__')
    defaults = {a.arg: d for a, d in zip(init.args.kwonlyargs, init.args.kw_defaults)}
    require(ast.literal_eval(defaults['prompt_version']) == 'semantic-extraction/v1', 'default is not OFF')
    plan, sources = read(root, PACKET / 'PLAN.json'), read(root, PACKET / 'sources.json')
    require(len(plan['pairs']) == 10, 'expected 10 paired inputs')
    require(len(list((root / PACKET / 'requests').rglob('*.json'))) == 20, 'expected 20 requests')
    for pair in plan['pairs']:
        off = read(root, PACKET / pair['arms']['OFF']['path'])
        on = read(root, PACKET / pair['arms']['ON']['path'])
        for arm, request in [('OFF', off), ('ON', on)]:
            canonical = json.dumps(request, sort_keys=True, ensure_ascii=False,
                allow_nan=False, separators=(',', ':')).encode('utf-8')
            require(digest(canonical) == pair['arms'][arm]['request_sha256'], 'request fingerprint changed')
        require({k for k in off if off[k] != on[k]} == {'run_id', 'task_id', 'turn_id', 'user_input'},
                'unexpected request difference')
        left = json.loads(off['user_input'].split('Input:\n', 1)[1])
        right = json.loads(on['user_input'].split('Input:\n', 1)[1])
        if pair['profile'] == 'null':
            require(right['prompt_version'] == freeze['opt_in_prompt_version'], 'missing ON version')
            right['prompt_version'] = left['prompt_version']
        require(left == right, 'ON changed source or schema input')
        require(left['source_segment']['text'] == sources[pair['source_index']]['content'],
                'input text differs from original transcript')
        require(not {'gold', 'qrels', 'expected', 'case_id', 'query_text'} & set(left),
                'evaluator data in model input')
    for name in manifest['files']:
        require(not any(token in name.lower() for token in
            ('case_design', 'failure_matrix', 'qrels', 'evaluator_only', 'evidence.zip')),
            'excluded evaluator/archive material was included')
    junit = ET.parse(root / PACKET / 'preparation-tests.xml').getroot()
    require(len(list(junit.iter('testcase'))) == 39, 'expected 39 recorded tests')
    require(not any(list(junit.iter(tag)) for tag in ('failure', 'error', 'skipped')), 'test receipt not green')
    require(plan['new_provider_calls'] == 0 and not plan['real_execution_enabled'], 'not an offline plan')
    print(json.dumps(dict(status='PASS', files_verified=len(manifest['files']),
        request_pairs=10, frozen_requests=20, recorded_tests=39, default='OFF',
        provider_calls=0, budget_writes=0), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    verify(args.root.resolve())
