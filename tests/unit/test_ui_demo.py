from __future__ import annotations

import pytest

from linkloom.ui.demo import DemoCase, DemoRunBackend


def test_mps_demo_builds_evidence_from_the_frozen_source_lines() -> None:
    case = DemoCase.load_mps_001()

    assert case.context.display_name == "Atlas Lantern"
    assert case.context.document_count == 6
    assert len(case.documents) == 6
    assert case.evidence["mps-decision-10"]["quote"].startswith(
        "The Atlas Lantern team selected Aster A"
    )
    assert case.evidence["mps-decision-10"]["line_start"] == 10
    assert case.evidence["mps-decision-10"]["line_end"] == 12
    assert case.evidence["mps-readiness-region-15"]["relative_path"] == (
        "06-adoption-readiness.md"
    )


def test_demo_success_and_insufficient_states_use_the_same_public_contract() -> None:
    backend = DemoRunBackend(DemoCase.load_mps_001())

    success = backend.demo_snapshot("success")
    insufficient = backend.demo_snapshot("insufficient")
    running = backend.demo_snapshot("running")

    assert success["schema_version"] == insufficient["schema_version"]
    assert success["kind"] == "success"
    assert success["decision"]["value"].startswith("Aster A")
    assert success["evidence"][0]["source"]["preview_kind"] == "document"
    assert insufficient["kind"] == "insufficient"
    assert insufficient["decision"]["value"] is None
    assert insufficient["uncertainty"]["unknown_fields"] == ["production cutover date"]
    assert running["kind"] == "running"
    assert running["run"]["stage"] == "reading"


def test_demo_run_advances_from_realistic_product_stages_to_answer() -> None:
    backend = DemoRunBackend(DemoCase.load_mps_001())

    started = backend.start(backend.context()["default_query"])
    first = backend.inspect(started["run"]["id"])
    second = backend.inspect(started["run"]["id"])
    final = backend.inspect(started["run"]["id"])

    assert started["kind"] == "running"
    assert first["run"]["stage"] == "reading"
    assert second["run"]["stage"] == "checking"
    assert final["kind"] == "success"


def test_demo_rejects_unknown_state_and_run() -> None:
    backend = DemoRunBackend(DemoCase.load_mps_001())

    assert backend.inspect("missing") is None
    assert backend.demo_snapshot("unknown") is None


def test_demo_does_not_replay_a_fixed_answer_for_an_unsupported_question() -> None:
    backend = DemoRunBackend(DemoCase.load_mps_001())

    started = backend.start("Who approved the project budget?")
    backend.inspect(started["run"]["id"])
    backend.inspect(started["run"]["id"])
    final = backend.inspect(started["run"]["id"])

    assert final["kind"] == "error"
    assert final["error"]["code"] == "DEMO_QUERY_NOT_AVAILABLE"


def test_demo_rejects_an_empty_question_at_its_own_boundary() -> None:
    backend = DemoRunBackend(DemoCase.load_mps_001())

    with pytest.raises(ValueError, match="non-empty decision question"):
        backend.start("   ")
