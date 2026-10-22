"""Known external adapters cannot gain task-stage authority from an offline label."""
from types import SimpleNamespace
import pytest

from linkloom.agents.providers import GeminiProviderAdapter, DeepSeekProviderAdapter
from linkloom.ui.task_investigation import TaskInvestigationModels


@pytest.mark.parametrize('adapter_class', [GeminiProviderAdapter, DeepSeekProviderAdapter])
@pytest.mark.parametrize('execution_kind', ['FAKE', 'REPLAY'])
def test_external_stage_cannot_be_relabelled_as_offline(adapter_class, execution_kind):
    # No client, key, SDK construction or model request is involved.
    external = object.__new__(adapter_class)
    offline = SimpleNamespace(complete=lambda request: None)
    with pytest.raises(ValueError, match='LIVE_ADAPTER_CANNOT_BE_FAKE_OR_REPLAY'):
        TaskInvestigationModels(external, offline, offline, execution_kind=execution_kind)
