import json
import pytest
from scripts.gemini_transport import unified


def test_prepare_is_not_authorization_and_no_sdk_constructed(tmp_path,monkeypatch):
    from google import genai
    monkeypatch.setattr(genai,'Client',lambda **kw:pytest.fail('no SDK construction'))
    packet = unified.prepare(tmp_path/'packet')
    assert packet['real_generation_requests']==0 and packet['state']=='PREPARED_NOT_AUTHORIZED'
    assert 'scripts/gemini_transport/product.py' in packet['source_sha256']
    assert 'tests/smoke/provider_ledger.py' in packet['source_sha256']
    assert 'src/linkloom/ui/unified_ask.py' in packet['source_sha256']
    monkeypatch.delenv('LINKLOOM_UNIFIED_TRANSPORT_AUTHORIZATION',raising=False)
    with pytest.raises(PermissionError):
        unified.execute(tmp_path/'packet')
    assert not (tmp_path/'packet'/'executed').exists()


def test_protocol_boundary_and_source_changes_block_execution(tmp_path,monkeypatch):
    folder = tmp_path/'packet'
    packet = unified.prepare(folder)
    monkeypatch.setenv('LINKLOOM_UNIFIED_TRANSPORT_AUTHORIZATION',packet['required_authorization'])
    assert unified.validate_authorization(folder)['experiment_id']==packet['experiment_id']
    packet['max_generation_requests']=7
    (folder/'protocol.json').write_text(json.dumps(packet),encoding='utf-8')
    with pytest.raises(ValueError,match='BOUNDARY'):
        unified.validate_authorization(folder)
    packet['max_generation_requests']=6
    packet['source_sha256']['scripts/gemini_transport/product.py']='bad'
    (folder/'protocol.json').write_text(json.dumps(packet),encoding='utf-8')
    with pytest.raises(ValueError,match='FINGERPRINT'):
        unified.validate_authorization(folder)


def test_existing_experiment_directory_cannot_be_reprepared(tmp_path):
    unified.prepare(tmp_path/'packet')
    with pytest.raises(FileExistsError):
        unified.prepare(tmp_path/'packet')


@pytest.mark.parametrize('field,value',[
    ('schema_version','other'),('stop_on',[]),('output_directory','elsewhere'),
    ('real_generation_requests',1),('success_dimensions',['gold']),
    ('input_output_parameters','changed'),('max_runtime_runs',True),('unexpected','value'),
    ('new_send_budget_seconds',901),('inflight_grace_seconds',131),('terminal_grace_seconds',6)])
def test_full_authorization_packet_is_locked(tmp_path,monkeypatch,field,value):
    folder = tmp_path/'packet'
    packet = unified.prepare(folder)
    monkeypatch.setenv('LINKLOOM_UNIFIED_TRANSPORT_AUTHORIZATION',packet['required_authorization'])
    packet[field] = value
    (folder/'protocol.json').write_text(json.dumps(packet),encoding='utf-8')
    with pytest.raises(ValueError,match='BOUNDARY'):
        unified.validate_authorization(folder)


def test_terminal_deadline_shares_send_gate_and_has_only_declared_grace(monkeypatch):
    packet = unified.protocol_for('UAT-'+'a'*32)
    assert packet['new_send_budget_seconds']==900
    # Send gate was set at t=100; HTTP startup/polling cannot restart the budget.
    send_deadline = 100+packet['new_send_budget_seconds']
    monkeypatch.setattr(unified,'monotonic',lambda:1000)
    assert unified.remaining_terminal_seconds(packet,send_deadline)==135
    monkeypatch.setattr(unified,'monotonic',lambda:1134)
    assert unified.remaining_terminal_seconds(packet,send_deadline)==1
    monkeypatch.setattr(unified,'monotonic',lambda:1135)
    with pytest.raises(TimeoutError):
        unified.remaining_terminal_seconds(packet,send_deadline)
