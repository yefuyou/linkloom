"""Offline execution/metering contracts; never a semantic business score."""
from types import SimpleNamespace
import json

import pytest

from scripts.run_task_semantic_smoke import MeteredClient,manifest,execute


def test_manifest_stable_and_not_a_gold_payload():
    first=manifest()
    assert first==manifest()
    assert first['selected']==['revision','causality','conflict']
    assert first['max_generation_attempts']==3
    assert 'gold' not in json.dumps(first).lower()


def test_sdk_meter_counts_thinking_and_preserves_failed_attempt(tmp_path):
    class SDK:
        def generate_content(self,**kwargs):
            if kwargs.get('fail'):
                raise TimeoutError()
            return SimpleNamespace(model_version='actual-version',response_id='test',
                usage_metadata=SimpleNamespace(prompt_token_count=100,candidates_token_count=20,
                    thoughts_token_count=30,total_token_count=150,cached_content_token_count=None))
    meter=MeteredClient(SDK(),tmp_path)
    meter.generate_content(model='gemini-3.8-flash')
    assert meter.calls[0]['cost_estimate_usd']==pytest.approx(.0002625)
    with pytest.raises(TimeoutError):
        meter.generate_content(model='gemini-3.8-flash',fail=True)
    assert meter.calls[1]['cost_estimate_usd'] is None
    assert (tmp_path/'attempt-2.json').is_file() and (tmp_path/'receipt-2.json').is_file()


def test_changed_frozen_request_cannot_connect(tmp_path,monkeypatch):
    frozen=manifest()
    frozen['model_id']='another-model'
    def never():
        pytest.fail('must not connect when freeze is changed')
    monkeypatch.setattr('scripts.run_task_semantic_smoke.connect',never)
    with pytest.raises(RuntimeError,match='FROZEN_MANIFEST_MISMATCH'):
        execute(frozen,tmp_path/'not-created')
    assert not (tmp_path/'not-created').exists()


@pytest.mark.parametrize('dependency',[
    'scripts/check_task_semantic_provider.py','src/linkloom/agents/providers/gemini_api.py',
    'src/linkloom/agents/model_adapter.py','src/linkloom/semantic_ingestion/extraction.py'])
def test_changed_execution_dependency_cannot_connect(tmp_path,monkeypatch,dependency):
    frozen=manifest()
    from scripts import run_task_semantic_smoke as driver
    original=driver.file_hash
    monkeypatch.setattr(driver,'file_hash',lambda p:'0'*64 if p==driver.ROOT/dependency else original(p))
    monkeypatch.setattr(driver,'connect',lambda:pytest.fail('changed dependency reached connect'))
    with pytest.raises(RuntimeError,match='FROZEN_MANIFEST_MISMATCH'):
        execute(frozen,tmp_path/'not-created')
