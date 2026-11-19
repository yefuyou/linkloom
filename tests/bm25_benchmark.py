"""Reproducible synthetic comparison, without models or user data.

Run from the repository root: python -m tests.bm25_benchmark
Build timings include tracemalloc; update/query timings do not. Peak memory
covers initial construction only. Tokenization counts exclude no query calls:
query20 includes its 20 query tokenizations. Full-build calls count the public
build API; the legacy implementation additionally rebuilds all scope engines
on each mutation (N + 22 engines for this fixture's initial corpus).
"""
import json
import sys
import time
import tracemalloc
from dataclasses import replace

from linkloom.indexing.bm25 import BM25Index
from linkloom.indexing.models import IndexDocument


def corpus(n):
    return [IndexDocument('w', str(i), f'e:{i}', f'/d{i % 20}/{i}.md',
        f'项目检索 alpha beta group{i % 37} ' * (1 + i % 4), f'fixture:{i}') for i in range(n)]


def measure(cls, n):
    documents = corpus(n)
    index = cls()
    count = 0
    full_builds = 0
    original_build = index.build
    def build(documents):
        nonlocal full_builds
        full_builds += 1
        return original_build(documents)
    index.build = build
    module = sys.modules[cls.__module__]
    tokenize = module.tokenize_for_index
    def counted(text):
        nonlocal count
        count += 1
        return tokenize(text)
    module.tokenize_for_index = counted
    tracemalloc.start()
    rows = {}
    def run(name, action):
        nonlocal count, full_builds
        count = 0
        full_builds = 0
        start = time.perf_counter()
        action()
        rows[name] = {'ms': round((time.perf_counter() - start) * 1000, 3), 'tokenizations': count, 'full_build_calls': full_builds}
    try:
        run('build', lambda: index.build(documents))
        rows['peak_build_mib'] = round(tracemalloc.get_traced_memory()[1] / 1024 ** 2, 3)
        tracemalloc.stop()
        run('first_query', lambda: index.search('项目 alpha group3', workspace_id='w'))
        run('query20', lambda: [index.search('项目 alpha group3', workspace_id='w') for _ in range(20)])
        run('add', lambda: index.upsert(replace(documents[0], resource_id='new', logical_path='/d0/new.md')))
        run('modify', lambda: index.upsert(replace(documents[0], content='项目 beta changed')))
        run('first_after_update', lambda: index.search('项目 alpha group3', workspace_id='w'))
        run('stable_after_update20', lambda: [index.search('项目 alpha group3', workspace_id='w') for _ in range(20)])
        run('move_same', lambda: index.move('w', '1', '/d1/renamed.md'))
        run('move_cross', lambda: index.move('w', '1', '/else/renamed.md'))
        run('delete', lambda: index.remove('w', '2'))
        run('noop', lambda: index.upsert(index.get_document('w', '3')))
        run('batch10', lambda: [index.upsert(replace(documents[i], content='alpha changed')) for i in range(10, 20)])
    finally:
        tracemalloc.stop()
        module.tokenize_for_index = tokenize
    return rows


if __name__ == '__main__':
    # Baseline is extracted from the branch's master parent, not maintained code.
    import subprocess
    source = subprocess.check_output(['git', 'show', '6413f430c:src/linkloom/indexing/bm25.py'], text=True)
    import types
    baseline = types.ModuleType('bm25_baseline')
    sys.modules[baseline.__name__] = baseline
    exec(source, baseline.__dict__)
    for n in (1000, 10000):
        print(json.dumps({'n': n, 'before': measure(baseline.BM25Index, n), 'after': measure(BM25Index, n)}), flush=True)
