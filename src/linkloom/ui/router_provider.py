"""One strict DeepSeek routing proposal. No tool is executed."""
from dataclasses import dataclass, replace
import json
import logging
import os
from pathlib import Path
from urllib.request import HTTPRedirectHandler, Request, build_opener

from linkloom.agents.model_adapter import ModelTurnProposal
from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.providers.gemini_api import (
    GEMINI_38_FLASH_MODEL_ID, GeminiProviderAdapter, GeminiSDKClientBridge, build_gemini_sdk_client,
)
from linkloom.ui.structured_router import (
    CAPABILITY, CONFIG, INTENTS, PROMPT, SAFETY, TIME_ROLES, _reject_constant, _unique_object, routing_domain,
)
from linkloom.ui.ask_admission import admission_restriction, legacy_denial_reason, router_safety_reason
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def _submission(context):
    def enum(values):
        return {"type": "string", "enum": list(values)}
    properties = {
        "decision": enum(("ROUTE", "CLARIFY", "REFUSE")),
        "operation": enum(("READ", "WRITE_REQUEST", "OTHER")),
        "intent": enum(INTENTS),
        "candidate_id": enum(["", *(row["id"] for row in context["candidates"])]),
        "mentions": {"type": "array", "items": {"type": "string"}},
        "unresolved_names": {"type": "array", "items": {"type": "string"}},
        "entity_mentions": {"type": "array", "items": {"type": "string"}},
        "time_text": {"type": "string"},
        "time_role": enum(TIME_ROLES),
        "reason_code": enum(["NONE", *sorted(SAFETY | CAPABILITY)]),
    }
    return dict(type="function", function=dict(name="submit_route", strict=True,
        description="Return one read-only routing proposal; this function is never executed.",
        parameters=dict(type="object", properties=properties, required=list(properties),
                        additionalProperties=False)))


def _tool_arguments(message, tool, diagnostic=None):
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return None
    try:
        call = calls[0]
        if call["type"] != "function" or call["function"]["name"] != tool["function"]["name"]:
            return None
        args = json.loads(call["function"]["arguments"], object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
        schema = tool["function"]["parameters"]["properties"]
        # A reply without entity_mentions stays valid; the host then keeps its own name check (router/v4).
        if isinstance(args, dict) and set(schema) - args.keys() - {"entity_mentions"}:
            if diagnostic is not None:
                diagnostic["wire_invalid"] = "missing_fields"
            return None
        if not isinstance(args, dict) or set(args) | {"entity_mentions"} != set(schema):
            return None
        for key, value in args.items():
            prop = schema[key]
            if prop["type"] == "array":
                if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                    return None
            elif "enum" not in prop:
                if not isinstance(value, str):
                    return None
            elif value not in prop["enum"]:
                if diagnostic is not None:
                    diagnostic["wire_invalid"] = "invalid_enum"
                return None
        if message.get("content"):
            logging.getLogger(__name__).debug("Ignoring text accompanying a validated %s call: %r",
                                              tool["function"]["name"], message["content"])
        return args
    except (KeyError, TypeError, ValueError, RecursionError):
        return None


def _single_call_arguments(message, tool):
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        return "{}"
    try:
        call = calls[0]
        if call["type"] != "function" or call["function"]["name"] != tool["function"]["name"]:
            return "{}"
        arguments = call["function"]["arguments"]
        return arguments if isinstance(arguments, str) else "{}"
    except (KeyError, TypeError):
        return "{}"


def _route_text(message, context, *, diagnostics=False):
    diagnostic = {"wire_invalid": "schema_invalid"} if diagnostics else None
    args = _tool_arguments(message, _submission(context), diagnostic)
    if args is None:
        return json.dumps(diagnostic or {})
    try:
        proposed_operation = args.pop("operation")
        candidate = args.pop("candidate_id")
        query = context["question"]
        safety = router_safety_reason(query, operation=proposed_operation, reason=args["reason_code"],
                                      keyword_rules=False)
        if safety:
            args["reason_code"] = safety
        op = ("WRITE_REQUEST" if safety == "WRITE_NOT_ALLOWED" else
              "OTHER" if proposed_operation == "OTHER" or args["intent"] == "OTHER" and args["reason_code"] == "UNSUPPORTED_QUERY" else "READ")
        domain = next((row["domain"] for row in context["candidates"] if row["id"] == candidate), "NONE")
        # The host resolves the quoted time text itself (structured_router.resolve_time_text).
        version = "router/v5" if "entity_mentions" in args else "router/v4"
        return json.dumps(dict(args, schema_version=version, domain=routing_domain(domain, args["intent"]),
                               operation=op, candidate_ids=[candidate] if candidate else []), ensure_ascii=False)
    except (AssertionError, KeyError, TypeError, ValueError, RecursionError):
        return json.dumps(diagnostic or {})


class _DeepSeekJSONClient:
    def __init__(self, key, *, raw_sink=None, purpose="route"):
        self._key = key
        self._purpose = purpose
        self._raw_sink = raw_sink
        self._opener = build_opener(_NoRedirect())

    def create_chat_completion(self, **payload):
        user_text = next(message["content"] for message in payload["messages"] if message["role"] == "user")
        if self._purpose == "security":
            from linkloom.ui.security_gate import PROMPT as prompt, submission
        else:
            prompt, submission = PROMPT, _submission
        if not user_text.startswith(prompt):
            raise ValueError("ROUTER_PROMPT_MISMATCH")
        context = json.loads(user_text[len(prompt):])
        tool = submission(context)
        payload.pop("response_format", None)
        payload["tools"] = [tool]
        payload["tool_choice"] = {"type": "function", "function": {"name": tool["function"]["name"]}}
        request = Request(CONFIG["endpoint"], data=json.dumps(payload, ensure_ascii=False).encode("utf8"),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, method="POST")
        with self._opener.open(request, timeout=CONFIG["timeout_seconds"]) as response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("ROUTER_PROVIDER_RESPONSE_LIMIT")
            if self._raw_sink is not None:
                self._raw_sink(body, context)
            response = json.loads(body)
        choices = response.get("choices", [])
        if len(choices) == 1 and isinstance(choices[0].get("message"), dict):
            choice = choices[0]
            if choice.get("finish_reason") == "length":
                return response
            raw = "{}"
            if choice.get("finish_reason") == "tool_calls":
                if self._purpose == "security":
                    # Pass the single submit_safety argument text through unchanged so
                    # security_gate.classify validates it and can record the raw reply.
                    raw = _single_call_arguments(choice["message"], tool)
                else:
                    raw = _route_text(choice["message"], context, diagnostics=True)
            choice["message"] = {"content": raw}
            choice["finish_reason"] = "stop"
        return response


class GeminiRouterAdapter:
    """Router/Safety policy over the shared GeminiProviderAdapter.complete(); it never calls the SDK itself."""

    def __init__(self, client, *, purpose="route"):
        self.client, self.purpose = client, purpose
        self.provider = GeminiProviderAdapter(GeminiSDKClientBridge(client), model_id=GEMINI_38_FLASH_MODEL_ID)

    def complete(self, request):
        if self.purpose == "security":
            from linkloom.ui.security_gate import PROMPT as prompt, submission
        else:
            prompt, submission = PROMPT, _submission
        if not request.user_input.startswith(prompt):
            raise ValueError("ROUTER_PROMPT_MISMATCH")
        context = json.loads(request.user_input[len(prompt):])
        tool = submission(context)["function"]
        # Business differences travel as request options; the provider adapter owns mapping, call, errors and usage.
        options = replace(request.generation_options, temperature=None, thinking_level="low",
                          response_json_schema=tool["parameters"])
        response = self.provider.complete(replace(request, model_id=GEMINI_38_FLASH_MODEL_ID, generation_options=options))
        proposal = response.proposal
        if response.error is not None or proposal is None or proposal.kind != "final":
            return response
        # Only a complete reply is readable; truncation ("length") is rejected upstream by record_reply.
        text = proposal.final_answer if response.finish_reason == "stop" else "{}"
        if self.purpose == "route" and text != "{}":
            message = {"tool_calls": [{"type": "function", "function": {"name": tool["name"], "arguments": text}}]}
            text = _route_text(message, context, diagnostics=True)
        return replace(response, action=None, proposal=ModelTurnProposal.final(proposal.proposal_id, text))


@dataclass(frozen=True)
class RouterProvider:
    adapter: object
    external_provider: bool = False
    mode: str = "llm_first"
    security_adapter: object = None

    def reserve(self, app, owner, request, validate):
        models = owner.task_models
        limits = getattr(models, "provider_limits", None) or TaskProviderLimits(
            1, 12000, max(CONFIG["max_output_tokens"], CONFIG["security_max_output_tokens"]),
            .03, 6, 6, .00656)
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
    if os.environ.get("LINKLOOM_ROUTER_PROVIDER", "").strip().casefold() == "gemini":
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            return RouterProvider(None, external_provider=True, mode=mode)
        client = build_gemini_sdk_client(key, http_options_kwargs=dict(
            timeout=CONFIG["timeout_seconds"] * 1000, retry_options=dict(attempts=1)))
        return RouterProvider(GeminiRouterAdapter(client), external_provider=True, mode=mode,
                              security_adapter=GeminiRouterAdapter(client, purpose="security"))
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    adapter = DeepSeekProviderAdapter(_DeepSeekJSONClient(key), model_id=CONFIG["model_id"],
                                    json_output=True) if key else None
    security = DeepSeekProviderAdapter(_DeepSeekJSONClient(key, purpose="security"),
        model_id=CONFIG["model_id"], json_output=True) if key else None
    return RouterProvider(adapter, external_provider=True, mode=mode, security_adapter=security)
