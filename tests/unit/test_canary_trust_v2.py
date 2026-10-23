import json

import pytest

from linkloom.evaluation.canary_trust import AttemptJournal, classify_attempt, summarize_attempts


@pytest.mark.parametrize('evidence,expected', [
    ({'sdk_exception': 'SSLError', 'session_outcome': 'FAILED'}, 'OUTCOME_UNKNOWN'),
    ({'sdk_exception': 'ValueError', 'session_outcome': 'FAILED'}, 'OUTCOME_UNKNOWN'),
    ({'pre_transport_rejection': 'request_mapping_failed', 'sdk_entered': False}, 'NOT_SENT'),
    ({'pre_transport_rejection': 'sdk_argument_validation', 'sdk_entered': False}, 'NOT_SENT'),
    ({'http_status': 400, 'provider_response_received': True}, 'SENT_FAILED'),
    ({'http_status': 200, 'http_response_observed': True, 'provider_response_received': True}, 'SENT_SUCCEEDED'),
    ({'http_status': 200, 'provider_response_received': True}, 'OUTCOME_UNKNOWN'),
    ({'provider_response_received': True, 'provider_response_id': True}, 'OUTCOME_UNKNOWN'),
    ({'provider_response_received': True, 'provider_response_id': 'receipt', 'provider_error_received': True}, 'SENT_FAILED'),
    ({'provider_response_received': True, 'provider_response_id': 'receipt'}, 'SENT_SUCCEEDED'),
    ({'session_outcome': 'SUCCEEDED', 'usage': None}, 'OUTCOME_UNKNOWN'),
    ({'pre_transport_rejection': 'sdk_argument_validation', 'sdk_entered': True}, 'OUTCOME_UNKNOWN'),
    ({}, 'OUTCOME_UNKNOWN'),
])
def test_transport_status_requires_positive_evidence(evidence, expected):
    assert classify_attempt(evidence) == expected


def test_unknown_reservation_survives_crash_and_no_usage_is_not_free(tmp_path):
    tmp_path = tmp_path / 'attempts'
    journal = AttemptJournal(tmp_path, run_id='run', identity={'source_sha': 'frozen'}, max_requests=2, max_usd=0.5)
    request = {'messages': ['source']}
    journal.reserve(request, source_hashes={'source': 'hash'}, reserved_usd=.25)
    summary = summarize_attempts(tmp_path)
    assert summary['outcomes'] == {'OUTCOME_UNKNOWN': 1}
    assert summary['unresolved_provider_outcomes'] == 1
    assert summary['cost_reserved_usd'] == .25
    assert summary['actual_billed_usd'] is None
    with pytest.raises(FileExistsError):
        AttemptJournal(tmp_path, run_id='run', identity={}, max_requests=2, max_usd=.5)


def test_budget_and_immutable_terminal_receipts(tmp_path):
    tmp_path = tmp_path / 'attempts'
    journal = AttemptJournal(tmp_path, run_id='run', identity={}, max_requests=2, max_usd=.25)
    ticket = journal.reserve({'request': 1}, source_hashes={'source': 'hash'}, reserved_usd=.25)
    journal.finish(ticket, {'provider_response_id': 'receipt', 'provider_response_received': True})
    with pytest.raises(FileExistsError):
        journal.finish(ticket, {})
    with pytest.raises(ValueError, match='budget'):
        journal.reserve({'request': 2}, source_hashes={}, reserved_usd=.25)
    record = json.loads((tmp_path / '0001.request.json').read_text())
    assert record['source_hashes'] == {'source': 'hash'}
    assert record['request_sha256']
    assert record['run_id'] == 'run'


def test_tampered_terminal_cannot_claim_success(tmp_path):
    tmp_path = tmp_path / 'attempts'
    journal = AttemptJournal(tmp_path, run_id='run', identity={}, max_requests=2, max_usd=.5)
    ticket = journal.reserve({'request': 1}, source_hashes={}, reserved_usd=.25)
    journal.finish(ticket, {'provider_response_id': 'receipt', 'provider_response_received': True})
    path = tmp_path / '0001.terminal.json'
    row = json.loads(path.read_text())
    row['request_sha256'] = 'wrong'
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match='identity'):
        summarize_attempts(tmp_path)
