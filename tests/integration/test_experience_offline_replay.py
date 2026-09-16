import builtins
import hashlib
import io
import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

import pytest

from linkloom.evaluation.experience import (
    EvaluationFindingProjector,
    evaluate_experience,
)
from linkloom.experience.context import ExperienceContextBuilder
from linkloom.experience.models import (
    ExperienceQuery,
    ExperienceReviewDecision,
    ReflectionInput,
)
from linkloom.experience.reflection import RunReflection
from linkloom.experience.retrieval import ExperienceRetriever
from linkloom.experience.store import ExperienceStore
from tests.experience_authority_support import fixture_authority, fixture_result


FIXTURES = Path(__file__).parents[1] / "fixtures" / "experience_reflection_v1"
PROJECT_ROOT = Path(__file__).parents[2]


def load_json(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def fixed_clock() -> datetime:
    return datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)


class FakeModel:
    def __init__(self) -> None:
        self.seen_context = ""

    def complete(self, context: str) -> str:
        self.seen_context = context
        return "offline-terminal-result"


def test_sealed_projection_replay_is_gold_safe_bounded_and_auditable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    authority = fixture_authority()
    mps = fixture_result(authority)
    aer = fixture_result(authority, "provider")
    iti = fixture_result(authority, "unresolved")

    file_accesses: list[str] = []
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open

    def guard_path(value: object) -> None:
        try:
            path = os.fspath(value).casefold()
        except TypeError:
            return
        file_accesses.append(path)
        if any(token in path for token in ("golden8", "gold.json", "relation_pairs")):
            raise AssertionError(f"Gold access attempted: {path}")

    def guarded_open(file: object, *args: object, **kwargs: object):
        guard_path(file)
        return original_open(file, *args, **kwargs)

    def guarded_io_open(file: object, *args: object, **kwargs: object):
        guard_path(file)
        return original_io_open(file, *args, **kwargs)

    def guarded_os_open(file: object, *args: object, **kwargs: object):
        guard_path(file)
        return original_os_open(file, *args, **kwargs)

    def network_forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("offline replay attempted network access")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(socket, "create_connection", network_forbidden)

    reflection = RunReflection(authority=authority, clock=fixed_clock)
    mps_result = reflection.reflect(mps)
    assert len(mps_result) == 1
    assert reflection.reflect(aer) == ()
    assert reflection.reflect(iti) == ()
    decision = ExperienceReviewDecision(
        "experience-review/v1", mps_result[0].experience_id, "accepted",
        "reviewer:test", "independent_reviewer", "review:offline", fixed_clock().isoformat(),
    )

    vault_root = tmp_path / "synthetic-vault"
    vault_root.mkdir()
    store = ExperienceStore(
        tmp_path / "artifacts" / "experience.jsonl",
        clock=fixed_clock,
        vault_roots=(vault_root,),
        authority=authority,
    )
    candidate = store.save(mps_result[0])
    assert candidate.status == "candidate"
    accepted = store.record_review(decision)
    assert accepted.status == "accepted"
    assert ExperienceStore(
        store.log_path, clock=fixed_clock, vault_roots=(vault_root,), authority=authority
    ).get(accepted.experience_id) == accepted

    relevant_query = ExperienceQuery(
        workflow="team_decision",
        task_characteristics=("current_decision", "compared_options"),
        pattern_types=("evidence_semantics",),
    )
    selection = ExperienceRetriever(store.list(), authority=authority, store=store).retrieve(
        relevant_query, top_k=3, max_chars=2000
    )
    context = ExperienceContextBuilder(authority=authority, store=store).build(selection)
    assert selection.records == (accepted,)
    assert len(context.model_text) <= 2000

    fake_model = FakeModel()
    assert fake_model.complete(context.model_text) == "offline-terminal-result"
    assert fake_model.seen_context.startswith("Relevant prior experience")

    irrelevant = ExperienceRetriever(store.list(), authority=authority, store=store).retrieve(
        ExperienceQuery(
            workflow="action_inbox",
            task_characteristics=("explicit_task",),
        )
    )
    excluded = ExperienceRetriever(store.list(), authority=authority, store=store).retrieve(
        ExperienceQuery(
            workflow="team_decision",
            task_characteristics=(
                "current_decision",
                "explicit_rejection_evidence_present",
            ),
            pattern_types=("evidence_semantics",),
        )
    )
    assert irrelevant.records == ()
    assert excluded.records == ()

    report = evaluate_experience(
        record=accepted,
        reflection_input=mps,
        authority=authority,
        query=relevant_query,
        selection=selection,
        context=context,
        max_chars=2000,
    )
    assert set(report.statuses().values()) == {"PASS"}
    assert store.provenance(accepted.experience_id) == accepted.provenance
    assert accepted.experience_id not in context.model_text
    assert accepted.source_run_ids[0] not in context.model_text
    assert all(
        token not in context.model_text.casefold()
        for token in ("mps-001", "expected", "gold", "aster")
    )
    assert file_accesses
    assert not any(
        token in path
        for path in file_accesses
        for token in ("golden8", "gold.json", "relation_pairs")
    )


def test_historical_artifacts_are_auditable_but_not_authoritative_input() -> None:
    cases = (
        (
            "mps-001.reflection-input.json",
            PROJECT_ROOT
            / ".artifacts/deepseek_real_provider_smoke/"
            "mps-001-745eb207a01449a98e39ee23b9b4ed1e/mps-001",
            "posthoc_evaluation.json",
        ),
        (
            "aer-002.reflection-input.json",
            PROJECT_ROOT
            / ".artifacts/deepseek_business_evaluation/"
            "run-gate3-authorized-20260916-aer-002-01/aer-002",
            "run_manifest.json",
        ),
        (
            "iti-005.reflection-input.json",
            PROJECT_ROOT
            / ".artifacts/deepseek_business_evaluation/"
            "run-gate3-authorized-20260916-iti-005-01/iti-005",
            "posthoc_evaluation.json",
        ),
    )
    if any(not root.exists() for _, root, _ in cases):
        pytest.skip("local sealed artifacts are not available in this checkout")

    for fixture_name, root, evaluator_name in cases:
        safe_projection = ReflectionInput.from_dict(load_json(fixture_name))
        observed_bytes = (root / "observed_summary.json").read_bytes()
        seal = json.loads(
            (root / "observed_summary.seal.json").read_text(encoding="utf-8")
        )
        evaluator_bytes = (root / evaluator_name).read_bytes()
        observed = json.loads(observed_bytes)
        evaluator = json.loads(evaluator_bytes)

        assert hashlib.sha256(observed_bytes).hexdigest() == (
            safe_projection.observed_summary_sha256
        )
        assert seal["sealed"] is True
        assert seal["gold_available"] is False
        assert seal["observed_summary_sha256"] == (
            safe_projection.observed_summary_sha256
        )
        evaluator_sha256 = hashlib.sha256(evaluator_bytes).hexdigest()
        assert safe_projection.evaluator_artifact_sha256 == evaluator_sha256
        assert safe_projection.evaluator_artifact_schema_version == (
            evaluator["schema_version"]
        )
        assert {
            finding.evaluator_artifact_sha256
            for finding in safe_projection.findings
        } == {evaluator_sha256}
        assert safe_projection.available_source_evidence_refs == tuple(
            f"{safe_projection.source_run_id}:{item}"
            for item in observed["visible_evidence_refs"]
        )
        safe_findings = []
        for finding in safe_projection.findings:
            raw = finding.to_dict()
            raw.pop("evaluator_artifact_sha256")
            safe_findings.append(raw)
        # Historical hashes remain audit evidence, not a complete evaluator receipt.
        with pytest.raises(ValueError, match="authorit"):
            EvaluationFindingProjector().project(
                source_run_id=safe_projection.source_run_id,
                observed_summary=observed_bytes,
                observed_seal=(root / "observed_summary.seal.json").read_bytes(),
                evaluator_artifact=evaluator_bytes,
                findings=tuple(safe_findings),
                task_characteristics=safe_projection.task_characteristics,
            )
