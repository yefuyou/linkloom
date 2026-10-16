"""Production contracts exposed by the V3 cross-branch failure autopsy."""
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.agents.model_adapter import ModelTurnRequest
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.runtime.errors import ProviderOutcomeUnknownError
import pytest


def test_search_schema_describes_the_identity_the_executor_actually_requires():
    identity = {
        "workspace_id": "synthetic-v3", "index_path": "vault_index.json",
        "index_sha256": "a" * 64, "index_schema_version": 1,
        "vault_root_fingerprint": "root_synthetic", "document_count": 2,
        "fixture_id": None,
    }
    runtime = create_retrieval_tool_runtime(
        lambda *args: [], lambda ref: {}, expected_source_context=identity,
    )
    schema = runtime.registry.resolve("search_notes")[0].input_schema
    context = schema["properties"]["source_context"]
    for key, value in identity.items():
        if value is not None:
            assert key in context["required"]
        assert context["properties"][key]["enum"] == [value]


@pytest.mark.parametrize("nested_timeout", [False, True])
def test_strict_unknown_survives_string_code_and_nested_retryable_timeout(nested_timeout):
    class StrictUnknown(ProviderOutcomeUnknownError):
        code = "PROVIDER_OUTCOME_UNCERTAIN"

    class Client:
        calls = 0

        def generate_content(self, **kwargs):
            self.calls += 1
            if nested_timeout:
                try:
                    raise TimeoutError("synthetic timeout")
                except TimeoutError as cause:
                    raise StrictUnknown() from cause
            raise StrictUnknown()

    client = Client()
    response = GeminiProviderAdapter(client).complete(ModelTurnRequest(
        run_id="run_v3", turn_id="turn_v3", task_id="task_v3",
        agent_id="retrieval_agent", sequence=1, user_input="Synthetic query",
        available_tools=[], observation=None,
    ))
    assert response.error.outcome == "unknown_provider_outcome"
    assert response.error.retryable is False
    assert response.error.details["retry_classification"] is None
    assert client.calls == 1
