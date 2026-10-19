"""Offline tests exercise the production factory with a mocked SDK boundary."""
import pytest

from linkloom.ui.canary import CanaryHost


def test_default_live_is_closed_before_sdk_or_state_creation(tmp_path):
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        CanaryHost.open_live(enabled=False, grant=None)
    assert not list(tmp_path.iterdir())


def test_http_like_provider_and_budget_fields_are_not_authority(tmp_path):
    with pytest.raises(ValueError, match="AUTHORIZATION"):
        CanaryHost.open_live(enabled=True, grant={"provider": "fake", "max_requests": 999})


def test_live_stage_objects_cannot_be_injected_or_relabelled():
    from linkloom.ui.task_investigation import TaskInvestigationModels
    from linkloom.agents.providers import GeminiProviderAdapter
    from types import SimpleNamespace
    with pytest.raises(ValueError, match='UNTRUSTED_CANARY_ADAPTER'):
        TaskInvestigationModels(object(), object(), object(), execution_kind='LIVE')
    adapter = GeminiProviderAdapter(SimpleNamespace(generate_content=lambda **k: None))
    with pytest.raises(ValueError, match='LIVE_ADAPTER_CANNOT_BE_FAKE'):
        TaskInvestigationModels(adapter, adapter, adapter)


def test_offline_network_denial_is_independent_of_execution_label():
    import socket
    with pytest.raises(RuntimeError, match='OFFLINE_GATE'):
        socket.create_connection(('example.com', 443))


def test_frozen_config_and_shared_ledger_limits_agree():
    from linkloom.ui.canary_spec import load_spec
    from linkloom.agents.providers.canary_ledger import STAGE_PER_CASE_CAPS, CASE_TOTAL_CAPS
    spec = load_spec()
    names = dict(discovery='F', planning='G', semantic='Semantic', runtime='Runtime')
    for stage, alias in names.items():
        assert STAGE_PER_CASE_CAPS[stage] == dict(spec.config.stage_requests[alias])
    assert CASE_TOTAL_CAPS == {name: case.max_requests for name, case in spec.cases.items()}
