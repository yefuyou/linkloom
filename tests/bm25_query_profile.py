"""Offline query phase profiling: python -m tests.bm25_query_profile.

cProfile times include profiler overhead; compare normal wall times using
bm25_benchmark. IDF, scoring, sorting, and materialization remain distinguishable.
"""
import cProfile
from dataclasses import replace
import pstats
import subprocess
import sys
import types

from tests.bm25_benchmark import corpus
from linkloom.indexing.bm25 import BM25Index


def load_revision(revision):
    module = types.ModuleType('bm25_' + revision)
    sys.modules[module.__name__] = module
    exec(subprocess.check_output(['git', 'show', revision + ':src/linkloom/indexing/bm25.py'], text=True), module.__dict__)
    return module.BM25Index


def profile(cls, n):
    index = cls()
    index.build(corpus(n))
    for phase, repeats in [('first', 1), ('stable', 20), ('after_update', 1), ('stable_after_update', 20)]:
        if phase == 'after_update':
            index.upsert(replace(index.get_document('w', '0'), content='alpha changed'))
        profiler = cProfile.Profile()
        profiler.enable()
        for _ in range(repeats):
            index.search('项目 alpha group3', workspace_id='w')
        profiler.disable()
        print(n, cls.__module__, phase)
        pstats.Stats(profiler).strip_dirs().sort_stats('cumulative').print_stats(16)


if __name__ == '__main__':
    for n in (1000, 10000):
        for cls in (load_revision('6413f430c'), load_revision('bc4a4e46b'), BM25Index):
            profile(cls, n)
