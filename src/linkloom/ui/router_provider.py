"""Explicit DeepSeek JSON routing transport; no redirects, tools or retries."""
from dataclasses import dataclass, replace
import json
import os
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.ui.structured_router import CONFIG
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class _DeepSeekJSONClient:
    def __init__(self, key):
        self._key = key
        self._opener = build_opener(_NoRedirect())

    def create_chat_completion(self, **payload):
        request = Request(CONFIG["endpoint"], data=json.dumps(payload, ensure_ascii=False).encode("utf8"),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, method="POST")
        with self._opener.open(request, timeout=CONFIG["timeout_seconds"]) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("ROUTER_PROVIDER_RESPONSE_LIMIT")
            return json.loads(body)


@dataclass(frozen=True)
class RouterProvider:
    adapter: object
    external_provider: bool = False
    mode: str = "llm_first"

    def reserve(self, app, owner, request, validate):
        models = owner.task_models
        limits = getattr(models, "provider_limits", None) or TaskProviderLimits(1, 12000, 512, .02, 6, 6, .00656)
        limits = replace(limits, max_requests=1, request_timeout_seconds=min(limits.request_timeout_seconds, CONFIG["timeout_seconds"]))
        root = Path(app.repository.database_path).parent / "router-provider" / request.run_id
        session = TaskProviderSession(root, limits, validate)
        with owner._lock:
            owner._provider_sessions.append(session)
        return session


def configured_router():
    """Called only by the explicit Product CLI after its existing .env load."""
    mode = os.environ.get("LINKLOOM_LLM_ROUTER", "off").strip().casefold()
    if mode == "on":
        mode = "llm_first"
    if mode not in {"llm_first", "cascade"}:
        return None
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    adapter = DeepSeekProviderAdapter(_DeepSeekJSONClient(key), model_id=CONFIG["model_id"],
                                    json_output=True) if key else None
    return RouterProvider(adapter, external_provider=True, mode=mode)
