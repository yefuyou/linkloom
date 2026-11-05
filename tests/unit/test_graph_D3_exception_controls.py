"""Synthetic context/quote sealing tests; no real D3, provider or outputs."""
import json
from pathlib import Path
import pytest
import run_graph_D3_exception as driver


def dump(p,value):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value),encoding='utf8')


def inputs(tmp_path,monkeypatch,wrong_generation=False):
    monkeypatch.setattr(driver,'ROOT',tmp_path);monkeypatch.setattr(driver,'H',tmp_path/'D3');monkeypatch.setattr(driver.frozen,'ROOT',tmp_path);monkeypatch.setattr(driver.frozen,'assert_off_peak',lambda cfg:None)
    old=dict(model_id='same-model',locator={'min_score':2},query_time_fixed='2024-04-01',price_checked_on='2024-04-01',pricing_rechecked_at_utc='old',pricing_valid_until_utc='expired')
    new=dict(old,query_time_fixed='2024-04-03',pricing_rechecked_at_utc='fresh',pricing_valid_until_utc='future')
    if wrong_generation:new['model_id']='changed-model'
    dump(tmp_path/'config.json',old);dump(tmp_path/'D3/runtime-config.json',new);dump(tmp_path/'D3/input-context.json',dict(query_time_fixed='2024-04-03'))
    marker=dict(hashes={str(tmp_path/'config.json'):driver.sha(tmp_path/'config.json')},asset_stats={},databases=[])
    dump(tmp_path/'runtime-freeze.json',marker)
    rules=dict(final_configuration_sha256=driver.sha(tmp_path/'D3/runtime-config.json'),source_freeze_sha256=driver.sha(tmp_path/'runtime-freeze.json'),hashes={str(tmp_path/'D3/runtime-config.json'):driver.sha(tmp_path/'D3/runtime-config.json'),str(tmp_path/'D3/input-context.json'):driver.sha(tmp_path/'D3/input-context.json')})
    dump(tmp_path/'D3/rules-freeze.json',rules)
    return driver.Inputs()


def test_only_fixture_clock_and_billing_metadata_can_differ(tmp_path,monkeypatch):
    value=inputs(tmp_path,monkeypatch);assert value.verify()
    assert driver.read(tmp_path/'config.json')['query_time_fixed']=='2024-04-01'


def test_even_consistently_resealed_generation_change_is_rejected(tmp_path,monkeypatch):
    value=inputs(tmp_path,monkeypatch,wrong_generation=True)
    with pytest.raises(ValueError,match='MODEL_OR_LOCATOR_PARAMETERS_CHANGED'):value.verify()


def test_original_frozen_configuration_cannot_be_rewritten(tmp_path,monkeypatch):
    value=inputs(tmp_path,monkeypatch);dump(tmp_path/'config.json',{'changed':True})
    with pytest.raises(ValueError,match='FROZEN_CODE_OR_INPUT_CHANGED'):value.verify()


def test_d3_context_is_bound_to_its_frozen_clock(tmp_path,monkeypatch):
    value=inputs(tmp_path,monkeypatch)
    # Even before construction, a context/config mismatch cannot be accepted.
    dump(tmp_path/'D3/input-context.json',dict(query_time_fixed='2024-04-04'))
    rules=driver.read(tmp_path/'D3/rules-freeze.json');rules['hashes'][str(tmp_path/'D3/input-context.json')]=driver.sha(tmp_path/'D3/input-context.json');dump(tmp_path/'D3/rules-freeze.json',rules)
    with pytest.raises(ValueError,match='D3_CONTEXT_CHANGED'):driver.Inputs().verify()
