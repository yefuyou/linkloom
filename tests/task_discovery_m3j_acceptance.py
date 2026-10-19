"""Reproduce M3-J veto before/after offline; never calls a network provider."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.unit.test_task_discovery_m3j import REFUSAL_CASES
from tests.unit.test_task_observation_discovery import claim, setup, source
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService


def evaluate(service_type):
    rows = []
    for index, (text, work, expected) in enumerate(REFUSAL_CASES):
        artifact = source(text)
        excerpt = work if text.count(work) == 1 else next(
            clause for clause in re.split(r"[。；;]", text) if work in clause)
        current, provider, registry, _ = setup(artifact, [claim(excerpt, work)])
        service = service_type(current.extractor, source_registry=registry,
            current_source=current.current_source)
        result = service.discover((artifact,), workspace_id=artifact.workspace_id)
        found = result.valid_observations + result.partial_observations
        rows.append(dict(case=index, text=text, work=work, expected_accept=expected,
            accepted=bool(found), exact_evidence=all(o.verify(artifact) for o in found),
            groups=len(result.groups), calls=len(provider.requests),
            failures=[f.reason for f in result.failures]))
    true_positive = sum(r['expected_accept'] and r['accepted'] for r in rows)
    accepted = sum(r['accepted'] for r in rows)
    expected = sum(r['expected_accept'] for r in rows)
    return dict(cases=rows, expected=expected, accepted=accepted, true_positive=true_positive,
        false_accepts=sum(r['accepted'] and not r['expected_accept'] for r in rows),
        false_rejects=expected-true_positive, recall=true_positive/expected,
        precision=true_positive/accepted if accepted else None,
        evidence_valid=all(r['exact_evidence'] for r in rows))


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--replay-output', type=Path)
    args = parser.parse_args()
    baseline = subprocess.run(['git', 'show', '4b58dda:src/linkloom/semantic_ingestion/task_observation_discovery.py'],
        cwd=ROOT, capture_output=True, check=True).stdout.decode('utf-8')
    spec = importlib.util.spec_from_loader('m3j_frozen_baseline', loader=None)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    exec(compile(baseline, '<frozen-4b58dda-discovery>', 'exec'), module.__dict__)
    report = dict(execution='OFFLINE_SCRIPTED_PROVIDER', paid_calls=0,
        scope='F veto contracts only; NOT semantic model quality or held-out evaluation',
        baseline_ref='4b58ddac03f347941339a05cfaa7f73a0028cbb9',
        before=evaluate(module.TaskObservationDiscoveryService),
        after=evaluate(TaskObservationDiscoveryService))
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    if args.replay_output:
        from tests import task_discovery_acceptance as diagnostic
        snapshots = {}
        for name in ('sources', 'labels'):
            blob = subprocess.run(['git', 'show', f'4b58dda:tests/fixtures/task_discovery/{name}.json'],
                cwd=ROOT, capture_output=True, check=True).stdout
            snapshot = args.output.with_suffix(f'.{name}.json')
            with snapshot.open('xb') as stream:
                stream.write(blob)
            snapshots[name] = snapshot
        diagnostic.SOURCES, diagnostic.LABELS = snapshots['sources'], snapshots['labels']
        original = ROOT / 'docs/requirements/m3-task-discovery/evidence/live'
        diagnostic.run(args.replay_output, original)
        diagnostic.score(args.replay_output / 'predictions.json', args.output.with_suffix('.replay-score.json'))
        recorded = diagnostic.read(original / 'predictions.json')
        replayed = diagnostic.read(args.replay_output / 'predictions.json')
        keys = ('valid_observations', 'partial_observations', 'groups', 'sources', 'failures',
                'unprocessed_sources', 'coverage_complete', 'coverage_policy', 'semantic_exhaustive')
        comparison = {a['case_id']: all(a['result'][key] == b['result'][key] for key in keys)
            for a, b in zip(recorded['cases'], replayed['cases'], strict=True)}
        comparison['paid_calls'] = 0
        comparison['continuation_candidate_coverage'] = replayed['cases'][0]['result']['candidate_coverage']
        diagnostic.save(args.output.with_suffix('.replay-comparison.json'), comparison)
    print(json.dumps({k: {field: report[k][field] for field in
        ('expected', 'accepted', 'true_positive', 'false_accepts', 'false_rejects', 'recall', 'precision')}
        for k in ('before', 'after')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
