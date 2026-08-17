from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"
FIXTURE_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "sample_vault"
sys.path.insert(0, str(SRC_ROOT))

from linkloom.loader import LoaderError  # noqa: E402
from linkloom.scanner import scan_vault  # noqa: E402
from linkloom.services.ask import AskService  # noqa: E402
from linkloom.services.connect import ConnectService  # noqa: E402


def fixture_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }


def test_walking_skeleton_ask_pipeline(tmp_path: Path) -> None:
    before = fixture_snapshot(FIXTURE_ROOT)

    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)

    ask_output = tmp_path / "ask"
    ask_result = AskService.run(
        vault_root=FIXTURE_ROOT,
        index_path=scan_result.index_path,
        query="Scanner",
        output_dir=ask_output,
    )

    # 1. Pipeline success
    assert ask_result.status == "completed"
    assert len(ask_result.evidence) > 0
    assert (ask_output / "result.json").exists()
    assert (ask_output / "report.md").exists()

    # 2. Input fixture snapshot is intact
    after = fixture_snapshot(FIXTURE_ROOT)
    assert before == after

    # 3. Hashes close: index hash matches
    index_raw = scan_result.index_path.read_bytes()
    expected_index_hash = hashlib.sha256(index_raw).hexdigest()
    assert ask_result.source["index_sha256"] == expected_index_hash

    # 4. Each evidence hash matches source note hash
    for ev in ask_result.evidence:
        source_bytes = (FIXTURE_ROOT / ev["relative_path"]).read_bytes()
        assert ev["content_sha256"] == hashlib.sha256(source_bytes).hexdigest()
        assert ev["quote_sha256"] == hashlib.sha256(ev["quote"].encode("utf-8")).hexdigest()
        assert ev["status"] == "verified"


def test_walking_skeleton_connect_pipeline(tmp_path: Path) -> None:
    before = fixture_snapshot(FIXTURE_ROOT)

    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)

    connect_output = tmp_path / "connect"
    connect_result = ConnectService.run(
        vault_root=FIXTURE_ROOT,
        index_path=scan_result.index_path,
        query="Scanner",
        output_dir=connect_output,
    )

    assert connect_result.status == "completed"
    assert len(connect_result.candidates) > 0
    assert len(connect_result.evidence) > 0
    assert all(len(cand["evidence"]) > 0 for cand in connect_result.candidates)

    for ev in connect_result.evidence:
        source_bytes = (FIXTURE_ROOT / ev["relative_path"]).read_bytes()
        assert ev["content_sha256"] == hashlib.sha256(source_bytes).hexdigest()
        assert ev["quote_sha256"] == hashlib.sha256(ev["quote"].encode("utf-8")).hexdigest()
        assert ev["status"] == "verified"
        assert ev["source_kind"] in ("note_body", "heading", "tag", "wikilink")

    # 5. Regression assertions: structural signal evidence grounding for every emitted candidate
    ev_map = {ev["evidence_id"]: ev for ev in connect_result.evidence}
    for cand in connect_result.candidates:
        cand_evs = [ev_map[eid] for eid in cand["evidence"]]
        assert len(cand_evs) > 0
        has_structural_support = False
        for sb in cand.get("score_breakdown", []):
            kind = sb["kind"]
            val = sb["value"]
            if kind == "wikilink":
                target = val.split("->")[-1].strip() if "->" in val else val
                if any(ev["source_kind"] == "wikilink" and "[[" in ev["quote"] and (target in ev["quote"] or Path(target).stem in ev["quote"]) for ev in cand_evs):
                    has_structural_support = True
                    break
            elif kind == "shared_tag":
                if any(ev["source_kind"] == "tag" and val in ev["quote"] for ev in cand_evs):
                    has_structural_support = True
                    break
            elif kind == "shared_heading_token":
                if any(ev["source_kind"] == "heading" and val.lower() in ev["quote"].lower() for ev in cand_evs):
                    has_structural_support = True
                    break
        assert has_structural_support, f"Candidate {cand['candidate_id']} lacks supporting structural evidence for {cand.get('score_breakdown')}"

    # Specific sample relation assertions
    broken_cand = next(
        (c for c in connect_result.candidates if c["left"]["relative_path"] == "00-扫描入门.md" and c["right"]["relative_path"] == "损坏-frontmatter.md"),
        None,
    )
    assert broken_cand is not None
    broken_evs = [ev_map[eid] for eid in broken_cand["evidence"]]
    assert any(ev["relative_path"] == "损坏-frontmatter.md" and "[[00-扫描入门]]" in ev["quote"] for ev in broken_evs)

    struct_cand = next(
        (c for c in connect_result.candidates if c["left"]["relative_path"] == "00-扫描入门.md" and c["right"]["relative_path"] == "综合结构测试.md"),
        None,
    )
    assert struct_cand is not None
    struct_evs = [ev_map[eid] for eid in struct_cand["evidence"]]
    assert any(ev["source_kind"] == "tag" and "学习/评测" in ev["quote"] for ev in struct_evs)

    assert (connect_output / "result.json").exists()
    assert (connect_output / "report.md").exists()
    report_content = (connect_output / "report.md").read_text(encoding="utf-8")
    assert "## Candidates" in report_content
    assert "## Evidence" in report_content

    # Input fixture snapshot is intact
    assert fixture_snapshot(FIXTURE_ROOT) == before


def test_walking_skeleton_stale_content_rejected(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "test.md"
    note.write_text("# Initial Content", encoding="utf-8")

    scan_output = tmp_path / "scan"
    scan_result = scan_vault(vault, scan_output)

    # Mutate note
    note.write_text("# Tampered Content", encoding="utf-8")

    ask_output = tmp_path / "ask"
    with pytest.raises(LoaderError) as exc_info:
        AskService.run(
            vault_root=vault,
            index_path=scan_result.index_path,
            query="Initial",
            output_dir=ask_output,
        )
    assert exc_info.value.code == "CONTENT_CHANGED"
    assert not ask_output.exists()


def test_walking_skeleton_output_inside_vault_fails(tmp_path: Path) -> None:
    scan_output = tmp_path / "scan"
    scan_result = scan_vault(FIXTURE_ROOT, scan_output)

    unsafe_output = FIXTURE_ROOT / "illegal_output"
    with pytest.raises(LoaderError) as exc_info:
        AskService.run(
            vault_root=FIXTURE_ROOT,
            index_path=scan_result.index_path,
            query="Scanner",
            output_dir=unsafe_output,
        )
    assert exc_info.value.code == "OUTPUT_INSIDE_INPUT"
    assert not unsafe_output.exists()


def test_no_forbidden_module_imports() -> None:
    """Verify that read services in isolation do not import relation_eval, gold dataset or mutation modules."""
    import os
    import subprocess
    cmd = [
        sys.executable,
        "-c",
        (
            "import sys; "
            "import linkloom.cli; "
            "import linkloom.loader; "
            "import linkloom.retrieval; "
            "import linkloom.schemas; "
            "import linkloom.services.ask; "
            "import linkloom.services.connect; "
            "forbidden = [m for m in sys.modules if m.startswith('relation_eval') or m.startswith('linkloom.mutations') or 'gold' in m.lower()]; "
            "sys.exit(1 if forbidden else 0)"
        ),
    ]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(SRC_ROOT) if not existing_pythonpath else f"{SRC_ROOT}{os.pathsep}{existing_pythonpath}"
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Forbidden import detected in clean process: {res.stderr}"
