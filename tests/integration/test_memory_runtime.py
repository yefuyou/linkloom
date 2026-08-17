import json
import os
import tempfile
from pathlib import Path

import pytest
from linkloom.memory.models import MemoryScope
from linkloom.memory.store import MemoryStore
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest, RuntimeState

def test_memory_root_inside_vault_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        ckpt = Path(tmp) / "ckpt"
        ckpt.mkdir()
        
        with pytest.raises(Exception) as exc:
            RuntimeEngine(
                vault_root=vault,
                index_path=vault / "index.json",
                checkpoint_dir=ckpt,
                memory_root=vault / "memory"
            )
        assert exc.value.details["code"] == "MEMORY_INSIDE_VAULT"

def test_runtime_multi_agent_memory_injection():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("hello", encoding="utf-8")
        import hashlib
        doc_sha = hashlib.sha256(b"hello").hexdigest()
        idx = vault / "index.json"
        idx.write_text(json.dumps({
            "schema_version": 1,
            "notes": [
                {"relative_path": "doc.md", "content_sha256": doc_sha, "content": "hello"}
            ]
        }))
        
        ckpt = Path(tmp) / "ckpt"
        ckpt.mkdir()
        trace_dir = Path(tmp) / "traces"
        trace_dir.mkdir()
        mem_dir = Path(tmp) / "mem"
        mem_dir.mkdir()

        store = MemoryStore(str(mem_dir / "memory.jsonl"))
        candidate = store.create_candidate(
            MemoryScope.THREAD, "test_key", {"internal_data": "do_not_leak_this_sentinel"}, ["doc.md"]
        )
        store.confirm_candidate(candidate.id, "human")

        engine = RuntimeEngine(
            vault_root=vault,
            index_path=idx,
            checkpoint_dir=ckpt,
            trace_dir=trace_dir,
            memory_root=mem_dir
        )

        req = RunRequest(request_id="1", workflow="ask", query="test query", thread_id="t1")
        status = engine.start_multi_agent(req)
        
        if status.status == "failed":
            print(f"FAILED RUN ERROR: {status.error}")

        # Check leak in checkpoint DB and files
        for f in ckpt.rglob("*"):
            if f.is_file():
                if f.name == "memory.jsonl":
                    continue
                content = f.read_text(encoding="utf-8", errors="ignore")
                assert "do_not_leak_this_sentinel" not in content, f"Leaked in {f}"
        
        # Check leak in traces
        injected_found = False
        for f in trace_dir.rglob("*.jsonl"):
            lines = f.read_text(encoding="utf-8").splitlines()
            for line in lines:
                if not line.strip(): continue
                assert "do_not_leak_this_sentinel" not in line
                event = json.loads(line)
                if event["event_type"] == "memory.injected":
                    injected_found = True
                    refs = event["attributes"]["refs"]
                    assert len(refs) == 1
                    assert refs[0]["memory_id"]
                    assert "value_sha256" in refs[0]
                    assert "value" not in refs[0]
        
        if not injected_found:
            for f in trace_dir.rglob("*.jsonl"):
                print("TRACE FILE:", f.read_text(encoding="utf-8"))
            
        assert injected_found

        # state roundtrip test
        latest_state = engine.checkpointer.get_latest("t1")
        assert len(latest_state.memory_refs) == 1
        assert latest_state.memory_refs[0]["key"] == "test_key"
        
        # from_dict / to_dict roundtrip
        state_dict = latest_state.to_dict()
        assert len(state_dict["memory_refs"]) == 1
        restored = RuntimeState.from_dict(state_dict)
        assert len(restored.memory_refs) == 1
