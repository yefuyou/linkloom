from types import SimpleNamespace
import sys
import pytest

from linkloom.ui import __main__ as cli


def test_task_configuration_does_not_send_imports_or_model_requests(tmp_path, monkeypatch):
    captured = {}
    def unexpected(**kwargs):
        raise AssertionError('configuration must never call a model')
    sdk = SimpleNamespace(models=SimpleNamespace(generate_content=unexpected), close=lambda: None)
    def build(key, **kwargs):
        captured['options'] = kwargs
        return sdk
    def server(backend, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(server_port=1234, serve_forever=lambda: None, server_close=lambda: None)
    monkeypatch.setattr(cli, 'build_gemini_sdk_client', build)
    monkeypatch.setattr(cli, 'create_http_server', server)
    monkeypatch.setenv('GEMINI_API_KEY', 'offline-test-placeholder')
    monkeypatch.setattr(sys, 'argv', ['linkloom.ui', '--database', str(tmp_path / 'product.sqlite'),
        '--allow-external-provider', '--allow-task-investigation',
        '--task-max-requests', '16', '--task-max-input-tokens', '500000',
        '--task-max-output-tokens', '100000', '--task-max-cost-usd', '2',
        '--task-max-seconds', '120', '--task-request-timeout-seconds', '30'])
    assert cli.main() == 0
    assert captured['product_app'].extractor is None
    assert captured['investigation_service'].config()['task_investigation']
    assert captured['options']['http_options_kwargs']['retry_options'] == {'attempts': 1}
    assert captured['options']['http_options_kwargs']['timeout'] == 30000


@pytest.mark.parametrize('args', [[], ['--allow-external-provider']])
def test_task_provider_cannot_start_without_explicit_permissions_and_budgets(monkeypatch, args):
    monkeypatch.setattr(sys, 'argv', ['linkloom.ui', '--allow-task-investigation', *args])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2


def test_pinned_sdk_constructs_supported_timeout_and_retry_options_offline():
    from google.genai import types
    options = types.HttpOptions(timeout=30000, retry_options=types.HttpRetryOptions(attempts=1))
    assert options.timeout == 30000 and options.retry_options.attempts == 1


def test_real_sdk_sends_structured_task_request_to_mock_transport_only():
    import httpx
    import json
    from linkloom.agents.providers import build_gemini_sdk_client, GeminiProviderAdapter
    from linkloom.ui.__main__ import GeminiSDKClientBridge
    from linkloom.agents.model_adapter import ModelTurnRequest, ModelGenerationOptions
    from linkloom.tools.contracts import ToolDefinition
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        assert 'gemini-3.8-flash:generateContent' in str(request.url)
        declaration = captured[-1]['tools'][0]['functionDeclarations'][0]
        assert 'parametersJsonSchema' in declaration
        return httpx.Response(200, json=dict(candidates=[dict(content=dict(role='model', parts=[
            dict(functionCall=dict(name='propose_task_relations', args={'proposals': []}, id='synthetic-call'))]),
            finishReason='STOP')], usageMetadata=dict(promptTokenCount=10, candidatesTokenCount=5, totalTokenCount=15)))
    sdk = build_gemini_sdk_client('offline-placeholder', http_options_kwargs=dict(timeout=60000,
        retry_options=dict(attempts=1), client_args=dict(transport=httpx.MockTransport(handle))))
    try:
        provider = GeminiProviderAdapter(GeminiSDKClientBridge(sdk))
        tool = ToolDefinition('propose_task_relations', '1', 'Unconfirmed proposals.',
            dict(type='object', properties=dict(proposals=dict(type='array', items=dict(type='string'))),
                 required=['proposals'], additionalProperties=False), dict(type='object'))
        request = ModelTurnRequest(run_id='sdk-test', turn_id='sdk-turn', task_id='task', agent_id='agent',
            sequence=1, user_input='Public synthetic test only.', observation=None,
            available_tools=[tool], model_id='gemini-3.8-flash',
            generation_options=ModelGenerationOptions(max_output_tokens=256, thinking_level='low', require_tool_call=True))
        response = provider.complete(request)
        assert not response.error and response.proposal.tool_calls[0].runtime_call.tool_id == tool.tool_id
        assert len(captured) == 1
    finally:
        sdk.close()
