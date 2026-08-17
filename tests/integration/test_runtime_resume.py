from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from linkloom.scanner import scan_vault
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest, RunStatus, RuntimeState
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.runtime.errors import ThreadNotFoundError, StateTransitionError, InterruptResponseInvalidError

# Dual-Track Explanation:
# 大白话 (Plain-Language Analogy):
# 这个测试就像是模拟“游戏打到一半断电后读档”。
# 我们先保存游戏的当前场景进度，断电（关闭实例）后再重启，通过读档证明我们重新连接游戏后通关的结局（最终答案），
# 与一口气打通关的游戏结局是完全一模一样的，没有任何的装备丢失（数据等价性）。
#
# 专业学术概念 (Industry Technical Definition):
# This suite implements black-box integration assertions validating state persistence convergence.
# It replicates cold-start process resumption by re-instantiating the RuntimeEngine using durable SQLite transactions,
# asserting that the pipeline reconstructs structurally identical output payloads and execution metadata.


def fixture_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


@pytest.fixture
def test_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Provides a fresh copy of the sample vault and its scan index."""
    vault_root = tmp_path / "sample_vault"
    shutil.copytree(FIXTURE_ROOT, vault_root)
    scan_dir = tmp_path / "scan"
    scan_res = scan_vault(vault_root, scan_dir)
    return vault_root, scan_res.index_path


def test_runtime_ask_resume_equivalence(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # 1. Start query normally to get a non-paused baseline
    req_baseline = RunRequest(
        request_id="req_base_001",
        workflow="ask",
        query="Scanner",
        vault_root=str(vault_root),
        index_path=None,
        dry_run=True,
    )
    engine_base = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir / "base",
    )
    status_base = engine_base.start(req_baseline)
    assert status_base.status == "completed"
    
    result_base_file = checkpoint_dir / "base" / status_base.result_ref
    assert result_base_file.exists()
    result_base_data = json.loads(result_base_file.read_text(encoding="utf-8"))

    # 2. Run with pause policy at retrieve_context
    req_paused = RunRequest(
        request_id="req_pause_001",
        workflow="ask",
        query="Scanner",
        vault_root=str(vault_root),
        index_path=None,
        dry_run=True,
    )
    engine_paused = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir / "paused",
        pause_after="retrieve_context",
    )

    before_snapshot = fixture_snapshot(vault_root)

    status_paused = engine_paused.start(req_paused)
    assert status_paused.status == "paused"
    assert status_paused.current_step == "emit_result"
    assert status_paused.interrupt is not None
    assert status_paused.checkpoint_id is not None
    paused_state = engine_paused.checkpointer.get_latest(status_paused.thread_id)
    assert paused_state is not None
    assert all(isinstance(ref, str) for ref in paused_state.evidence_refs)
    assert all("quote" not in ref for ref in paused_state.evidence_refs)
    assert status_paused.interrupt["checkpoint_id"] in {
        item["checkpoint_id"]
        for item in engine_paused.checkpointer.list_checkpoints(status_paused.thread_id)
    }
    
    thread_id = status_paused.thread_id
    interrupt_id = status_paused.interrupt["interrupt_id"]

    # 3. Simulate process shutdown by creating a completely new engine instance
    engine_resume = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir / "paused",
    )
    
    # Verify inspect API
    status_inspect = engine_resume.inspect(thread_id)
    assert status_inspect.status == "paused"
    assert status_inspect.current_step == "emit_result"

    # Resume the thread
    status_completed = engine_resume.resume(
        thread_id=thread_id,
        interrupt_id=interrupt_id,
        response="resume",
    )
    
    assert status_completed.status == "completed"

    result_res_file = checkpoint_dir / "paused" / status_completed.result_ref
    assert result_res_file.exists()
    result_res_data = json.loads(result_res_file.read_text(encoding="utf-8"))

    # Business-field comparison equivalence
    assert result_base_data["status"] == result_res_data["status"]
    assert result_base_data["query"] == result_res_data["query"]
    assert len(result_base_data["evidence"]) == len(result_res_data["evidence"])
    assert result_base_data["answer"]["text"] == result_res_data["answer"]["text"]

    # Ensure no vault write back occurred
    after_snapshot = fixture_snapshot(vault_root)
    assert before_snapshot == after_snapshot


def test_runtime_connect_resume_equivalence(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # Pause-resume pipeline for connect workflow
    req = RunRequest(
        request_id="req_connect_001",
        workflow="connect",
        query="Scanner",
        vault_root=str(vault_root),
        index_path=None,
        dry_run=True,
    )
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir,
        pause_after="retrieve_context",
    )
    status = engine.start(req)
    assert status.status == "paused"

    status_completed = engine.resume(
        thread_id=status.thread_id,
        interrupt_id=status.interrupt["interrupt_id"],
        response="resume",
    )
    assert status_completed.status == "completed"
    assert status_completed.result_ref is not None
    assert (checkpoint_dir / status_completed.result_ref).exists()


def test_thread_isolation(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # One engine with shared checkpointer (e.g. SQLite database)
    checkpointer = SQLiteCheckpointer(checkpoint_dir)
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir,
        checkpointer=checkpointer,
        pause_after="retrieve_context",
    )

    req1 = RunRequest(
        request_id="req_t1",
        workflow="ask",
        query="Scanner",
        thread_id="thread_t1",
    )
    status1 = engine.start(req1)
    
    # Try resume another thread that does not exist
    with pytest.raises(ThreadNotFoundError):
        engine.resume("thread_t2", status1.interrupt["interrupt_id"], "resume")
