"""Standalone SDK constructor for synthetic diagnostics, without product changes."""
from google import genai
from google.genai import types


def build_diagnostic_sdk(api_key, diagnostics, *, proxy=None, trust_env=True, transport=None):
    args = {'event_hooks': diagnostics.hooks(), 'trust_env': trust_env, 'http2': False}
    if proxy is not None:
        args['proxy'] = proxy
    if transport is not None:
        args['transport'] = transport
    return genai.Client(api_key=api_key, vertexai=False, http_options=types.HttpOptions(
        timeout=130000, retry_options=types.HttpRetryOptions(attempts=1), client_args=args,
    ))
