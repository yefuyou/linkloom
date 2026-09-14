"""Provider adapters kept outside the provider-neutral agent contracts."""

from .deepseek_api import (
    DeepSeekClient,
    DeepSeekProviderAdapter,
    build_deepseek_request,
    map_tool_definition_to_deepseek_tool,
)
from .gemini_api import (
    GeminiClient,
    GeminiProviderAdapter,
    build_gemini_request,
    map_tool_definition_to_gemini_function,
)

__all__ = [
    "DeepSeekClient",
    "DeepSeekProviderAdapter",
    "build_deepseek_request",
    "map_tool_definition_to_deepseek_tool",
    "GeminiClient",
    "GeminiProviderAdapter",
    "build_gemini_request",
    "map_tool_definition_to_gemini_function",
]
