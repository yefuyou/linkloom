import json
from datetime import datetime, timezone
import httpx
import pytest
from scripts import recovery_live as live
from scripts.reliability_probe import MODEL, REQUEST_RESERVATION_USD


def test_all_nested_ledgers_and_unknown_cost_are_accounted(tmp_path, monkeypatch):
    monkeypatch.setattr(live, 'OUT', tmp_path)
    path = tmp_path/'live-one'/'case'/ 'provider_ledger.jsonl'
    path.parent.mkdir(parents=True)
    events = [
        {'event':'request_reserved','request_id':'a','utc':datetime.now(timezone.utc).isoformat()},
        {'event':'request_finished','request_id':'a','usage':{'estimated_cost_usd':.02}},
        {'event':'request_reserved','request_id':'b','utc':datetime.now(timezone.utc).isoformat()},
        {'event':'request_finished','request_id':'b','usage':None},
    ]
    path.write_text('\n'.join(json.dumps(e) for e in events),encoding='utf8')
    attempts, cost, _, strict, _ = live.accounting()
    assert attempts == 2
    assert cost == pytest.approx(.02+REQUEST_RESERVATION_USD)
    assert strict == 0  # unknown Usage alone is not strict outcome uncertainty
    path.write_text(json.dumps(events[0]),encoding='utf8')
    with pytest.raises(RuntimeError,match='UNCLOSED'):
        live.accounting()


def test_budget_enforces_new_caps_and_canary(monkeypatch, tmp_path):
    monkeypatch.setattr(live, 'OUT', tmp_path)
    payload = {'model':MODEL,'contents':'synthetic','config':{}}
    budget = live.Budget(live=False,canary=True)
    for _ in range(20):
        budget.admit(payload)
        budget.busy=False
    with pytest.raises(RuntimeError,match='CANARY'):
        budget.admit(payload)
    budget=live.Budget(live=False)
    budget.attempts=120
    with pytest.raises(RuntimeError,match='SEND_LIMIT'):
        budget.admit(payload)
    budget.attempts=0
    budget.reserved_usd=8-REQUEST_RESERVATION_USD/2
    with pytest.raises(RuntimeError,match='COST_LIMIT'):
        budget.admit(payload)


def test_strict_uncertainty_stops_after_two_and_blocks_original_case(tmp_path, monkeypatch):
    monkeypatch.setattr(live, 'OUT', tmp_path)
    for i in range(2):
        p=tmp_path/f'live-{i}'/'case'/'outcome.json'
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({'classification':'provider_outcome_uncertain','case_id':str(i)}),encoding='utf8')
    assert live.accounting()[3:] == (2,{'0','1'})
    with pytest.raises(RuntimeError,match='TWO_STRICT'):
        live.Budget(live=True).admit({'model':MODEL,'contents':'synthetic','config':{}})


def test_transport_type_and_strict_outcome_are_not_usage_flags():
    known=live.classify_exception(httpx.ConnectError('synthetic'))
    unknown=live.classify_exception(RuntimeError('synthetic'))
    assert known['classification']=='provider_known_failure'
    assert known['transport_classification']=='TRANSPORT_CONNECT'
    assert unknown['classification']=='provider_outcome_uncertain'
    assert live.classify_exception(httpx.ReadTimeout('synthetic'))['classification']=='provider_outcome_uncertain'


def test_historical_live_entry_cannot_start_another_budget():
    from scripts.reliability_probe import execute
    with pytest.raises(RuntimeError,match='HISTORICAL_LIVE_AUTHORIZATION_EXPIRED'):
        execute(live=True,attempt='forbidden')
