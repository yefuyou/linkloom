"""Measure synthetic source preparation and unchanged-repeat work; no Provider."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import statistics
import sys
import time
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'tests/integration'), str(ROOT)]


def measure(count, root):
    from scripts.reliability_probe import seed
    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.scanner import scan_vault
    from linkloom.loader import VaultReader
    from linkloom.retrieval_v2.runtime_backend import RuntimeRetrievalBackend
    from linkloom.agents.model_adapter import FakeModelAdapter
    from linkloom.runtime.team_decision_planner import plan_relation_investigation
    from run_bounded_multihop import SourceOnlyController
    documents = ['Lina Frost is the accountable owner of Cedar Veil.',
                 'Lina Frost reports directly to Aster Brook.']
    documents.extend(f'Unrelated astronomy log {i}. Telescope calibration and moonstone samples.'
                     for i in range(count-2))
    root.mkdir(parents=True)
    app, workspace = seed(root, documents)
    query = 'Who is the direct leader of the owner of Cedar Veil?'
    stages = {'snapshot_ms': [], 'scan_vault_ms': [], 'read_notes_ms': [], 'retrieval_ms': []}
    counts = dict.fromkeys(stages, 0)
    originals = {'snapshot_ms': app.snapshot_investigation_sources,
                 'scan_vault_ms': scan_vault, 'read_notes_ms': VaultReader.read_notes,
                 'retrieval_ms': RuntimeRetrievalBackend.search}

    def timed(key):
        def run(*args, **kwargs):
            start = time.perf_counter()
            try:
                return originals[key](*args, **kwargs)
            finally:
                stages[key].append((time.perf_counter()-start)*1000)
                counts[key] += 1
        return run
    models = []
    def factory(_):
        model = FakeModelAdapter([SourceOnlyController()]*12)
        models.append(model)
        return model
    service = ProductInvestigationService(app, model_factory=factory, external_provider=False)
    runs = []
    # Optional local observation; no tracer overhead in latency measurements.
    try:
        import psutil
        process = psutil.Process()
        base = process.memory_info().rss
    except ImportError:
        process, base = None, None
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.object(app, 'snapshot_investigation_sources', timed('snapshot_ms')))
            stack.enter_context(patch('linkloom.ui.investigation.scan_vault', timed('scan_vault_ms')))
            stack.enter_context(patch.object(VaultReader, 'read_notes', timed('read_notes_ms')))
            stack.enter_context(patch.object(RuntimeRetrievalBackend, 'search', timed('retrieval_ms')))
            for repeat in range(3):
                before = {k: len(v) for k, v in stages.items()}
                started = time.perf_counter()
                snapshot = service.start(query, workspace, task_plan=plan_relation_investigation(query))
                prep = (time.perf_counter()-started)*1000
                rid = snapshot['run']['id']
                while True:
                    snapshot = service.inspect(rid)
                    if snapshot['kind'] != 'running':
                        break
                    time.sleep(.01)
                runs.append({'repeat': repeat, 'preparation_ms': prep,
                    'elapsed_ms': (time.perf_counter()-started)*1000,
                    'kind': snapshot['kind'], 'metrics': snapshot['metrics'],
                    'answer': snapshot.get('source_investigation', {}).get('answer'),
                    'evidence_verified': bool(snapshot['evidence']) and all(
                        e.get('content_sha256') and e.get('quote_sha256') and e.get('source_version')
                        for e in snapshot['evidence']),
                    'stages': {k: {'count': len(v)-before[k], 'total_ms': sum(v[before[k]:])} for k, v in stages.items()}})
        current = process.memory_info().rss if process else None
        return {'sources': count, 'runs': runs,
            'rss_current_delta_bytes': current-base if current is not None else None,
            'memory_kind': 'OS process RSS delta across three runs; not peak allocation',
            'median_elapsed_ms': statistics.median(r['elapsed_ms'] for r in runs)}
    finally:
        app.close()


if __name__ == '__main__':
    import os
    os.environ['LINKLOOM_RETRIEVAL_MODE'] = 'bm25'
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'work/real-agent-reliability'/('scale-'+uuid4().hex[:8]))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = []
    for count in (10, 100, 500):
        row = measure(count, args.output/str(count))
        rows.append(row)
        print(json.dumps(row), flush=True)
    (args.output/'summary.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
