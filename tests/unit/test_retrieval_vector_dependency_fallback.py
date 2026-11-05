"""#59: hybrid retrieval without the optional vector dependency degrades visibly.

The missing dependency is simulated with ``sys.modules[name] = None`` so the
tests behave the same whether or not sentence-transformers is installed.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

from linkloom.retrieval_v2.runtime_backend import (
    VECTOR_DEPENDENCY_MODULE,
    VECTOR_DEPENDENCY_WARNING_CODE,
    RetrievalMode,
    RuntimeRetrievalBackend,
)
from linkloom.schemas import NoteDocument


ROOT = Path(__file__).resolve().parents[2]


def _note(path: str, text: str) -> NoteDocument:
    return NoteDocument(relative_path=path, title=path.removesuffix(".md"), content=text,
                        headings=[], tags=[], wikilinks=[], size_bytes=len(text.encode()),
                        content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                        line_count=len(text.splitlines()))


NOTES = [_note("a.md", "Supplier A is approved."), _note("b.md", "Supplier B is rejected.")]


class TermEmbedder:
    def encode(self, texts):
        return np.asarray([[float(text.casefold().count("supplier")), 1.0] for text in texts],
                          dtype=np.float32)


@pytest.fixture
def vector_dependency_missing(monkeypatch):
    # ``import sentence_transformers`` now raises ModuleNotFoundError and
    # ``importlib.util.find_spec`` reports it as unavailable.
    monkeypatch.setitem(sys.modules, VECTOR_DEPENDENCY_MODULE, None)


@pytest.mark.parametrize("mode", ["hybrid", "directory_hybrid"])
def test_hybrid_without_vector_dependency_falls_back_to_bm25_with_warning(
    vector_dependency_missing, caplog, mode,
):
    with caplog.at_level(logging.WARNING, logger="linkloom.retrieval_v2.runtime_backend"):
        backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: NOTES, mode=mode)
    assert backend.requested_mode is RetrievalMode(mode)
    assert backend.mode is RetrievalMode.BM25

    # Log: one warning naming the requested mode, the missing module and the fix.
    records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(records) == 1
    logged = records[0].getMessage()
    assert f"'{mode}'" in logged and VECTOR_DEPENDENCY_MODULE in logged
    assert "falling back to 'bm25'" in logged and "linkloom[retrieval]" in logged

    # Returned result: search succeeds (no ModuleNotFoundError) and says why.
    result = backend.search("supplier approved", top_k=2)
    assert result.evidence, "BM25 fallback must still return evidence"
    observation = result.observation
    assert observation.retrieval_mode == "bm25"
    assert observation.requested_mode == mode
    assert observation.warnings == ({
        "code": VECTOR_DEPENDENCY_WARNING_CODE,
        "requested_mode": mode,
        "effective_mode": "bm25",
        "missing_module": VECTOR_DEPENDENCY_MODULE,
        "message": logged,
    },)


def test_injected_embedder_does_not_need_the_optional_dependency(vector_dependency_missing, caplog):
    with caplog.at_level(logging.WARNING, logger="linkloom.retrieval_v2.runtime_backend"):
        backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: NOTES,
                                          mode="hybrid", embedder=TermEmbedder())
    assert backend.mode is RetrievalMode.HYBRID
    assert backend.warnings == ()
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]
    observation = backend.search("supplier approved", top_k=2).observation
    assert observation.retrieval_mode == "hybrid" and observation.warnings == ()


def test_explicit_bm25_has_no_degradation_warning(vector_dependency_missing):
    backend = RuntimeRetrievalBackend(workspace_id="w", document_provider=lambda: NOTES, mode="bm25")
    observation = backend.search("supplier approved", top_k=2).observation
    assert observation.retrieval_mode == "bm25" and observation.requested_mode == "bm25"
    assert observation.warnings == ()


def test_hold_g_runtime_is_not_refused_when_vector_dependency_is_missing(
    vector_dependency_missing, monkeypatch, tmp_path, caplog,
):
    """Before #59 this run failed inside search_notes and was summarised as 'refused'."""
    sys.path.insert(0, str(ROOT / "tests" / "integration"))
    from run_bounded_multihop import run_case

    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "hybrid")
    cases = json.loads((ROOT / "tests/fixtures/bounded_multihop/heldout.json").read_text(encoding="utf-8"))["cases"]
    case = next(c for c in cases if c["id"] == "HOLD-G")
    public = {key: value for key, value in case.items() if key != "expected"}
    with caplog.at_level(logging.WARNING, logger="linkloom.retrieval_v2.runtime_backend"):
        result = run_case(public, tmp_path)
    assert result["outcome"] != "refused", result
    assert result["reason"] == "conflicting_evidence"
    assert any(r.levelno == logging.WARNING and VECTOR_DEPENDENCY_MODULE in r.getMessage()
               for r in caplog.records)


def test_importing_reliability_runner_does_not_change_retrieval_mode():
    """#58: module import must not mutate process-wide retrieval configuration."""
    env = {k: v for k, v in os.environ.items() if k != "LINKLOOM_RETRIEVAL_MODE"}
    code = (
        "import os, sys\n"
        f"sys.path[:0] = [{str(ROOT / 'src')!r}, {str(ROOT)!r}, {str(ROOT / 'tests' / 'integration')!r}]\n"
        "import run_real_agent_reliability\n"
        "print(os.environ.get('LINKLOOM_RETRIEVAL_MODE', '<unset>'))\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], env=env, cwd=ROOT,
                               capture_output=True, text=True, timeout=120, check=True)
    assert completed.stdout.strip().splitlines()[-1] == "<unset>"
