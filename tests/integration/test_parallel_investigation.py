import json
from threading import Barrier, Event
from time import sleep

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelProviderError, ModelResponse, ModelUsage
from linkloom.observability.reader import TraceReader
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.tools.contracts import ToolCall
from linkloom.ui.branch_budget import classify_branch_for_resume
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.task_provider import TaskProviderLimits
from tests.integration.test_product_application_flow import (
    _claim, _client, _start_product_server, _stop_product_server, _wait_source,
)


QUESTION = ("Which plan replaced Meridian A, who owns it, who is that owner's direct leader, "
            "and when did the replacement take effect?")
FACTS = (
    "Cobalt B replaced Meridian A on 2025-02-14",
    "Mira Chen is the accountable owner of Cobalt B",
    "Mira Chen reports directly to Kai Wu",
)


class MeetingProvider:
    def __init__(self, question, overlap, mode, release):
        self.chain = question.startswith("What plan")
        self.overlap, self.mode, self.release = overlap, mode, release
        self.calls = 0
        self.refs = []

    def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            self.overlap.wait(timeout=8)
            if self.mode in ("cancel", "source_change"):
                assert self.release.wait(8)
            if not self.chain and self.mode == "timeout":
                assert self.release.wait(10)
            if not self.chain and self.mode in ("partial", "hard", "uncertain"):
                code, category = {
                    "partial": ("MODEL_UNAVAILABLE", "unavailable_model"),
                    "hard": ("MODEL_AUTH_REQUIRED", "authentication"),
                    "uncertain": ("MODEL_UNKNOWN_FAILURE", "unknown_provider_error"),
                }[self.mode]
                return ModelResponse(error=ModelProviderError(code, category, "Synthetic Provider failure.",
                    outcome="unknown_provider_outcome" if self.mode == "uncertain" else "known_failure"))
        if request.observation and isinstance(request.observation.value, dict):
            self.refs.append(request.observation.value["evidence_id"])
        if self.calls == (7 if self.chain else 3):
            statements = "; ".join(FACTS) if self.chain else FACTS[0]
            action = ModelAction.final(json.dumps(dict(schema_version="team-decision-result/v1",
                decision=dict(value=statements, status="approved", evidence_refs=self.refs),
                rationale=[], rejected_alternatives=[], actions=[], unresolved_items=[],
                uncertainty=dict(status="none", statement=None, unknown_fields=[], evidence_refs=[]),
                evidence_refs=self.refs)))
        elif self.calls % 2:
            context = json.loads(request.user_input.rsplit("team_decision_request=", 1)[1])["source_context"]
            query = "Meridian A"
            if self.chain:
                goal, dependencies, query = (
                    ("successor", {}, "Meridian A"),
                    ("owner", {"successor": "Cobalt B"}, "Cobalt B"),
                    ("leader", {"owner": "Mira Chen"}, "Mira Chen"),
                )[(self.calls - 1) // 2]
                context.update(subgoal_id=goal, dependency_entities=dependencies)
            action = ModelAction.tool(ToolCall(call_id=f"search-{self.calls}", tool_id="search_notes",
                arguments=dict(query=query, limit=5, source_context=context)))
        else:
            quote = FACTS[(self.calls - 2) // 2] if self.chain else FACTS[0]
            hit = next(hit for hit in request.observation.value if quote in hit["quote"])
            action = ModelAction.tool(ToolCall(call_id=f"read-{self.calls}", tool_id="read_verified_note",
                arguments=dict(note_ref=hit["evidence_id"])))
        return ModelResponse(action=action, usage=ModelUsage(input_tokens=10, output_tokens=10))


@pytest.mark.parametrize("mode,expected", [
    ("complete", "COMPLETE"), ("partial", "PARTIAL"), ("uncertain", "PARTIAL"),
    ("timeout", "PARTIAL"), ("hard", "ABORTED"), ("cancel", "CANCELLED"),
    ("source_change", "ABORTED"), ("review_rejected", "PARTIAL"),
    ("late_source_change", "ABORTED"), ("late_cancel", "CANCELLED"),
])
def test_scoped_parallel_investigation_uses_two_durable_runtimes(tmp_path, monkeypatch, mode, expected):
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")
    base, server, worker, app = _start_product_server(tmp_path, [
        _claim("Cobalt B"), _claim("Cobalt B", claim_type="PROPOSAL"),
        _claim("Cobalt B", claim_type="PROPOSAL"), _claim("Cobalt B", claim_type="PROPOSAL"),
    ])
    release = Event()
    try:
        _, workspace = _client(base, "/api/workspaces", method="POST", data={"name": "Meetings"})
        workspace_id = workspace["workspace"]["workspace_id"]
        _, scope = _client(base, "/api/sources/import", method="POST", data=dict(
            name="approval.md", content="[2025-02-10T10:00:00Z] Alice: We decided to use Cobalt B as the supplier for Launch."))
        _wait_source(base, scope["source"]["source_id"])
        _, inbox = _client(base, "/api/review/candidates")
        status, approval = _client(base, f"/api/review/candidates/{inbox['candidates'][0]['candidate_id']}/approve",
            method="POST", data=dict(reviewer_id="local", reason="Synthetic reviewed scope"))
        assert status == 200, approval
        for index, fact in enumerate(FACTS, 1):
            _, imported = _client(base, "/api/sources/import", method="POST", data=dict(
                name=f"meeting-{index}.md", content=f"[2025-02-1{index}T10:00:00Z] Alice: {fact}."))
            _wait_source(base, imported["source"]["source_id"])

        overlapped = Event()
        barrier = Barrier(2, action=overlapped.set)
        models = {}

        def factory(question):
            model = MeetingProvider(question, barrier, mode, release)
            models[question] = model
            return model

        if mode == "review_rejected":
            # The Runtime already fails a Team Decision run whose Reviewer decision is not
            # evidence_sufficient, so no Provider output reaches this gate with a real rejection.
            # Reject branch-2's artifact (the single-fact branch) at the Reviewer projection instead.
            from linkloom.ui import parallel_investigation
            project_reviewer = parallel_investigation.project_reviewer

            def reject_single_fact(artifact):
                review = project_reviewer(artifact)
                value = artifact["result"]["team_decision"]["decision"]["value"]
                return dict(review, decision="evidence_insufficient") if value == FACTS[0] else review

            monkeypatch.setattr(parallel_investigation, "project_reviewer", reject_single_fact)

        limits = TaskProviderLimits(14, 2_000_000, 60_000, 3.0, 20, 1 if mode == "timeout" else 10, .01)
        service = ProductInvestigationService(app, model_factory=factory, external_provider=False,
                                              parallel_limits=limits)
        server.investigation_service = service
        request = dict(query=QUESTION, workspace_id=workspace_id, subject="Launch", relation="uses supplier",
                       as_of="2025-02-20")
        if mode == "complete":
            service.parallel_limits = None
            _, default = _client(base, "/api/product/ask", method="POST", data=request)
            assert "parallel_investigation" not in default["result"]
            assert not models
            service.parallel_limits = limits
        status, payload = _client(base, "/api/product/ask", method="POST", data=request)
        started = payload["result"]
        assert status == 202, started
        assert started["parallel_investigation"]["status"] == "RUNNING"
        run_id = started["agent_run_id"]
        root = tmp_path / "work" / "investigations" / run_id.removeprefix("ui-parallel-")
        assert overlapped.wait(12), json.dumps(dict(models={q: m.calls for q,m in models.items()},
            budgets=[json.loads(path.read_text())['failure'] for path in root.glob("branches/*/budget.json")],
            errors=[event['error'] for path in root.glob("branches/*/traces/*/events.jsonl")
                    for event in map(json.loads, path.read_text().splitlines()) if event.get('error')]))
        if mode == "cancel":
            assert service.cancel_parallel(run_id)
        elif mode == "source_change":
            with app._store_lock:
                app.repository.create_workspace("Different workspace")
        if mode == "timeout":
            timeout_receipt = root / "branches" / "branch-2" / "budget.json"
            for _ in range(200):
                try:
                    if json.loads(timeout_receipt.read_text())["failure"] == "PROVIDER_TIMEOUT_UNCERTAIN":
                        break
                except (OSError, ValueError):
                    pass
                sleep(.025)
            else:
                pytest.fail("Timed-out Provider request was not durably recorded")
        release.set()
        service.close(timeout=25)
        if mode.startswith("late_"):
            published = service.inspect(run_id)
            assert published["parallel_investigation"]["status"] == "COMPLETE"
            assert len(published["evidence"]) == 3
            if mode == "late_cancel":
                assert service.cancel_parallel(run_id)
            else:
                with app._store_lock:
                    app.repository.create_workspace("Different workspace")
        final = service.inspect(run_id)
        outcome = final["parallel_investigation"]
        assert outcome["status"] == expected, json.dumps(dict(outcome=outcome,
            calls={q: m.calls for q, m in models.items()},
            errors=[event['error'] for path in root.glob("branches/*/traces/*/events.jsonl")
                    for event in map(json.loads, path.read_text().splitlines()) if event.get('error')]))
        receipts = [json.loads(path.read_text()) for path in root.glob("branches/*/budget.json")]
        assert len(receipts) == 2
        for field, limit in (
            ("requests_reserved", limits.max_requests), ("input_tokens_reserved", limits.max_input_tokens),
            ("output_tokens_reserved", limits.max_output_tokens), ("cost_reserved_usd", limits.max_cost_usd),
        ):
            assert sum(receipt[field] for receipt in receipts) <= limit
        if expected in ("ABORTED", "CANCELLED"):
            assert not final["evidence"]
            assert all(classify_branch_for_resume(path.parent) == "WITHHELD"
                       for path in root.glob("branches/*/budget.json"))
        else:
            status, projected = _client(base, f"/api/product/ask/runs/{run_id}?workspace_id={workspace_id}")
            assert status == 200, projected
            assert projected["result"]["parallel_investigation"] == outcome
            assert len(projected["result"]["evidence"]) == 3
            assert len(final["evidence"]) == 3, final
            assert len({row["source_id"] for row in final["evidence"]}) == 3
            assert outcome["gaps"] == ({} if mode == "complete" else {
                "branch-2": "UNCERTAIN" if mode in ("timeout", "uncertain") else "FAILED"})
            assert [row["branch"] for row in outcome["branches"]] == (
                ["branch-1", "branch-2"] if mode == "complete" else ["branch-1"])
            for branch in outcome["branches"]:
                path = root / "branches" / branch["branch"]
                state = SQLiteCheckpointer(path / "runtime").load(branch["thread_id"])
                assert state.run_id == branch["run_id"] and state.status == "completed"
                assert any(row.tool_id == "read_verified_note" for row in state.tool_ledger)
                trace = TraceReader(path / "traces")
                assert trace.read_manifest(state.run_id).complete
                assert trace.read_events(state.run_id)
            if mode in ("timeout", "uncertain"):
                assert next(model for model in models.values() if not model.chain).calls == 1
                assert classify_branch_for_resume(root / "branches" / "branch-2") == "UNCERTAIN"
    finally:
        release.set()
        _stop_product_server(server, worker, app)
