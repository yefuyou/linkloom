"""Offline production composition acceptance, including cold-store resume."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelProviderError
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.observability.reader import TraceReader
from linkloom.runtime.artifacts import ModelArtifactStore
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.errors import RuntimeModelError
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


class ProcessCrash(BaseException):
    """Test-only process death which bypasses ordinary error finalization."""


class CrashCheckpointer(SQLiteCheckpointer):
    def __init__(self, root, boundary):
        super().__init__(root)
        self.boundary = boundary

    def save(self, state, checkpoint_id=None):
        result = super().save(state, checkpoint_id)
        should_crash = (
            state.model_executions
            and state.model_executions[-1].status == self.boundary
        )
        if self.boundary == "response_durable_final":
            latest = state.model_executions[-1] if state.model_executions else None
            action = latest.normalized_action if latest is not None else None
            should_crash = bool(
                latest is not None
                and latest.status == "response_durable"
                and isinstance(action, dict)
                and action.get("kind") == "final"
            )
        if should_crash:
            raise ProcessCrash(self.boundary)
        return result


class PolicyProvider:
    """Model policy owns the next action; fixture/executor never sequences it."""

    def __init__(self, *, read=False, query="shared", crash=False, failure=False):
        self.read = read
        self.query = query
        self.crash = crash
        self.failure = failure
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        if self.crash:
            raise ProcessCrash("provider in flight")
        if self.failure:
            return ModelResponse(error=ModelProviderError(
                code="MODEL_UNAVAILABLE", category="unavailable_model",
                message="Synthetic model unavailable.",
            ))
        observation = request.observation
        if observation is None:
            tool = "search_notes"
            arguments = {"query": self.query, "source_context": {"policy": "m04"}, "limit": 2}
        elif (self.read and observation.status == "ok" and observation.value
              and request.previous_tool_call.tool_id == "search_notes"):
            tool = "read_verified_note"
            arguments = {"note_ref": observation.value[-1]["evidence_id"]}
        else:
            return ModelResponse(action=ModelAction.final("Synthetic final from observed evidence."))
        return ModelResponse(action=ModelAction.tool(ToolCall(
            call_id=f"proposal:{request.sequence}", tool_id=tool, arguments=arguments,
            run_id=request.run_id, task_id=request.task_id, agent_id=request.agent_id,
            sequence=request.sequence,
        )))


@pytest.fixture
def environment(monkeypatch):
    # Workspace-local unique roots avoid unrelated protected Windows temp paths.
    root = Path(__file__).resolve().parents[2] / ".tmp" / f"m04-{uuid4().hex}"
    vault = root / "vault"
    vault.mkdir(parents=True)
    for name in ("alpha", "beta"):
        (vault / f"{name}.md").write_text(f"# {name}\n\nShared evidence for {name}.\n", encoding="utf-8")
    index = scan_vault(vault, root / "scan").index_path
    invocations, projections = [], []
    search, read = RuntimeAgentAdapter._search_notes, RuntimeAgentAdapter._read_verified_note_for_tool
    execute = RetrievalAgent.execute

    def counted_search(self, query, source_context, limit):
        invocations.append(("search_notes", {"query": query, "source_context": source_context, "limit": limit}))
        return search(self, query, source_context, limit)

    def counted_read(self, ref):
        invocations.append(("read_verified_note", {"note_ref": ref}))
        return read(self, ref)

    def capture(self, *args, **kwargs):
        result = execute(self, *args, **kwargs)
        projections.append(result)
        return result

    monkeypatch.setattr(RuntimeAgentAdapter, "_search_notes", counted_search)
    monkeypatch.setattr(RuntimeAgentAdapter, "_read_verified_note_for_tool", counted_read)
    monkeypatch.setattr(RetrievalAgent, "execute", capture)
    return root, vault, index, invocations, projections


def engine(env, provider, boundary=None):
    root, vault, index, _, _ = env
    return RuntimeEngine(
        vault_root=vault, index_path=index, checkpoint_dir=root / "checkpoints",
        trace_dir=root / "traces", model=provider,
        checkpointer=CrashCheckpointer(root / "checkpoints", boundary) if boundary else None,
    )


def start(runtime, **kwargs):
    max_steps = kwargs.pop("max_steps", 12)
    max_provider_requests = kwargs.pop("max_provider_requests", 8)
    return runtime.start_multi_agent(RunRequest(
        request_id="m04-request", thread_id="m04-thread", workflow="ask",
        query="shared", max_steps=max_steps,
        max_provider_requests=max_provider_requests, **kwargs,
    ))


def facts(env, runtime):
    state = runtime.checkpointer.get_latest("m04-thread")
    result = json.loads((env[0] / "checkpoints" / state.result_ref).read_text(encoding="utf-8")) if state.result_ref else None
    return state, result


def assert_consistent(env, runtime, expected_tools):
    state, result = facts(env, runtime)
    assert state.status == result["status"] == "completed"
    assert state.termination.status == "completed"
    assert result["tool_ledger"] == [r.to_dict() for r in state.tool_ledger]
    assert [r.tool_id for r in state.tool_ledger] == expected_tools
    assert state.evidence_refs == result["evidence"] == env[4][-1].output_refs
    task_id = result["agent_tasks"][0]["task_id"]
    assert env[4][-1].task_id == task_id
    assert all((r.run_id, r.task_id, r.agent_id) == (state.run_id, task_id, "retrieval_agent")
               for r in [*state.model_executions, *state.tool_ledger])
    assert len({r.call_id for r in state.tool_ledger}) == len(state.tool_ledger)
    artifacts = ModelArtifactStore(env[0] / "checkpoints" / "models")
    for record in state.model_executions:
        identity = {key: getattr(record, key) for key in ("run_id", "turn_id", "task_id", "agent_id", "sequence")}
        for kind in ("request", "response", "observation"):
            ref, sha = getattr(record, f"{kind}_ref"), getattr(record, f"{kind}_sha256")
            if ref and sha:
                assert artifacts.read(ref, expected_sha256=sha)["runtime_identity"] == identity
    events = TraceReader(env[0] / "traces").read_events(state.run_id)
    assert [e.seq for e in events] == sorted({e.seq for e in events})
    assert not any(e.event_type.startswith("handoff.") for e in events)
    for record in state.tool_ledger:
        types = Counter(e.event_type for e in events if e.attributes.get("call_id") == record.call_id)
        assert types["tool.called"] == 1
        assert types["tool.completed"] + types["tool.failed"] == 1
    assert env[3] == [(r.tool_id, r.arguments) for r in state.tool_ledger]
    return state, result


@pytest.mark.parametrize("read", [False, True])
def test_production_model_selected_trajectory(environment, read):
    provider = PolicyProvider(read=read)
    runtime = engine(environment, provider)
    assert start(runtime).status == "completed"
    state, result = assert_consistent(environment, runtime, ["search_notes"] + (["read_verified_note"] if read else []))
    assert len(provider.requests) == 2 + int(read)
    for request, record in zip(provider.requests[1:], state.tool_ledger):
        assert request.observation.to_dict() == record.result
        assert request.previous_tool_call.call_id == record.call_id
    assert not result["fallback_used"]
    assert result["review"]["decision"] == "evidence_sufficient"


def test_production_empty_search(environment):
    runtime = engine(environment, PolicyProvider(query="zzzz-no-match"))
    assert start(runtime).status == "completed"
    state, result = assert_consistent(environment, runtime, ["search_notes"])
    assert state.tool_ledger[0].status == "completed"
    assert environment[4][-1].status == "completed"
    assert not result["evidence"] and not result["fallback_used"]
    assert "NO_VERIFIED_EVIDENCE" not in json.dumps(result)


@pytest.mark.parametrize("tool", ["search_notes", "read_verified_note"])
def test_production_tool_failure(environment, monkeypatch, tool):
    def fail(self, *args, **kwargs):
        raise ValueError("Synthetic internal failure at /private/fixture.md")
    monkeypatch.setattr(RuntimeAgentAdapter, "_search_notes" if tool == "search_notes" else "_read_verified_note_for_tool", fail)
    provider = PolicyProvider(read=True)
    runtime = engine(environment, provider)
    start(runtime)
    state, result = facts(environment, runtime)
    failed = next(r for r in state.tool_ledger if r.tool_id == tool)
    assert failed.status == "failed" and failed.error["code"] == "TOOL_EXECUTION_FAILED"
    assert environment[4][-1].status == "failed"
    assert result["fallback_used"] and result["agent_tasks"][0]["status"] == "failed"
    assert "/private" not in json.dumps(result)
    assert provider.requests[-1].observation.error.code == "TOOL_EXECUTION_FAILED"


def test_production_provider_failure(environment):
    provider = PolicyProvider(failure=True)
    runtime = engine(environment, provider)
    start(runtime)
    state, result = facts(environment, runtime)
    assert len(provider.requests) == 1 and not environment[3] and not state.tool_ledger
    assert state.model_executions[-1].provider_error["code"] == "MODEL_UNAVAILABLE"
    assert environment[4][-1].status == "failed" and result["fallback_used"]


@pytest.mark.parametrize("continue_read", [False, True])
def test_terminal_result_resume_preserves_identity_and_never_replays(environment, continue_read):
    before = PolicyProvider()
    first = engine(environment, before, "tool_result_durable")
    with pytest.raises(ProcessCrash):
        start(first)
    durable, _ = facts(environment, first)
    assert len(before.requests) == len(environment[3]) == 1
    old = durable.tool_ledger[0]
    after = PolicyProvider(read=continue_read)
    restarted = engine(environment, after)
    assert restarted.checkpointer is not first.checkpointer
    assert restarted.resume_multi_agent("m04-thread").status == "completed"
    state, result = assert_consistent(environment, restarted, ["search_notes"] + (["read_verified_note"] if continue_read else []))
    assert (state.run_id, state.thread_id) == (durable.run_id, durable.thread_id)
    assert state.tool_ledger[0].to_dict() == old.to_dict()
    assert after.requests[0].sequence == before.requests[0].sequence + 1
    assert after.requests[0].previous_tool_call.call_id == old.call_id
    assert after.requests[0].observation.to_dict() == old.result
    assert {r.turn_id for r in before.requests}.isdisjoint(r.turn_id for r in after.requests)
    assert len(after.requests) == 1 + int(continue_read)
    assert sum(tool == "search_notes" for tool, _ in environment[3]) == 1
    assert result["review"]["decision"] == "evidence_sufficient"
    evidence = {
        "scenario": "terminal_result_then_read_final" if continue_read else "terminal_result_then_final",
        "run_id": state.run_id, "thread_id": state.thread_id,
        "task_id": old.task_id, "agent_id": old.agent_id, "reused_call_id": old.call_id,
        "model_turns_before": [r.turn_id for r in before.requests],
        "model_turns_after": [r.turn_id for r in after.requests],
        "executor_calls_before": 1,
        "executor_calls_after": len(environment[3]) - 1,
        "duplicate_model_calls": 0, "duplicate_executor_calls": 0,
        "result_ref": state.result_ref,
        "model_statuses": [r.status for r in state.model_executions],
        "ledger_statuses": [r.status for r in state.tool_ledger],
        "retrieval_result": environment[4][-1].to_dict(),
    }
    evidence_path = environment[0] / "resume_evidence.json"
    evidence_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"M0.4 resume evidence: {evidence_path}")


def test_ambiguous_request_sent_does_not_replay_or_rewrite(environment):
    before = PolicyProvider(crash=True)
    first = engine(environment, before)
    with pytest.raises(ProcessCrash):
        start(first)
    durable, _ = facts(environment, first)
    assert len(before.requests) == 1
    assert durable.model_executions[-1].status == "request_sent"
    after = PolicyProvider()
    restarted = engine(environment, after)
    history = restarted.checkpointer.list_checkpoints("m04-thread")
    status = restarted.resume_multi_agent("m04-thread")
    assert status.error["code"] == "MODEL_RESUME_REQUIRES_VERIFICATION"
    assert not after.requests and not environment[3] and not environment[4]
    assert restarted.checkpointer.get_latest("m04-thread").to_dict() == durable.to_dict()
    assert restarted.checkpointer.list_checkpoints("m04-thread") == history


def test_durable_response_resume_executes_only_unstarted_tool(environment):
    before = PolicyProvider()
    first = engine(environment, before, "response_durable")
    with pytest.raises(ProcessCrash):
        start(first)
    state, _ = facts(environment, first)
    assert len(before.requests) == 1 and not environment[3] and not state.tool_ledger
    after = PolicyProvider()
    restarted = engine(environment, after)
    assert restarted.resume_multi_agent("m04-thread").status == "completed"
    assert_consistent(environment, restarted, ["search_notes"])
    assert len(after.requests) == len(environment[3]) == 1
    assert after.requests[0].sequence == 2


def test_durable_final_resume_reuses_final_with_zero_provider_budget(environment):
    before = PolicyProvider()
    first = engine(environment, before, "response_durable_final")
    with pytest.raises(ProcessCrash):
        start(first, max_provider_requests=2)
    durable, _ = facts(environment, first)
    assert len(before.requests) == 2
    assert durable.model_executions[-1].status == "response_durable"
    assert durable.model_executions[-1].normalized_action["kind"] == "final"

    after = PolicyProvider()
    restarted = engine(environment, after)
    assert restarted.resume_multi_agent("m04-thread").status == "completed"
    state, result = assert_consistent(environment, restarted, ["search_notes"])
    assert not after.requests
    assert len(environment[3]) == 1
    assert state.run_id == durable.run_id
    assert len(state.model_executions) == len(durable.model_executions)
    assert state.model_executions[-1].turn_id == durable.model_executions[-1].turn_id
    assert result["fallback_used"] is False
    assert environment[4][-1].status == "completed"
    assert environment[4][-1].error is None


def test_durable_final_resume_reuses_final_with_zero_step_budget(environment):
    before = PolicyProvider(read=True)
    first = engine(environment, before, "response_durable_final")
    with pytest.raises(ProcessCrash):
        start(first, max_steps=3, max_provider_requests=8)
    durable, _ = facts(environment, first)
    assert len(before.requests) == 3
    assert durable.model_executions[-1].status == "response_durable"
    assert durable.model_executions[-1].normalized_action["kind"] == "final"

    after = PolicyProvider(read=True)
    restarted = engine(environment, after)
    assert restarted.resume_multi_agent("m04-thread").status == "completed"
    state, result = assert_consistent(
        environment, restarted, ["search_notes", "read_verified_note"]
    )
    assert not after.requests
    assert len(environment[3]) == 2
    assert state.run_id == durable.run_id
    assert len(state.model_executions) == len(durable.model_executions)
    assert state.model_executions[-1].turn_id == durable.model_executions[-1].turn_id
    assert result["fallback_used"] is False
    assert environment[4][-1].status == "completed"
    assert environment[4][-1].error is None


def test_resume_needing_next_turn_rejects_zero_provider_budget(environment):
    before = PolicyProvider()
    first = engine(environment, before, "tool_result_durable")
    with pytest.raises(ProcessCrash):
        start(first, max_provider_requests=1)
    assert len(before.requests) == len(environment[3]) == 1

    after = PolicyProvider()
    restarted = engine(environment, after)
    restarted.resume_multi_agent("m04-thread")
    state, result = facts(environment, restarted)
    assert not after.requests
    assert len(environment[3]) == 1
    assert environment[4][-1].error["code"] == "MODEL_PROVIDER_BUDGET_EXHAUSTED"
    assert result["fallback_used"] is True
    assert state.tool_ledger[0].status == "completed"


def test_resume_needing_next_turn_rejects_zero_step_budget(environment):
    before = PolicyProvider()
    first = engine(environment, before, "tool_result_durable")
    with pytest.raises(ProcessCrash):
        start(first, max_steps=1, max_provider_requests=8)
    assert len(before.requests) == len(environment[3]) == 1

    after = PolicyProvider()
    restarted = engine(environment, after)
    restarted.resume_multi_agent("m04-thread")
    state, result = facts(environment, restarted)
    assert not after.requests
    assert len(environment[3]) == 1
    assert environment[4][-1].error["code"] == "MODEL_MAX_STEPS_EXCEEDED"
    assert result["fallback_used"] is True
    assert state.tool_ledger[0].status == "completed"


def test_resume_rejects_terminal_run_without_calls(environment):
    runtime = engine(environment, PolicyProvider())
    start(runtime)
    after = PolicyProvider()
    restarted = engine(environment, after)
    with pytest.raises(RuntimeModelError):
        restarted.resume_multi_agent("m04-thread")
    assert not after.requests


def test_restart_does_not_reset_retrieval_step_allowance(environment):
    class SearchingProvider(PolicyProvider):
        def complete(self, request):
            self.requests.append(request)
            return ModelResponse(action=ModelAction.tool(ToolCall(
                call_id=f"again:{request.sequence}", tool_id="search_notes",
                arguments={"query": "shared", "source_context": {}, "limit": 2},
                sequence=request.sequence,
            )))

    before = SearchingProvider()
    first = engine(environment, before, "tool_result_durable")
    with pytest.raises(ProcessCrash):
        start(first)
    after = SearchingProvider()
    restarted = engine(environment, after)
    restarted.resume_multi_agent("m04-thread")
    state, result = facts(environment, restarted)
    assert len(before.requests) + len(after.requests) == 3
    assert len(state.model_executions) == 3
    assert environment[4][-1].status == "failed"
    assert environment[4][-1].error["code"] == "MODEL_MAX_STEPS_EXCEEDED"
    assert result["fallback_used"]


@pytest.mark.parametrize("change", ["index", "note", "task", "request_path", "query"])
def test_resume_preflight_rejects_changed_source_or_identity(environment, change):
    first = engine(environment, PolicyProvider(), "tool_result_durable")
    with pytest.raises(ProcessCrash):
        start(first)
    state, _ = facts(environment, first)
    root, vault, index, _, _ = environment
    if change == "index":
        index.write_text(index.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    elif change == "note":
        (vault / "alpha.md").write_text("changed source", encoding="utf-8")
    elif change == "query":
        path = root / "checkpoints" / state.request_ref
        data = json.loads(path.read_text(encoding="utf-8"))
        data["query"] = "substituted caller query"
        path.write_text(json.dumps(data), encoding="utf-8")
    else:
        data = state.to_dict()
        data["step_seq"] += 1
        if change == "task":
            data["model_executions"][0]["task_id"] = "other_task"
        else:
            data["request_ref"] = "../outside.json"
        first.checkpointer.boundary = None
        first.checkpointer.save(type(state).from_dict(data))
    after = PolicyProvider()
    restarted = engine(environment, after)
    original_count = len(environment[3])
    try:
        status = restarted.resume_multi_agent("m04-thread")
    except (RuntimeModelError, ValueError):
        pass
    else:
        assert status.status == "failed"
    assert not after.requests and len(environment[3]) == original_count
