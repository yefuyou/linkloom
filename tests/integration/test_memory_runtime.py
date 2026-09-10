import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction, ModelTurnRequest
from linkloom.memory.models import MemoryScope
from linkloom.memory.store import MemoryStore
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall


@pytest.fixture
def m03_root(request: pytest.FixtureRequest) -> Path:
    root = (
        Path(__file__).resolve().parents[2]
        / ".tmp"
        / f"m03-memory-{request.node.name}-{uuid4().hex[:8]}"
    )
    root.mkdir(parents=True, exist_ok=False)
    return root


def _search_then_final(query: str = "test query") -> FakeModelAdapter:
    def search(request: ModelTurnRequest) -> ModelAction:
        return ModelAction.tool(
            ToolCall(
                call_id="memory_model_search",
                tool_id="search_notes",
                arguments={"query": query, "source_context": {}, "limit": 10},
            )
        )

    def final(request: ModelTurnRequest) -> ModelAction:
        assert request.observation is not None
        return ModelAction.final("memory test final")

    return FakeModelAdapter([search, final])


def test_memory_root_inside_vault_rejected(m03_root: Path):
    vault = m03_root / "vault"
    vault.mkdir()
    ckpt = m03_root / "ckpt"
    ckpt.mkdir()

    with pytest.raises(Exception) as exc:
        RuntimeEngine(
            vault_root=vault,
            index_path=vault / "index.json",
            checkpoint_dir=ckpt,
            memory_root=vault / "memory",
        )
    assert exc.value.details["code"] == "MEMORY_INSIDE_VAULT"


def test_runtime_multi_agent_memory_injection(m03_root: Path):
    vault = m03_root / "vault"
    vault.mkdir()
    (vault / "doc.md").write_text("hello", encoding="utf-8")
    doc_sha = hashlib.sha256(b"hello").hexdigest()
    idx = vault / "index.json"
    idx.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "notes": [
                    {
                        "relative_path": "doc.md",
                        "content_sha256": doc_sha,
                        "content": "hello",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    ckpt = m03_root / "ckpt"
    ckpt.mkdir()
    trace_dir = m03_root / "traces"
    trace_dir.mkdir()
    mem_dir = m03_root / "mem"
    mem_dir.mkdir()

    store = MemoryStore(str(mem_dir / "memory.jsonl"))
    candidate = store.create_candidate(
        MemoryScope.THREAD,
        "test_key",
        {"internal_data": "do_not_leak_this_sentinel"},
        ["doc.md"],
    )
    store.confirm_candidate(candidate.id, "human")

    model = _search_then_final()
    engine = RuntimeEngine(
        vault_root=vault,
        index_path=idx,
        checkpoint_dir=ckpt,
        trace_dir=trace_dir,
        memory_root=mem_dir,
        model=model,
    )

    req = RunRequest(
        request_id="1",
        workflow="ask",
        query="test query",
        thread_id="t1",
        max_provider_requests=8,
    )
    status = engine.start_multi_agent(req)
    assert status.status == "completed"

    # Check leak in checkpoint DB and files.
    for file_path in ckpt.rglob("*"):
        if file_path.is_file():
            if file_path.name == "memory.jsonl":
                continue
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            assert "do_not_leak_this_sentinel" not in content, f"Leaked in {file_path}"

    # Check leak in traces and verify only the memory reference projection is emitted.
    injected_found = False
    for file_path in trace_dir.rglob("*.jsonl"):
        lines = file_path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            assert "do_not_leak_this_sentinel" not in line
            event = json.loads(line)
            if event["event_type"] == "memory.injected":
                injected_found = True
                refs = event["attributes"]["refs"]
                assert len(refs) == 1
                assert refs[0]["memory_id"]
                assert "value_sha256" in refs[0]
                assert "value" not in refs[0]

    assert injected_found

    latest_state = engine.checkpointer.get_latest("t1")
    assert latest_state is not None
    assert len(latest_state.memory_refs) == 1
    assert latest_state.memory_refs[0]["key"] == "test_key"

    state_dict = latest_state.to_dict()
    assert len(state_dict["memory_refs"]) == 1
    restored = type(latest_state).from_dict(state_dict)
    assert len(restored.memory_refs) == 1
