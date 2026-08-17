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
from linkloom.runtime.checkpoint import SQLiteCheckpointer, InMemoryCheckpointer
from linkloom.runtime.errors import (
    CheckpointWriteError,
    InterruptResponseInvalidError,
    StateTransitionError,
    ThreadBusyError,
    ValidationError,
)

# Dual-Track Explanation:
# 大白话 (Plain-Language Analogy):
# 故障注入测试就像是汽车的安全气囊和防抱死（ABS）系统测试。
# 我们模拟突然爆胎（文件哈希变化）、底盘打滑（数据库写失败）和猛踩刹车（非法回应），
# 验证系统是否能在千钧一发之际精准熄火（转换为 stale/failed 状态）而不是假装没事继续狂奔（伪造 completed 状态）。
#
# 专业学术概念 (Industry Technical Definition):
# This suite establishes verification assertions under controlled execution failure modes.
# It validates transaction rollbacks when checkpointer writes fail, ensures deterministic StaleSourceError propagation,
# and enforces strict schema alignment policies preventing invalid transition paths or double-resumptions.


@pytest.fixture
def test_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Provides a fresh copy of the sample vault and its scan index."""
    vault_root = tmp_path / "sample_vault"
    shutil.copytree(FIXTURE_ROOT, vault_root)
    scan_dir = tmp_path / "scan"
    scan_res = scan_vault(vault_root, scan_dir)
    return vault_root, scan_res.index_path


def test_failure_injection_source_stale(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    # 1. Run until pause
    req = RunRequest(
        request_id="req_stale",
        workflow="ask",
        query="Scanner",
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

    # 2. Modify index file content to simulate a stale/modified vault index
    index_path.write_text("{}", encoding="utf-8")

    # 3. Try to resume - should fail and transition to 'stale' status
    status_res = engine.resume(
        thread_id=status.thread_id,
        interrupt_id=status.interrupt["interrupt_id"],
        response="resume",
    )
    assert status_res.status == "stale"
    assert status_res.error is not None
    assert status_res.error["code"] == "CONTENT_CHANGED"


def test_failure_injection_fail_once_and_retry(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    req = RunRequest(
        request_id="req_fail_retry",
        workflow="ask",
        query="Scanner",
        index_path=None,
        dry_run=True,
    )
    
    # Injected failure at retrieve_context
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir,
        fail_at="retrieve_context",
        fail_once=True,
    )
    
    # 1. First execution should fail
    status = engine.start(req)
    assert status.status == "failed"
    assert status.error["code"] == "INJECTED_FAILURE"

    # 2. Retry execution should succeed (as fail_once is True and we already failed once)
    status_retry = engine.retry(run_id=status.run_id)
    assert status_retry.status == "completed"
    assert status_retry.result_ref is not None
    assert (checkpoint_dir / status_retry.result_ref).exists()
    assert status_retry.run_id != status.run_id


class FailingCheckpointer(InMemoryCheckpointer):
    """A checkpointer that deliberately throws writing errors when completing runs."""
    def save(self, state: RuntimeState, checkpoint_id: str | None = None) -> str:
        if state.status == "completed":
            raise CheckpointWriteError("Simulated checkpoint write failure.")
        return super().save(state, checkpoint_id)


def test_failure_injection_checkpoint_write_failure(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    req = RunRequest(
        request_id="req_cp_fail",
        workflow="ask",
        query="Scanner",
        index_path=None,
        dry_run=True,
    )
    
    failing_cp = FailingCheckpointer()
    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=checkpoint_dir,
        checkpointer=failing_cp,
    )

    # Start should run, execute service, but fail to persist completion, wrapping error to "failed" status
    status = engine.start(req)
    assert status.status == "failed"
    assert status.error is not None
    assert status.error["code"] == "CHECKPOINT_WRITE_FAILED"
    # Result ref must not be completed
    assert status.result_ref is None


def test_failure_injection_invalid_response(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    req = RunRequest(
        request_id="req_invalid",
        workflow="ask",
        query="Scanner",
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

    # 1. Invalid response should raise error
    with pytest.raises(InterruptResponseInvalidError):
        engine.resume(status.thread_id, status.interrupt["interrupt_id"], "invalid_payload")

    # Status remains paused
    status_inspect = engine.inspect(status.thread_id)
    assert status_inspect.status == "paused"

    # 2. Reject response transitions to rejected
    status_rej = engine.resume(status.thread_id, status.interrupt["interrupt_id"], "reject")
    assert status_rej.status == "rejected"


def test_failure_injection_double_resume_prevention(tmp_path: Path, test_vault: tuple[Path, Path]) -> None:
    vault_root, index_path = test_vault
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()

    req = RunRequest(
        request_id="req_double",
        workflow="ask",
        query="Scanner",
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

    # Resume once
    status_comp = engine.resume(status.thread_id, status.interrupt["interrupt_id"], "resume")
    assert status_comp.status == "completed"

    # Resume second time on same thread/interrupt should be rejected because state is completed, not paused
    with pytest.raises(StateTransitionError):
        engine.resume(status.thread_id, status.interrupt["interrupt_id"], "resume")


def test_runtime_enforces_checkpoint_boundary_and_step_budget(
    tmp_path: Path, test_vault: tuple[Path, Path]
) -> None:
    vault_root, index_path = test_vault
    with pytest.raises(ValidationError):
        RuntimeEngine(
            vault_root=vault_root,
            index_path=index_path,
            checkpoint_dir=vault_root / ".artifacts" / "runtime",
        )

    engine = RuntimeEngine(
        vault_root=vault_root,
        index_path=index_path,
        checkpoint_dir=tmp_path / "budget",
    )
    status = engine.start(
        RunRequest(
            request_id="req_budget",
            workflow="ask",
            query="Scanner",
            max_steps=1,
        )
    )
    assert status.status == "failed"
    assert status.error is not None
    assert status.error["code"] == "BUDGET_EXCEEDED"
