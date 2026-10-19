import pytest
from tests.m3l_support import offline_product, finish


@pytest.mark.parametrize('case', ['C1', 'C2', 'C3'])
def test_closed_host_uses_normal_ask_all_stages_and_preserves_authority(tmp_path, monkeypatch, case):
    p = offline_product(tmp_path, monkeypatch)
    p.app.repository.select_workspace(p.host._workspaces[case])
    result, code = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces[case],
        provider='arbitrary', model_id='expensive', max_requests=9999))
    assert code == 202, result
    result = finish(p.ask, p.host._workspaces[case], result)
    assert result['status'] == 'PARTIAL', result
    assert result['reviewer']['accepted'], result
    assert not result['conclusions']
    assert 'UNSATISFIED_PREMISE:revision' in result['preparation']['gaps']
    assert result['task_investigation']['semantic_analysis']['publishable'] is False
    assert {c['model'] for c in p.sdk.calls} == {p.spec.model_id}
    assert p.construction[0]['http_options_kwargs']['retry_options'] == {'attempts': 1}
    assert len(p.sdk.calls) == (9 if case == 'C3' else 8)
    state = p.host.ledger.snapshot()
    assert result['metrics']['provider_requests'] == state['request_count'] == len(p.sdk.calls)
    assert result['metrics']['accounting_scope'] == 'CANARY_CAMPAIGN_ALL_STAGES'
    assert result['metrics']['thinking_tokens'] == 2 * len(p.sdk.calls)
    p.app.close()


def test_sdk_wire_retry_is_stopped_before_a_second_send(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch)
    wire = p.construction[0]['wire_payload_observer']
    original = p.sdk.generate_content
    def retry(**payload):
        wire({})
        wire({})
        return original(**payload)
    p.sdk.generate_content = retry
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C1']))
    assert result['status'] == 'BLOCKED'
    assert not p.sdk.calls
    assert p.host.ledger.snapshot()['status'] == 'STOPPED'
    p.app.close()


def test_source_revoked_during_response_stops_before_next_stage(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch)
    workspace = p.host._workspaces['C1']
    original = p.sdk.generate_content
    def revoke(**payload):
        raw = original(**payload)
        source = p.app.snapshot_investigation_sources(workspace)[1][0]
        p.app.repository.delete_source(workspace, source['source_id'])
        return raw
    p.sdk.generate_content = revoke
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=workspace))
    assert result['status'] == 'BLOCKED' and not result['evidence']
    assert len(p.sdk.calls) == 1
    assert p.host.ledger.snapshot()['estimated_cost_usd'] > 0
    assert p.host.ledger.snapshot()['status'] == 'STOPPED'
    p.app.close()


def test_canary_fast_path_does_not_spend_any_provider_requests(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch)
    result, _ = p.ask.ask(dict(query='青竹项目现在使用哪个供应商？', workspace_id=p.host._workspaces['C1']))
    assert result['path'] != 'TASK_INVESTIGATION'
    assert not p.sdk.calls
    p.app.close()


@pytest.mark.parametrize('fault', ['timeout', 'usage', 'usage_shape'])
def test_unknown_provider_stops_entire_campaign_before_next_stage(tmp_path, monkeypatch, fault):
    p = offline_product(tmp_path, monkeypatch, fault=fault)
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C1']))
    assert result['status'] == 'BLOCKED', result
    assert len(p.sdk.calls) == 1
    assert p.host.ledger.snapshot()['estimated_cost_usd'] is None
    p.app.repository.select_workspace(p.host._workspaces['C2'])
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C2']))
    assert result['status'] == 'BLOCKED'
    assert len(p.sdk.calls) == 1
    p.app.close()


def test_foreign_workspace_or_changed_question_cannot_use_grant(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch)
    workspace = p.app.repository.create_workspace('not a Canary')['workspace_id']
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=workspace))
    assert result['status'] == 'BLOCKED'
    assert not p.sdk.calls
    p.app.close()


def test_usage_must_fit_actual_stage_output_cap_including_thinking(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch, fault='stage_output')
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C1']))
    assert result['status'] == 'BLOCKED'
    assert len(p.sdk.calls) == 1
    assert p.host.ledger.snapshot()['stop_reason'] == 'STAGE_OUTPUT_USAGE_EXCEEDED'
    p.app.close()


@pytest.mark.parametrize('changed_snapshot', [1, 2])
def test_first_preparation_source_race_permanently_stops_campaign(tmp_path, monkeypatch, changed_snapshot):
    p = offline_product(tmp_path, monkeypatch)
    original = p.app.snapshot_investigation_sources
    calls = []
    def change_on_second_snapshot(workspace):
        row, sources = original(workspace)
        calls.append(workspace)
        if len(calls) == changed_snapshot:
            sources = [dict(s, source_version='changed-version') for s in sources]
        return row, sources
    monkeypatch.setattr(p.app, 'snapshot_investigation_sources', change_on_second_snapshot)
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C1']))
    assert result['status'] == 'BLOCKED' and not p.sdk.calls
    assert p.host.ledger.snapshot()['status'] == 'STOPPED'
    monkeypatch.setattr(p.app, 'snapshot_investigation_sources', original)
    p.app.repository.select_workspace(p.host._workspaces['C2'])
    result, _ = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C2']))
    assert result['status'] == 'BLOCKED' and not p.sdk.calls
    p.app.close()


def test_admitted_source_versions_survive_new_host_and_reject_rebinding(tmp_path, monkeypatch):
    from linkloom.ui.canary import CanaryHost
    p = offline_product(tmp_path, monkeypatch)
    workspace = p.host._workspaces['C1']
    sources = p.app.snapshot_investigation_sources(workspace)[1]
    p.host.bind(p.spec.question, workspace, sources, lambda: sources)
    restarted = CanaryHost.open_live(enabled=True, grant=p.grant)
    second_app = restarted.product_application()
    changed = [dict(s, source_version='new-version') for s in sources]
    with pytest.raises(Exception, match='SOURCE_SNAPSHOT_CHANGED'):
        restarted.bind(p.spec.question, workspace, changed, lambda: changed)
    assert restarted.ledger.snapshot()['status'] == 'STOPPED'
    assert not p.sdk.calls
    second_app.close()
    p.app.close()


def test_source_id_cannot_be_reassigned_another_frozen_sources_text(tmp_path, monkeypatch):
    p = offline_product(tmp_path, monkeypatch)
    workspace = p.host._workspaces['C1']
    sources = list(p.app.snapshot_investigation_sources(workspace)[1])
    first, second = sources
    swapped = [dict(first, content=second['content']), dict(second, content=first['content'])]
    with pytest.raises(ValueError, match='SOURCE_IDENTITY'):
        p.host.bind(p.spec.question, workspace, swapped, lambda: swapped)
    assert p.host.ledger.snapshot()['status'] == 'STOPPED'
    assert not p.sdk.calls
    p.app.close()


def test_offline_task_chain_cannot_inject_known_live_outer_adapter(tmp_path):
    from tests.m3i_support import product_case, QUESTION
    from linkloom.agents.providers import GeminiProviderAdapter
    from types import SimpleNamespace
    app, service, ask, stages, workspace = product_case(tmp_path)
    def forbidden(**kwargs):
        raise AssertionError('Provider transport must never be reached')
    service.model_factory = lambda query: GeminiProviderAdapter(SimpleNamespace(generate_content=forbidden))
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    assert result['status'] == 'BLOCKED'
    assert result['error']['code'] == 'LIVE_ADAPTER_CANNOT_BE_FAKE_OR_REPLAY'
    app.close()


@pytest.mark.parametrize('fault', ['schema', 'citation'])
def test_bad_schema_and_citation_remain_blocked_with_diagnostics(tmp_path, monkeypatch, fault):
    p = offline_product(tmp_path, monkeypatch, fault=fault)
    result, code = p.ask.ask(dict(query=p.spec.question, workspace_id=p.host._workspaces['C1']))
    result = finish(p.ask, p.host._workspaces['C1'], result)
    assert result['status'] == 'BLOCKED', result
    assert not result['conclusions']
    assert len(p.sdk.calls) == (3 if fault == 'schema' else 8)
    p.app.close()
