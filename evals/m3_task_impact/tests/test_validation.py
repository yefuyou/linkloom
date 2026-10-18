import json
from pathlib import Path

from evals.m3_task_impact.fixtures import ROOT, load_inputs
from evals.m3_task_impact.runner import execute,scripted_d01
from evals.m3_task_impact.validation import validate_dataset


def test_all_annotations_have_exact_evidence_and_separate_splits():
    audit = validate_dataset()
    assert audit['splits']['dev']['cases']==12
    assert audit['splits']['holdout']['cases']==4
    assert audit['holdout_executed'] is False
    assert audit['model_calls']==0


def test_runner_cannot_load_gold_even_during_scripted_execution(monkeypatch):
    original = Path.read_text
    reads = []
    def guarded(path,*args,**kwargs):
        reads.append(path)
        assert 'gold' not in path.parts
        assert 'holdout' not in path.parts
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_text',guarded)
    record = execute(load_inputs()[0],scripted_d01,adapter_kind='SCRIPTED')
    assert record['status']=='EXECUTED'
    assert reads and all('gold' not in p.parts for p in reads)


def test_static_test_never_scores_or_runs_holdout(monkeypatch):
    import evals.m3_task_impact.runner as runner
    import evals.m3_task_impact.scorer as scorer
    def forbidden(*args,**kwargs):
        raise AssertionError('static validation must not execute or score cases')
    monkeypatch.setattr(runner,'execute',forbidden)
    monkeypatch.setattr(scorer,'score_case',forbidden)
    assert validate_dataset()['validation']=='STATIC_ONLY'
