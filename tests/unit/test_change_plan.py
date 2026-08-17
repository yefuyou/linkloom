import pytest
from pathlib import Path
from linkloom.mutations.models import ChangePlan, ChangeOperation, Precondition
from linkloom.mutations.operations import build_append_plan
from datetime import datetime

def test_change_plan_digest_stability():
    op1 = ChangeOperation(
        operation_id="op1", sequence=1, kind="append_block",
        target_relative_path="note.md", expected_sha256="abc",
        expected_size_bytes=10, proposed_content_sha256="def",
        precondition=Precondition(must_exist=True, must_not_be_symlink=True, allowed_root="f1"),
        payload={"text": "hello"}, preview_diff="+ hello", evidence_refs=["#1"],
        risk="low", status="pending"
    )
    digest1 = ChangePlan.compute_digest("plan1", [op1])
    digest2 = ChangePlan.compute_digest("plan1", [op1])
    assert digest1 == digest2

    op2 = ChangeOperation(
        operation_id="op1", sequence=1, kind="append_block",
        target_relative_path="note.md", expected_sha256="abc",
        expected_size_bytes=10, proposed_content_sha256="deg", # changed
        precondition=Precondition(must_exist=True, must_not_be_symlink=True, allowed_root="f1"),
        payload={"text": "hello"}, preview_diff="+ hello", evidence_refs=["#1"],
        risk="low", status="pending"
    )
    digest3 = ChangePlan.compute_digest("plan1", [op2])
    assert digest1 != digest3

def test_build_plan_unsupported_kind(tmp_path):
    root = tmp_path
    with pytest.raises(ValueError, match="OPERATION_NOT_SUPPORTED"):
        build_append_plan("run1", root, "f1", "rename", "note.md", "new_name", ["#1"], "idx", "low")

def test_build_plan_unsupported_operation_returns_status():
    from linkloom.mutations.operations import create_operation
    op = create_operation("op1", 1, "rename", "note.md", "abc", 10, "def", "f1", {"text": "new_name"}, "+ new_name", ["#1"], "low")
    assert op.status == "OPERATION_NOT_SUPPORTED"

def test_build_plan_absolute_path_rejected(tmp_path):
    root = tmp_path
    import os
    abs_path = "C:/etc/passwd" if os.name == "nt" else "/etc/passwd"
    from linkloom.mutations.models import PermissionError
    with pytest.raises(PermissionError, match="ABSOLUTE_PATH_NOT_ALLOWED"):
        build_append_plan("run1", root, "f1", "append_block", abs_path, "hello", ["#1"], "idx", "low")

def test_build_plan_traversal_rejected(tmp_path):
    root = tmp_path
    from linkloom.mutations.models import PermissionError
    with pytest.raises(PermissionError, match="PATH_TRAVERSAL_NOT_ALLOWED"):
        build_append_plan("run1", root, "f1", "append_block", "../passwd", "hello", ["#1"], "idx", "low")

def test_build_plan_success(tmp_path):
    root = tmp_path
    target = root / "note.md"
    target.write_text("initial")
    
    plan = build_append_plan(
        run_id="run1",
        root=root,
        fingerprint="f1",
        kind="append_block",
        target_relative_path="note.md",
        append_text="new_content",
        evidence_refs=["#1"],
        source_index_hash="idx",
        risk="low"
    )
    
    assert isinstance(plan, ChangePlan)
    assert plan.plan_id == "plan_run1"
    assert len(plan.operations) == 1
    assert plan.operation_count == 1
    assert plan.target_root_mode == "synthetic_fixture_only"
    assert plan.diff_summary == {"files_changed": 1, "lines_added": 0, "lines_removed": 1}
    assert plan.approval_status == "pending"

def test_duplicate_operations_rejected():
    op1 = ChangeOperation("op1", 1, "append_block", "a", "s1", 1, "s2", Precondition(True, True, "f1"), {}, "d", ["#1"], "low", "pending")
    with pytest.raises(ValueError, match="Duplicate operation id"):
        ChangePlan("p1", "sha", "r1", "idx", "f1", [op1, op1], 2, {}, datetime.now(), datetime.now())

def test_empty_evidence_refs_rejected(tmp_path):
    with pytest.raises(ValueError, match="Evidence refs cannot be empty"):
        build_append_plan("run1", tmp_path, "f1", "append_block", "note.md", "hello", [], "idx", "low")
