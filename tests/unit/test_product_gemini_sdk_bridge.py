from __future__ import annotations

from types import SimpleNamespace

from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.ui.__main__ import GeminiSDKClientBridge


def test_product_gemini_bridge_adapts_official_sdk_client_to_provider_contract() -> None:
    response = object()
    captured: dict[str, object] = {}

    def generate_content(**kwargs: object) -> object:
        captured.update(kwargs)
        return response

    sdk_client = SimpleNamespace(
        models=SimpleNamespace(generate_content=generate_content)
    )
    bridge = GeminiSDKClientBridge(sdk_client)

    GeminiProviderAdapter(bridge, model_id="gemini-3.8-flash")
    actual = bridge.generate_content(
        model="gemini-3.8-flash",
        contents=[{"role": "user", "parts": [{"text": "release acceptance"}]}],
        config={"thinking_config": {"thinking_level": "LOW"}},
    )

    assert actual is response
    assert captured == {
        "model": "gemini-3.8-flash",
        "contents": [{"role": "user", "parts": [{"text": "release acceptance"}]}],
        "config": {"thinking_config": {"thinking_level": "LOW"}},
    }
