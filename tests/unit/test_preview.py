import pytest
from pathlib import Path
from linkloom.mutations.preview import generate_preview
from linkloom.mutations.operations import build_append_plan
from unittest.mock import patch

def test_preview_stale_hash(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("initial")
    
    with pytest.raises(ValueError, match="Stale expected hash"):
        generate_preview(tmp_path, "note.md", "bad_hash", "append")

def test_preview_exact_diff(tmp_path):
    target = tmp_path / "note.md"
    target.write_text("line1\n")
    
    diff, exp_hash, exp_size, prop_hash = generate_preview(tmp_path, "note.md", None, "\nline2\n")
    diff_str = diff.replace('\r\n', '\n')
    assert "+ \n+ line2\n" in diff_str or "+\n+line2\n" in diff_str
    # Ensure no mutation
    assert target.read_text() == "line1\n"
    
def test_preview_missing_target(tmp_path):
    with pytest.raises(FileNotFoundError):
        generate_preview(tmp_path, "missing.md", None, "append")
