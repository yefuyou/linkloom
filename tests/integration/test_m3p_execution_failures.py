"""Actual Product HTTP failure semantics with deterministic, non-network adapters."""
import pytest

from linkloom.agents.model_adapter import (FakeModelAdapter, ModelAction, ModelProviderError, ModelResponse)
from linkloom.tools.contracts import ToolCall
from tests.m3p_support import product_case, product_http, http_ask


class UnknownOutcome:
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return ModelResponse(error=ModelProviderError(
            'MODEL_TIMEOUT', 'timeout', 'Synthetic unknown provider outcome.',
            outcome='unknown_provider_outcome'))


@pytest.mark.parametrize('failure', ['provider_unknown', 'budget', 'no_progress', 'workspace', 'permission'])
def test_task_http_execution_failure_never_becomes_accepted_partial(tmp_path, failure):
    app, service, _, _, workspace = product_case(tmp_path)
    models = []

    def factory(_query):
        if failure == 'provider_unknown':
            model = UnknownOutcome()
        else:
            def action(request):
                if failure == 'permission':
                    return ModelAction.tool(ToolCall('forbidden', 'read_verified_note', {'note_ref': 'unobserved'}))
                return ModelAction.tool(ToolCall('inspect', 'inspect_task_context', {
                    'workspace_id': 'outside-workspace' if failure == 'workspace' else workspace}))
            model = FakeModelAdapter([action] * 3)
        models.append(model)
        return model

    service.model_factory = factory
    service.relation_max_steps = 1 if failure == 'budget' else 2
    try:
        with product_http(app, service) as base:
            result = http_ask(base, workspace)
        assert result['status'] in {'BLOCKED', 'FAILED', 'PROVIDER_UNCERTAIN'}, result
        assert result['conclusions'] == []
        assert not result.get('task_review')
        assert not result['reviewer'].get('accepted')
        assert models and len(models[0].requests) <= service.relation_max_steps
        if failure == 'provider_unknown':
            assert len(models[0].requests) == 1
    finally:
        app.close()
