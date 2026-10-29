"""Offline Product Ask fixtures shared by integration tests and the browser demo.

The fixture owns only synthetic data and localhost resources.  The two provider
seams are deliberately injected: source extraction uses a tiny provider-neutral
fake, while investigation uses ``GeminiProviderAdapter`` over a transport that
returns SDK-shaped function calls and final text.  No credential lookup or
network provider is reachable from this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from threading import Lock, Thread
from time import monotonic, sleep
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
)
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.tools.contracts import ToolCall
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.product import ProductApplication
from linkloom.ui.server import create_http_server


ATLAS = "Atlas"
OTHER = "Other"
SUBJECT = "Launch"
RELATION = "uses supplier"
CURRENT_AS_OF = "2026-10-10"
HISTORICAL_AS_OF = "2026-10-06"


def claim(value: str, *, claim_type: str = "DECISION") -> dict[str, Any]:
    """Return a valid synthetic semantic candidate for the product pipeline."""

    return {
        "claim_type": claim_type,
        "subject": SUBJECT,
        "relation": RELATION,
        "value": value,
        "temporal_status": "SOURCE_EVENT_TIME" if claim_type == "DECISION" else "NOT_STATED",
        "valid_from": None,
        "valid_to": None,
        "confidence": {
            "claim_type": 0.98,
            "entity": 0.95,
            "relation": 0.94,
            "temporal": 0.91,
            "overall": 0.94,
        },
    }


class OfflineSemanticProvider:
    """Minimal provider-neutral extractor seam for deterministic source setup."""

    def __init__(self, claims: list[dict[str, Any]]) -> None:
        self._claims = [deepcopy(item) for item in claims]
        self.requests = 0
        self._lock = Lock()

    def complete(self, request: Any) -> ModelResponse:
        with self._lock:
            if not self._claims:
                raise AssertionError("the synthetic source fixture exhausted its claims")
            candidate = deepcopy(self._claims.pop(0))
            self.requests += 1
            ordinal = self.requests
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall(
                    call_id=f"offline-extract-{ordinal}",
                    tool_id=request.available_tools[0].tool_id,
                    arguments={"claims": [candidate]},
                )
            ),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            provider_request_id=f"offline-extract-request-{ordinal}",
            provider_response_id=f"offline-extract-response-{ordinal}",
        )


class UnknownProviderError(Exception):
    """Synthetic provider exception that maps to unknown provider outcome."""


class FailedProviderError(Exception):
    """Synthetic provider exception that maps to a known provider failure."""

    status_code = 503


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _function_responses(contents: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    responses: list[tuple[str, Any]] = []
    for item in _walk(contents):
        call = item.get("function_response")
        if not isinstance(call, dict):
            continue
        name = call.get("name")
        response = call.get("response")
        if isinstance(name, str):
            responses.append((name, response))
    return responses


def _source_context(contents: list[dict[str, Any]]) -> dict[str, Any]:
    """Recover the trusted source_context embedded in the Runtime prompt."""

    for item in _walk(contents):
        parts = item.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if not isinstance(text, str) or "team_decision_request=" not in text:
                continue
            raw = text.rsplit("team_decision_request=", 1)[1]
            try:
                request = json.loads(raw)
            except (TypeError, ValueError):
                continue
            context = request.get("source_context") if isinstance(request, dict) else None
            if isinstance(context, dict):
                return deepcopy(context)
    return {}


def _observed_search_values(contents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the model-visible values from the latest search observation.

    Runtime evidence IDs are intentionally minted by the active retrieval
    backend (``ev_v2_*``).  The offline model must cite those IDs exactly; it
    must not substitute the semantic-ingestion IDs returned by Product Ask's
    Decision Memory view.
    """

    values: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, response in _function_responses(contents):
        if name != "search_notes" or not isinstance(response, dict):
            continue
        observed = response.get("value")
        if not isinstance(observed, list):
            continue
        for item in observed:
            if not isinstance(item, dict):
                continue
            evidence_id = item.get("evidence_id")
            if not isinstance(evidence_id, str) or evidence_id in seen:
                continue
            seen.add(evidence_id)
            values.append(deepcopy(item))
    return values


def _evidence_id_for_source(
    contents: list[dict[str, Any]], source_id: str,
) -> str:
    """Resolve one Runtime evidence ID by its trusted source identity."""

    expected_path = f"{source_id}.md"
    for item in _observed_search_values(contents):
        if item.get("relative_path") == expected_path:
            evidence_id = item.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id:
                return evidence_id
    raise AssertionError(
        f"offline search observation did not contain Runtime evidence for {expected_path}"
    )


def _observed_temporal_view(contents: list[dict[str, Any]]) -> dict[str, Any]:
    """Return the reviewed state observed through the memory tool.

    The Runtime deliberately projects Product's trusted ``authorization`` as
    ``review_receipt`` before exposing it to the provider.  Reading this
    observation keeps the fake model subject to the same scope and evidence
    contract as a real model, including an omitted ``as_of`` and historical
    lookups.
    """

    for name, response in reversed(_function_responses(contents)):
        if name != "search_decision_memory" or not isinstance(response, dict):
            continue
        values = response.get("value")
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict) or item.get("kind") != "reviewed_temporal_state":
                continue
            view = item.get("view")
            if isinstance(view, dict) and view.get("status") == "FOUND":
                return deepcopy(view)
    raise AssertionError("offline model did not observe a reviewed temporal state")


class FakeTransport:
    """SDK-shaped Gemini transport for the temporal Runtime.

    The transport observes the exact ``contents`` payload produced by
    ``GeminiProviderAdapter``.  It returns only function-call JSON or a final
    JSON text response; it never executes a tool and never opens a socket.
    """

    def __init__(
        self,
        *,
        scenario: str,
    ) -> None:
        self.scenario = scenario
        self.requests: list[dict[str, Any]] = []
        self.final_sent = False
        self._lock = Lock()

    def generate_content(
        self,
        *,
        model: str,
        contents: list[dict[str, Any]],
        config: dict[str, Any],
    ) -> dict[str, Any]:
        with self._lock:
            self.requests.append({"model": model, "contents": contents, "config": config})
            ordinal = len(self.requests)
        if self.scenario == "uncertain" and ordinal == 1:
            raise UnknownProviderError("synthetic provider result is unknown")
        if self.scenario == "fail" and ordinal == 1:
            raise FailedProviderError("synthetic provider failed")

        responses = _function_responses(contents)
        names = [name for name, _ in responses]
        if "search_decision_memory" not in names:
            return self._tool_call(ordinal, "search_decision_memory", {
                "query": "reviewed Launch supplier decision",
                "limit": 5,
            })
        if "search_notes" not in names:
            return self._tool_call(ordinal, "search_notes", {
                "query": "Birchline Wrenwell Launch supplier",
                "source_context": _source_context(contents),
                "limit": 5,
            })

        read_count = names.count("read_verified_note")
        view = _observed_temporal_view(contents)
        effective = view.get("effective")
        if not isinstance(effective, dict):
            raise AssertionError("offline reviewed state has no effective decision")
        if read_count == 0:
            return self._tool_call(ordinal, "read_verified_note", {
                "note_ref": _evidence_id_for_source(
                    contents, effective["evidence"][0]["source_id"]
                ),
            })
        if read_count == 1 and effective.get("supersedes_id") is not None:
            predecessor = next(
                item for item in view["history"]
                if item["decision_id"] == effective["supersedes_id"]
            )
            return self._tool_call(ordinal, "read_verified_note", {
                "note_ref": _evidence_id_for_source(
                    contents, predecessor["evidence"][0]["source_id"]
                ),
            })

        self.final_sent = True
        return {
            "text": json.dumps(self._final_payload(view, contents), ensure_ascii=False),
            "response_id": f"offline-gemini-final-{ordinal}",
            "finish_reason": "STOP",
            "usage_metadata": {
                "prompt_token_count": 80,
                "candidates_token_count": 40,
                "total_token_count": 120,
            },
        }

    def _tool_call(self, ordinal: int, name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "function_calls": [{
                "id": f"offline-gemini-call-{ordinal}",
                "name": name,
                "args": args,
            }],
            "response_id": f"offline-gemini-response-{ordinal}",
            "finish_reason": "STOP",
        }

    def _final_payload(
        self, view: dict[str, Any], contents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        effective = view["effective"]
        predecessor = None
        if effective.get("supersedes_id") is not None:
            predecessor = next(
                item for item in view["history"]
                if item["decision_id"] == effective["supersedes_id"]
            )
        receipt = effective.get("review_receipt") or effective.get("authorization")
        if not isinstance(receipt, dict) or not isinstance(receipt.get("receipt_id"), str):
            raise AssertionError("offline reviewed state has no trusted review receipt")
        receipt_id = receipt["receipt_id"]
        evidence_ids = [
            _evidence_id_for_source(contents, item["source_id"])
            for item in effective["evidence"]
        ]
        prior_evidence_ids = [] if predecessor is None else [
            _evidence_id_for_source(contents, item["source_id"])
            for item in predecessor["evidence"]
        ]
        if self.scenario in {"forged", "forged_receipt"}:
            receipt_id = "forged-receipt"
        if self.scenario in {"forged", "wrong_evidence"}:
            evidence_ids = [
                "forged-evidence" if self.scenario == "forged" else "wrong-evidence"
            ]
        payload: dict[str, Any] = {
            "schema_version": "claim-proposal/v1",
            "claims": [],
            "temporal_decision": {
                "subject": view["subject"],
                "relation": view["relation"],
                "as_of": view["as_of"],
                "decision_id": effective["decision_id"],
                "receipt_id": receipt_id,
                "value": effective["value"],
                "supersedes_id": effective["supersedes_id"],
                "evidence_ids": evidence_ids,
                "prior_evidence_ids": prior_evidence_ids,
            },
        }
        if effective.get("supersedes_id") is not None:
            payload["uncertainty"] = {
                "status": "insufficient_evidence",
                "unknown_fields": ["replacement_reason"],
            }
        return payload


class _IdleRunBackend:
    """Backend required by the HTTP server for the legacy /api/runs seam."""

    def context(self) -> dict[str, Any]:
        return {"schema_version": "offline-unified-ask-fixture/v1", "workspace": {"display_name": ATLAS}}

    def start(self, query: str) -> dict[str, Any]:
        raise AssertionError("the Unified Ask fixture does not exercise legacy runtime starts")

    def inspect(self, run_id: str) -> dict[str, Any] | None:
        return None


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> tuple[int, dict[str, Any]]:
    body = None if data is None else json.dumps(data, ensure_ascii=False).encode("utf-8")
    request = Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as error:
        response = error
    with response:
        payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise AssertionError("fixture HTTP endpoint returned a non-object JSON payload")
        return response.status, payload


def request_bytes(base_url: str, path: str) -> tuple[int, bytes]:
    request = Request(base_url + path, method="GET")
    try:
        response = urlopen(request, timeout=60)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def wait_source(base_url: str, source_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        status, payload = request_json(base_url, f"/api/sources/{source_id}")
        if status != 200:
            raise AssertionError(payload)
        source = payload["source"]
        if source["ingestion_status"] != "INGESTING":
            return source
        sleep(0.01)
    raise AssertionError(f"source ingestion did not finish: {source_id}")


@dataclass
class UnifiedAskFixture:
    """A running, seeded, offline Product Ask server."""

    database_path: Path
    port: int = 0
    seed: bool = True
    app: ProductApplication | None = None
    server: Any | None = None
    worker: Thread | None = None
    workspace_ids: dict[str, str] | None = None
    service: ProductInvestigationService | None = None
    transports: list[FakeTransport] | None = None

    def __post_init__(self) -> None:
        self.database_path = Path(self.database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        semantic_provider = OfflineSemanticProvider([
            claim("Birchline"),
            claim("Wrenwell"),
            claim("Cedar", claim_type="PROPOSAL"),
        ])
        extractor = ProviderNeutralSemanticExtractor(
            semantic_provider,
            provider_id="offline-semantic-provider",
            model_id="offline-semantic-model",
            request_guard=lambda request: ProviderRequestAuthorization.approve(
                request,
                guard_id="offline-fixture-guard/v1",
                reservation_id=f"offline-fixture-reservation-{uuid4().hex[:10]}",
                estimated_cost_upper_bound_usd=0.0,
            ),
            request_guard_id="offline-fixture-guard/v1",
            max_attempts=1,
        )
        self.app = ProductApplication(
            database_path=self.database_path,
            extractor=extractor,
            relation_resolver=FrozenRelationResolver(
                (RELATION,), schema_version="offline-unified-ask-relations/v1"
            ),
        )
        self.server = create_http_server(_IdleRunBackend(), product_app=self.app, port=self.port)
        self.worker = Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        try:
            if self.seed:
                self._seed_synthetic_data()
        except BaseException:
            # A failed seed must not leave a ThreadingHTTPServer holding a
            # requested demo port after the constructor raises.
            self.stop()
            raise
        self.workspace_ids = self.workspace_ids or {}
        self.transports = []

    @property
    def base_url(self) -> str:
        if self.server is None:
            raise RuntimeError("the fixture is stopped")
        return f"http://127.0.0.1:{self.server.server_port}"

    def _seed_synthetic_data(self) -> None:
        status, payload = request_json(self.base_url, "/api/workspaces", method="POST", data={"name": ATLAS})
        if status != 201:
            raise AssertionError(payload)
        atlas_id = payload["workspace"]["workspace_id"]
        self._import_and_review(
            name="decision-2026-10-01.md",
            content="[2026-10-01T10:00:00Z] Alice: We decided to use Birchline as the supplier for Launch.",
            value="Birchline",
            approve=True,
        )
        self._import_and_review(
            name="decision-2026-10-07.md",
            content="[2026-10-07T10:00:00Z] Alice: We decided to use Wrenwell as the supplier for Launch.",
            value="Wrenwell",
            approve=True,
        )
        self._import_and_review(
            name="proposal-2026-10-09.md",
            content="[2026-10-09T10:00:00Z] Bob: Someone proposes Cedar as a replacement supplier for Launch.",
            value="Cedar",
            approve=False,
        )
        status, payload = request_json(self.base_url, "/api/workspaces", method="POST", data={"name": OTHER})
        if status != 201:
            raise AssertionError(payload)
        other_id = payload["workspace"]["workspace_id"]
        status, payload = request_json(
            self.base_url,
            "/api/workspaces/current",
            method="POST",
            data={"workspace_id": atlas_id},
        )
        if status != 200:
            raise AssertionError(payload)
        self.workspace_ids = {ATLAS: atlas_id, OTHER: other_id}

    def _import_and_review(self, *, name: str, content: str, value: str, approve: bool) -> None:
        status, payload = request_json(
            self.base_url,
            "/api/sources/import",
            method="POST",
            data={"name": name, "content": content},
        )
        if status != 202:
            raise AssertionError(payload)
        source = wait_source(self.base_url, payload["source"]["source_id"])
        if source["ingestion_status"] != "COMPLETE":
            raise AssertionError(source)
        if not approve:
            return
        status, inbox = request_json(self.base_url, "/api/review/candidates")
        if status != 200:
            raise AssertionError(inbox)
        candidate = next(
            item for item in inbox["candidates"] if item["proposed_value"] == value
        )
        status, result = request_json(
            self.base_url,
            f"/api/review/candidates/{candidate['candidate_id']}/approve",
            method="POST",
            data={"reviewer_id": "offline-fixture-reviewer", "reason": "Synthetic acceptance fixture"},
        )
        if status != 200 or result.get("workflow_state") != "MATERIALIZED":
            raise AssertionError(result)

    def configure_agent(
        self,
        *,
        scenario: str = "normal",
        external_provider: bool = False,
        configured: bool = True,
        max_steps: int = 8,
    ) -> ProductInvestigationService:
        """Replace the dynamic investigation service with an offline service."""

        if self.app is None or self.server is None or self.transports is None:
            raise RuntimeError("fixture not initialized")

        def model_factory(_query: str) -> GeminiProviderAdapter:
            selected_scenario = scenario
            normalized_query = _query.casefold()
            if scenario == "normal":
                if "uncertain provider" in normalized_query:
                    selected_scenario = "uncertain"
                elif "fail provider" in normalized_query:
                    selected_scenario = "fail"
            transport = FakeTransport(scenario=selected_scenario)
            self.transports.append(transport)
            return GeminiProviderAdapter(transport)

        service = ProductInvestigationService(
            self.app,
            model_factory=model_factory if configured else None,
            run_mode="OFFLINE_FAKE_GEMINI",
            max_steps=max_steps,
            external_provider=external_provider,
        )
        self.service = service
        self.server.investigation_service = service
        return service

    def select_workspace(self, name: str) -> str:
        if not self.workspace_ids or name not in self.workspace_ids:
            raise KeyError(name)
        status, payload = request_json(
            self.base_url,
            "/api/workspaces/current",
            method="POST",
            data={"workspace_id": self.workspace_ids[name]},
        )
        if status != 200:
            raise AssertionError(payload)
        return self.workspace_ids[name]

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.worker is not None:
            self.worker.join(timeout=3)
        if self.app is not None:
            self.app.close()
        self.server = None
        self.worker = None


def poll_product_ask(
    fixture: UnifiedAskFixture,
    run_id: str,
    workspace_id: str,
    *,
    timeout: float = 90.0,
) -> tuple[int, dict[str, Any]]:
    """Bounded client wait; expiration does not mean the Runtime has failed."""

    deadline = monotonic() + timeout
    latest_status = 202
    latest: dict[str, Any] = {}
    while monotonic() < deadline:
        latest_status, latest = request_json(
            fixture.base_url,
            f"/api/product/ask/runs/{run_id}?workspace_id={workspace_id}",
        )
        result = latest.get("result", {})
        if result.get("status") not in {"INVESTIGATING"}:
            return latest_status, latest
        sleep(0.01)
    raise AssertionError(
        f"HTTP polling deadline ({timeout}s) reached; Runtime may still be running: "
        f"{latest_status} {latest}"
    )


__all__ = [
    "ATLAS",
    "OTHER",
    "CURRENT_AS_OF",
    "HISTORICAL_AS_OF",
    "RELATION",
    "SUBJECT",
    "FakeTransport",
    "UnifiedAskFixture",
    "poll_product_ask",
    "request_bytes",
    "request_json",
    "wait_source",
]
