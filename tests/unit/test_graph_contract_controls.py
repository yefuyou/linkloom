"""Campaign-boundary tests with only synthetic local JSON, never actual D3."""
from pathlib import Path
import json
import pytest
import run_graph_contract_acceptance as campaign


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf8')


def locked(tmp_path,monkeypatch):
    monkeypatch.setattr(campaign,'ROOT',tmp_path);monkeypatch.setattr(campaign,'assert_off_peak',lambda *args:None)
    write(tmp_path/'config.json',{})
    write(tmp_path/'runtime-freeze.json',dict(hashes={},asset_stats={},databases=[]))
    assertion=tmp_path/'D3/assertions.json';write(assertion,{'assertions':['original']})
    write(tmp_path/'D3/rules-freeze.json',dict(final_configuration_sha256=campaign.sha(tmp_path/'config.json'),source_freeze_sha256=campaign.sha(tmp_path/'runtime-freeze.json'),hashes={str(assertion):campaign.sha(assertion)}))
    write(tmp_path/'D3/token-preflight.json',{'max_tokens':1})
    write(tmp_path/'D3/execution-freeze.json',dict(hashes={str(p):campaign.sha(p) for p in [tmp_path/'D3/rules-freeze.json',tmp_path/'D3/token-preflight.json']}))
    report=tmp_path/'DEV/report.json';budget=tmp_path/'budget-dev.json';write(report,dict(status='COMPLETE'));write(budget,dict(reserved_usd=.2))
    write(tmp_path/'DEV/results-freeze.json',dict(hashes={str(p):campaign.sha(p) for p in [report,budget]}))
    result=campaign.Inputs('D3');result.verify();return result


def test_d3_rules_cannot_be_rewritten_together_with_assertions(tmp_path,monkeypatch):
    inputs=locked(tmp_path,monkeypatch)
    path=tmp_path/'D3/assertions.json';write(path,{'assertions':['changed']})
    rules=campaign.read(inputs.rules);rules['hashes'][str(path)]=campaign.sha(path);write(inputs.rules,rules)
    with pytest.raises(ValueError,match='D3_RULES_SEAL_CHANGED'):inputs.verify()


@pytest.mark.parametrize('target',['D3/token-preflight.json','D3/execution-freeze.json','DEV/report.json','budget-dev.json','DEV/results-freeze.json'])
def test_sealed_execution_and_dev_budget_inputs_cannot_change(tmp_path,monkeypatch,target):
    inputs=locked(tmp_path,monkeypatch);write(tmp_path/target,{'changed':True})
    with pytest.raises(ValueError,match='D3_EXECUTION|DEV_RESULTS'):inputs.verify()


def test_configuration_and_source_seal_are_bound_to_d3(tmp_path,monkeypatch):
    inputs=locked(tmp_path,monkeypatch);write(tmp_path/'config.json',{'changed':True})
    with pytest.raises(ValueError,match='D3_FINAL_CONFIGURATION_CHANGED'):inputs.verify()


def test_local_budget_cap_and_combined_budget_are_distinct():
    # Each existing CampaignBudget remains <=1; the second run only gets the
    # remainder of the explicitly authorized combined allocation.
    config=dict(legacy_max_turns=12)
    dev=campaign.matrix_budget(40,0,config,0)
    assert dev['remaining']==1.55 and dev['allocation']==1 and dev['max_calls']==120
    assert dev['upper']==pytest.approx(.18432)
    final=campaign.matrix_budget(12,0,config,dev['upper'])
    assert final['allocation']<=1 and dev['upper']+final['upper']<=1.55
    with pytest.raises(ValueError,match='FULL_MATRIX_OVER_BUDGET'):campaign.matrix_budget(40,0,config,1.5)
    with pytest.raises(ValueError,match='COMBINED_BUDGET_EXHAUSTED'):campaign.matrix_budget(12,0,config,1.55)
    with pytest.raises(ValueError,match='FULL_MATRIX_OVER_BUDGET'):campaign.matrix_budget(40,40,config,0)
