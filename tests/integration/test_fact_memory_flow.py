"""Fact channel through the product routes, with LINKLOOM_FACT_MEMORY on and off."""
from __future__ import annotations

from collections import Counter
import json
from time import monotonic, sleep

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.fact_memory import FLAG
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor, ProviderRequestAuthorization
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.unified_ask import UnifiedAskService

SOURCE = """[2026-03-01T09:00:00Z] Mira: My favourite tea is Darjeeling.
[2026-03-02T09:00:00Z] Mira: He said the plant needs water.
[2026-05-10T09:00:00Z] Mira: These days my favorite tea is Roasted Oolong.
[2026-05-11T09:00:00Z] Mira: My dog's name is Pepper.
[2026-05-12T09:00:00Z] Mira: My name is Mira Okafor.
[2026-05-13T09:00:00Z] Theo: I moved to Lisbon last spring.
[2026-05-14T09:00:00Z] Mira: I finally picked a bakery near the station.
"""

CLAIMS = {
    "Darjeeling": ("I", "favourite tea", "Darjeeling"),
    "plant needs water": ("he", "needs", "water"),
    "Roasted Oolong": ("Mira", "favorite teas", "Roasted  Oolong"),
    "dog's name": ("my dog", "name", "Pepper"),
    "Mira Okafor": ("I", "name", "Mira Okafor"),
    "Lisbon": ("I", "city of residence", "Lisbon"),
    "bakery": ("I", "favorite bakery", "Sunrise Bakery"),  # value not in the message
}


class FactProvider:
    """Offline extractor: one FACT claim per message, no canonical relation."""

    def __init__(self) -> None:
        self.calls = Counter()

    def complete(self, request):
        prompt = request.user_input
        marker = next(marker for marker in CLAIMS if marker in prompt)
        self.calls[marker] += 1
        subject, relation, value = CLAIMS[marker]
        claim = {"claim_type": "FACT", "subject": subject, "relation": relation, "value": value,
                 "temporal_status": "NOT_STATED", "valid_from": None, "valid_to": None,
                 "confidence": {key: 0.9 for key in ("claim_type", "entity", "relation", "temporal", "overall")}}
        return ModelResponse(
            action=ModelAction.tool(ToolCall(call_id=f"c-{len(self.calls)}-{self.calls[marker]}",
                                             tool_id=request.available_tools[0].tool_id,
                                             arguments={"claims": [claim]})),
            usage=ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20),
            provider_response_id="offline", finish_reason="STOP")


class AnswerAdapter:
    """Stands in for the configured model in the bounded fact-answer call."""

    def __init__(self, reply) -> None:
        self.reply, self.prompts = reply, []

    def complete(self, request):
        self.prompts.append(request.user_input)
        return ModelResponse(action=ModelAction.final(self.reply(request.user_input)),
                             usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2))


class Investigation:
    external_provider = False
    task_only = False

    def __init__(self, adapter) -> None:
        self.model_factory = lambda _query: adapter

    def config(self):
        return {"available": True, "external_provider": False}


@pytest.fixture
def app(tmp_path):
    extractor = ProviderNeutralSemanticExtractor(
        FactProvider(), provider_id="offline", model_id="offline-model",
        request_guard=lambda request: ProviderRequestAuthorization.approve(
            request, guard_id="guard", reservation_id="r", estimated_cost_upper_bound_usd=0.0),
        request_guard_id="guard", max_attempts=1)
    application = ProductApplication(database_path=tmp_path / "product.sqlite", extractor=extractor)
    application.handle_post("/api/workspaces", {"name": "Personal facts"})
    created, _ = application.handle_post("/api/sources/import", {"name": "chat.txt", "content": SOURCE})
    deadline = monotonic() + 10
    while application.handle_get(f"/api/sources/{created['source']['source_id']}")["source"]["ingestion_status"] == "INGESTING":
        assert monotonic() < deadline
        sleep(0.01)
    yield application
    application.close()


def pending_by_value(app):
    return {item["proposed_value"]: item["candidate_id"] for item in app.handle_get("/api/review/candidates")["candidates"]}


def approve(app, candidate_id):
    return app.handle_post(f"/api/review/candidates/{candidate_id}/approve",
                           {"reviewer_id": "reviewer", "reason": "checked against the chat"})[0]


def ask(app, query, as_of=None, investigation=None):
    payload = {"query": query, "workspace_id": app._workspace_id()}
    if as_of:
        payload["as_of"] = as_of
    return UnifiedAskService(app, lambda: investigation, graph_service=False, slot_embedder=None).ask(payload)


def test_switch_off_keeps_the_current_behavior(app, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    pending = pending_by_value(app)
    assert len(pending) == 7
    with pytest.raises(ProductApplicationError) as blocked:
        approve(app, pending["Darjeeling"])
    assert blocked.value.status == 409
    assert blocked.value.code in {"ENTITY_UNRESOLVED", "RELATION_UNRESOLVED"}
    assert blocked.value.reason_codes is None  # no new field in the error payload
    assert len(pending_by_value(app)) == 7
    result, status = ask(app, "What is my favorite tea?")
    assert (status, result["error"]["code"]) == (200, "DECISION_SCOPE_NO_MATCH")
    tables = {row[0] for row in app.store._connection.execute("SELECT name FROM sqlite_master")}
    assert "fact_record" not in tables


def test_fact_channel_admits_supersedes_and_answers_by_time(app, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    pending = pending_by_value(app)
    first = approve(app, pending["Darjeeling"])
    assert first["channel"] == "FACT" and first["subject_id"] == "speaker:mira"
    assert first["attribute_key"] == "favorite tea" and first["value"] == "Darjeeling"
    assert first["effective_at"] == "2026-03-01T09:00:00+00:00"
    assert "RELATION_UNRESOLVED" in first["decision_reason_codes"]
    second = approve(app, pending["Roasted  Oolong"])
    assert second["attribute_key"] == "favorite tea"
    assert second["value"] == "Roasted Oolong"  # model wording, whitespace folded
    assert second["evidence_sentence"] == "These days my favorite tea is Roasted Oolong."
    assert second["supersedes_fact_id"] == first["fact_id"]
    dog = approve(app, pending["Pepper"])
    own = approve(app, pending["Mira Okafor"])
    assert (dog["subject_id"], own["subject_id"]) == ("mention:my dog", "speaker:mira")
    assert dog["attribute_key"] == own["attribute_key"] == "name"
    assert own["supersedes_fact_id"] is None  # different subject, no merge
    theo = approve(app, pending["Lisbon"])
    assert theo["subject_id"] == "speaker:theo"
    assert approve(app, pending["Darjeeling"])["fact_id"] == first["fact_id"]  # idempotent

    remaining = pending_by_value(app)
    assert set(remaining) == {"water", "Sunrise Bakery"}

    before, status = ask(app, "What is my favorite tea?", as_of="2026-04-01")
    assert status == 200 and before["path"] == "FACT" and before["status"] == "COMPLETED"
    assert before["conclusions"][0]["value"] == "Darjeeling"
    after, _ = ask(app, "What is my favorite tea?", as_of="2026-06-01")
    assert after["conclusions"][0]["value"] == "Roasted Oolong"
    assert after["evidence"][0]["supersedes_fact_id"] == first["fact_id"]
    assert "Roasted Oolong" in after["evidence"][0]["quote"]
    unrelated, _ = ask(app, "Which vendor did the platform team choose?")
    assert unrelated["error"]["code"] == "DECISION_SCOPE_NO_MATCH"


def test_rejected_facts_report_every_reason(app, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    pending = pending_by_value(app)
    with pytest.raises(ProductApplicationError) as pronoun:
        approve(app, pending["water"])
    assert "FACT_SUBJECT_PRONOUN_UNRESOLVED" in pronoun.value.reason_codes
    assert "RELATION_UNRESOLVED" in pronoun.value.reason_codes  # decision reasons are kept too
    with pytest.raises(ProductApplicationError) as ungrounded:
        approve(app, pending["Sunrise Bakery"])
    assert ungrounded.value.code == "FACT_VALUE_NOT_IN_SOURCE"
    assert ungrounded.value.reason_codes[-1] == "FACT_VALUE_NOT_IN_SOURCE"
    assert app.fact_store.list(app._workspace_id()) == ()
    assert set(pending_by_value(app)) == set(pending)


def test_model_answer_may_only_cite_offered_facts(app, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    pending = pending_by_value(app)
    for value in ("Darjeeling", "Roasted  Oolong"):
        approve(app, pending[value])

    def reply(prompt):
        records = json.loads(prompt.split("Records: ", 1)[1])
        current = records[-1]  # records arrive oldest first
        return json.dumps({"status": "ANSWERED", "answer": current["value"], "fact_ids": [current["id"]]})

    adapter = AnswerAdapter(reply)
    result, status = ask(app, "Which tea do I like best now?", as_of="2026-06-01",
                         investigation=Investigation(adapter))
    assert status == 200 and result["conclusions"][0]["value"] == "Roasted Oolong"
    assert "Question date: 2026-06-01T00:00:00+00:00" in adapter.prompts[-1]
    invented = AnswerAdapter(lambda _p: json.dumps({"status": "ANSWERED", "answer": "matcha", "fact_ids": ["F99"]}))
    refused, _ = ask(app, "Which tea do I like best now?", investigation=Investigation(invented))
    assert refused["status"] == "FAILED" and refused["error"]["code"] == "FACT_ANSWER_INVALID"
    unknown = AnswerAdapter(lambda _p: json.dumps({"status": "UNKNOWN", "answer": "", "fact_ids": []}))
    insufficient, _ = ask(app, "Which tea do I like best now?", investigation=Investigation(unknown))
    assert insufficient["status"] == "INSUFFICIENT" and not insufficient["conclusions"]


def test_deleted_source_hides_its_facts(app, monkeypatch):
    monkeypatch.setenv(FLAG, "1")
    approve(app, pending_by_value(app)["Darjeeling"])
    workspace_id = app._workspace_id()
    assert len(app.fact_records(workspace_id)) == 1
    source_id = app.handle_get("/api/sources")["sources"][0]["source_id"]
    app.handle_delete(f"/api/sources/{source_id}")
    assert app.fact_records(workspace_id) == ()
    result, _ = ask(app, "What is my favorite tea?")
    assert result["error"]["code"] == "DECISION_SCOPE_NO_MATCH"


@pytest.mark.parametrize("enabled", [False, True])
def test_http_approval_error_lists_all_reasons_only_with_the_switch(app, monkeypatch, enabled):
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from linkloom.ui.server import create_http_server
    if enabled:
        monkeypatch.setenv(FLAG, "1")
    else:
        monkeypatch.delenv(FLAG, raising=False)
    candidate_id = pending_by_value(app)["Sunrise Bakery"]
    server = create_http_server(None, product_app=app, port=0)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}/api/review/candidates/{candidate_id}/approve",
                          data=json.dumps({"reviewer_id": "reviewer", "reason": "checked"}).encode("utf-8"),
                          method="POST", headers={"Content-Type": "application/json"})
        with pytest.raises(HTTPError) as response:
            urlopen(request, timeout=5)
        error = json.loads(response.value.read().decode("utf-8"))["error"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert response.value.code == 409
    if enabled:
        assert error["code"] == "FACT_VALUE_NOT_IN_SOURCE"
        assert {"RELATION_UNRESOLVED", "FACT_VALUE_NOT_IN_SOURCE"} <= set(error["reason_codes"])
    else:
        assert set(error) == {"code", "message"}
