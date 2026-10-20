"""Synthetic offline admission evidence only; never human/Provider proof."""
import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from linkloom.experiments.strategy_admission import (
    AdmissionAuthority, ArtifactPin, ExperimentalAdmissionStore,
    HumanAuditBinding, StrategyExperimentalAdmissionReceipt,
)
from linkloom.strategy.authority import ComparisonAuthority
from linkloom.strategy.models import StrategyCandidate, canonical_hash, canonical_json
from linkloom.strategy.store import StrategyStore
from tests.strategy_support import strategy_root, accepted_source, evaluation_fixture


def pin_json(path, value):
    path.write_text(canonical_json(value), encoding="utf-8")
    return ArtifactPin.capture(path)


def setup_admission(root, source, *, proof_kind="scripted_replay"):
    candidate, bundle, oracle = evaluation_fixture(root, source, proof_kind=proof_kind)
    comparison = ComparisonAuthority(bundle.root / "manifest.json",
                                    manifest_sha256=bundle.manifest_sha256, oracle_authority=oracle)
    strategies = StrategyStore(root / "strategy.jsonl", source_store=source, comparison_authority=comparison)
    strategies.save(candidate)
    message = "SYNTHETIC TEST ONLY: exact candidate experimentally admitted; not accepted or spend authorized."
    message_hash = hashlib.sha256(message.encode()).hexdigest()
    audit = {
        "schema_version": "gate6-experimental-admission-human-audit/v1",
        "audit_id": "a" * 32, "evidence_ref": "audit:" + "a" * 32,
        "source_thread_id": "synthetic-test-thread", "source_turn_id": "synthetic-test-turn",
        "source_message_role": "user", "strategy_id": candidate.strategy_id,
        "decision": "approved_for_experiment", "actor": "human:user", "source": "human",
        "reviewed_at": "2026-09-17T13:43:07.000Z", "reviewed_at_source": "synthetic test clock",
        "recorded_at": "2026-09-17T13:44:00+00:00", "approval_message": message,
        "approval_message_sha256": message_hash, "approval_message_capture": "synthetic test only",
        "record_role": "human_decision_capture_only_not_admission_receipt",
        "receipt_state_at_capture": "not_persisted_missing_executable_authority",
        "provider_or_spend_authorized": False, "strategy_accepted": False,
    }
    audit_pin = pin_json(root / "admission.audit.json", audit)
    proofs = {}
    for name in ("architecture", "spec", "architecture_review", "mechanism_review", "source_audit"):
        path = root / (name + ".md")
        path.write_text("SYNTHETIC TEST ONLY\n\n## VERDICT\n\nAPPROVE\n", encoding="utf-8")
        proofs[name] = ArtifactPin.capture(path)
    import linkloom.experiments.strategy_admission as implementation
    proofs["implementation"] = ArtifactPin.capture(Path(implementation.__file__))
    binding = HumanAuditBinding(audit_pin, "synthetic-test-thread", "synthetic-test-turn",
                               message_hash, audit["reviewed_at"])
    authority = AdmissionAuthority(
        strategy_store=strategies, strategy_id=candidate.strategy_id,
        candidate_event_id=strategies._candidate_events[candidate.strategy_id],
        candidate_payload_sha256=canonical_hash(candidate.to_dict()),
        experiment_id=canonical_hash("synthetic-experiment"),
        log_path=root / "admission.jsonl", comparison_id=bundle.summary.result_id,
        human_audit=binding, proofs=proofs,
    )
    return authority, strategies, candidate, bundle


def test_exact_admission_replays_without_promoting(accepted_source, strategy_root):
    authority, strategies, candidate, bundle = setup_admission(strategy_root, accepted_source[0])
    originals = {p: p.read_bytes() for p in (strategies.log_path, accepted_source[0].log_path)}
    receipt = ExperimentalAdmissionStore(authority).record_admission()
    assert type(receipt) is StrategyExperimentalAdmissionReceipt
    assert receipt.reviewed_at == "2026-09-17T13:43:07.000Z"
    assert receipt.candidate_payload_sha256 == canonical_hash(candidate.to_dict())
    assert json.loads(receipt.offline_comparison_json)["recommendation"] == "insufficient_evidence"
    replay = ExperimentalAdmissionStore(authority)
    assert replay.resolve_for_experiment(authority.experiment_id) == receipt
    assert replay.state == "approved_for_experiment"
    assert strategies.get(candidate.strategy_id).status == "candidate"
    assert strategies.list("accepted") == ()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    # This module grants metadata eligibility only, never serialization/send readiness.
    assert not hasattr(replay, "serialize") and not hasattr(replay, "reserve")
    with pytest.raises(ValueError):
        replay.resolve_for_experiment(authority.experiment_id, route="production")


def test_duplicate_foreign_or_raw_caller_permission_cannot_admit(accepted_source, strategy_root):
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    store.record_admission()
    before = authority.log_path.read_bytes()
    with pytest.raises(ValueError):
        store.record_admission()
    with pytest.raises(ValueError):
        store.resolve_for_experiment(canonical_hash("foreign-experiment"))
    with pytest.raises(ValueError):
        ExperimentalAdmissionStore({"approved": True})
    assert authority.log_path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("actor", "reviewer:test"), ("source", "independent_reviewer"),
    ("source_message_role", "assistant"), ("decision", "accepted"),
    ("provider_or_spend_authorized", True), ("strategy_accepted", True),
    ("strategy_id", "0" * 64), ("source_turn_id", "foreign-turn"),
    ("approval_message", "different human decision"),
])
def test_wrong_audit_even_repinned_is_refused(field, value, accepted_source, strategy_root):
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    audit = json.loads(authority.human_audit.artifact.read())
    audit[field] = value
    pin = pin_json(authority.human_audit.artifact.path, audit)
    authority.human_audit = replace(authority.human_audit, artifact=pin)
    with pytest.raises(ValueError):
        ExperimentalAdmissionStore(authority).record_admission()


def test_stale_proof_and_torn_log_fail_closed_without_repair(accepted_source, strategy_root):
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    store.record_admission()
    with authority.log_path.open("ab") as handle:
        handle.write(b'{"unfinished":')
    raw = authority.log_path.read_bytes()
    with pytest.raises(ValueError):
        store.resolve_for_experiment(authority.experiment_id)
    with pytest.raises(ValueError):
        store.record_admission()
    assert authority.log_path.read_bytes() == raw
    authority.proofs["architecture"].path.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError):
        authority.build_receipt()


def configure_closure(authority, receipt, verdict, *, reason="experiment_completed"):
    """Synthetic closure envelope, NOT evidence of real effectiveness."""
    from linkloom.experiments.strategy_admission import AdmissionClosureAuthority
    artifact = pin_json(authority.log_path.parent / "closure.evidence.json", {
        "schema_version": "gate6-admission-closure-evidence/v1",
        "admission_id": receipt.admission_id, "experiment_id": authority.experiment_id,
        "strategy_id": authority.strategy_id, "reason": reason,
        "experiment_verdict": verdict, "closed_at": "2026-09-17T14:00:00+00:00",
        "actor": "human:user" if reason == "human_withdrawal" else "reviewer:offline_test",
        "source": "human" if reason == "human_withdrawal" else "independent_reviewer",
        "audit_ref": "audit:" + "b" * 32 if reason == "human_withdrawal" else None,
        "evidence_role": "closure_only_not_promotion_or_effectiveness_validation",
    })
    authority.closure_authority = AdmissionClosureAuthority(artifact, authority.proofs["mechanism_review"])


@pytest.mark.parametrize("verdict", ["EFFECTIVE", "NO_EFFECT", "REGRESSION", "INSUFFICIENT_EVIDENCE"])
def test_each_verdict_closes_without_strategy_transition(verdict, accepted_source, strategy_root, monkeypatch):
    authority, strategies, candidate, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    receipt = store.record_admission()
    originals = {p: p.read_bytes() for p in (strategies.log_path, accepted_source[0].log_path)}
    configure_closure(authority, receipt, verdict)
    def forbidden_review(*args, **kwargs):
        raise AssertionError("admission/closure/replay must not initiate Gate 5 promotion")
    monkeypatch.setattr(StrategyStore, "record_review", forbidden_review)
    closure = store.close_admission()
    assert closure["experiment_verdict"] == verdict
    assert store.state == "closed"
    replay = ExperimentalAdmissionStore(authority)
    assert replay.history[-1]["payload"] == closure
    assert replay.history[0]["payload"] == receipt.to_dict()
    assert strategies.get(candidate.strategy_id).status == "candidate"
    assert strategies.list("accepted") == strategies.list("rejected") == ()
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    with pytest.raises(ValueError):
        replay.resolve_for_experiment(authority.experiment_id)
    with pytest.raises(ValueError):
        replay.record_admission()
    with pytest.raises(ValueError):
        replay.close_admission()


def test_withdrawal_is_append_only_and_cannot_be_reused(accepted_source, strategy_root):
    authority, strategies, candidate, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    receipt = store.record_admission()
    prefix = authority.log_path.read_bytes()
    configure_closure(authority, receipt, None, reason="human_withdrawal")
    store.close_admission()
    assert authority.log_path.read_bytes().startswith(prefix)
    assert store.state == "closed" and strategies.get(candidate.strategy_id).status == "candidate"


def test_no_configured_closure_or_unknown_execution_event_is_authority(accepted_source, strategy_root):
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    store.record_admission()
    raw = authority.log_path.read_bytes()
    with pytest.raises(ValueError):
        store.close_admission()
    assert authority.log_path.read_bytes() == raw
    event = json.loads(raw)
    event["event_type"] = "execution_binding"
    event["event_id"] = canonical_hash({k: v for k, v in event.items() if k != "event_id"})
    authority.log_path.write_text(canonical_json(event) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ExperimentalAdmissionStore(authority)


def test_separate_compatible_gate5_review_is_not_reset_by_closure(accepted_source, strategy_root):
    from linkloom.strategy.models import HumanStrategyDecision
    authority, strategies, candidate, bundle = setup_admission(strategy_root, accepted_source[0], proof_kind="responsive_fake")
    store = ExperimentalAdmissionStore(authority)
    receipt = store.record_admission()
    assert bundle.summary.recommendation == "recommend_accept"
    strategies.record_review(HumanStrategyDecision(
        "strategy-human-decision/v1", candidate.strategy_id, bundle.summary.result_id,
        "accepted", "human:test_fixture", "human", "2026-09-17T13:50:00+00:00",
        "Reviewed the complete offline comparison and its limits.",
    ))
    raw = strategies.log_path.read_bytes()
    with pytest.raises(ValueError):
        store.resolve_for_experiment(authority.experiment_id)
    configure_closure(authority, receipt, "NO_EFFECT")
    store.close_admission()
    assert store.state == "closed"
    assert strategies.get(candidate.strategy_id).status == "accepted"
    assert strategies.log_path.read_bytes() == raw


def test_admitted_candidate_cannot_enter_normal_retrieval_or_serializer(accepted_source, strategy_root):
    from linkloom.experience.models import ExperienceQuery
    from linkloom.strategy.context import StrategyContextBuilder
    from linkloom.strategy.retrieval import StrategyRetriever, StrategySelection
    authority, strategies, candidate, _ = setup_admission(strategy_root, accepted_source[0])
    receipt = ExperimentalAdmissionStore(authority).record_admission()
    query = ExperienceQuery("team_decision", ("current_decision", "compared_options"))
    selection = StrategyRetriever(strategies).retrieve(query)
    assert selection.strategy_ids == ()
    assert StrategyContextBuilder(strategies).build(selection).to_dict()["model_text"] == ""
    with pytest.raises(ValueError):
        StrategyContextBuilder(strategies).build(StrategySelection((candidate.strategy_id,), query, 1, 2000))
    with pytest.raises(ValueError):
        StrategyContextBuilder(strategies).build(receipt)
    with pytest.raises(ValueError):
        strategies.record_review(receipt)
    with pytest.raises(ValueError):
        replace(candidate, status="accepted")


@pytest.mark.parametrize("damage", ["missing_acceptance", "torn_source", "torn_strategy", "foreign_log", "rehashed_payload"])
def test_stale_or_foreign_governance_blocks_restart(damage, accepted_source, strategy_root):
    authority, strategies, _, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    store.record_admission()
    if damage == "missing_acceptance":
        source = accepted_source[0].log_path
        source.write_bytes(source.read_bytes().splitlines(keepends=True)[0])
    elif damage in {"torn_source", "torn_strategy"}:
        path = accepted_source[0].log_path if damage == "torn_source" else strategies.log_path
        with path.open("ab") as handle:
            handle.write(b'{"unfinished":')
    elif damage == "foreign_log":
        foreign = authority.log_path.parent / "foreign-admission.jsonl"
        foreign.write_bytes(authority.log_path.read_bytes())
        authority.log_path = foreign
    else:
        event = json.loads(authority.log_path.read_bytes())
        event["payload"]["scope_json"] = canonical_json({"authorized_spend": True})
        payload = event["payload"]
        payload["admission_id"] = canonical_hash({k: v for k, v in payload.items() if k != "admission_id"})
        event["event_id"] = canonical_hash({k: v for k, v in event.items() if k != "event_id"})
        authority.log_path.write_text(canonical_json(event) + "\n", encoding="utf-8")
    raw = authority.log_path.read_bytes()
    with pytest.raises(ValueError):
        ExperimentalAdmissionStore(authority).resolve_for_experiment(authority.experiment_id)
    assert authority.log_path.read_bytes() == raw


def test_busy_owned_log_does_not_duplicate_or_retry(accepted_source, strategy_root):
    from linkloom.experiments.strategy_admission import locked_log
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    store.record_admission()
    raw = authority.log_path.read_bytes()
    with locked_log(authority.log_path):
        with pytest.raises(ValueError):
            ExperimentalAdmissionStore(authority).record_admission()
    assert authority.log_path.read_bytes() == raw


def test_review_and_scope_cannot_be_caller_relaxed(accepted_source, strategy_root):
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    authority.proofs["mechanism_review"].path.write_text("## VERDICT\n\nREJECT\n", encoding="utf-8")
    authority.proofs["mechanism_review"] = ArtifactPin.capture(authority.proofs["mechanism_review"].path)
    with pytest.raises(ValueError):
        ExperimentalAdmissionStore(authority).record_admission()


@pytest.mark.parametrize("change", ["wrong_strategy", "unknown_verdict", "wrong_actor", "stale_pin"])
def test_invalid_closure_cannot_close_or_promote(change, accepted_source, strategy_root):
    authority, strategies, candidate, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    receipt = store.record_admission()
    configure_closure(authority, receipt, "EFFECTIVE")
    pin = authority.closure_authority.evidence
    payload = json.loads(pin.read())
    if change == "wrong_strategy":
        payload["strategy_id"] = "0" * 64
    elif change == "unknown_verdict":
        payload["experiment_verdict"] = "recommend_accept"
    else:
        payload["actor"] = "model:fake"
    updated = pin_json(pin.path, payload)
    if change != "stale_pin":
        authority.closure_authority = replace(authority.closure_authority, evidence=updated)
    raw = authority.log_path.read_bytes()
    with pytest.raises(ValueError):
        store.close_admission()
    assert authority.log_path.read_bytes() == raw
    assert strategies.get(candidate.strategy_id).status == "candidate"


def test_changed_original_payload_cannot_be_newly_admitted_even_if_id_is_same(accepted_source, strategy_root):
    authority, strategies, candidate, _ = setup_admission(strategy_root, accepted_source[0])
    event = json.loads(strategies.log_path.read_bytes())
    event["payload"]["created_at"] = "2026-09-17T12:00:00+00:00"
    # Strategy identity intentionally excludes created_at; reviewed payload/event do not.
    assert StrategyCandidate.from_dict(event["payload"]).strategy_id == candidate.strategy_id
    event["event_id"] = canonical_hash({k: v for k, v in event.items() if k != "event_id"})
    strategies.log_path.write_text(canonical_json(event) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ExperimentalAdmissionStore(authority).record_admission()


def reconfigure(authority, **changes):
    configuration = {name: getattr(authority, name) for name in (
        "strategy_store", "strategy_id", "experiment_id", "log_path", "candidate_event_id",
        "candidate_payload_sha256", "comparison_id", "human_audit", "proofs", "vault_roots", "closure_authority",
    )}
    return AdmissionAuthority(**dict(configuration, **changes))


def directory_alias(alias, target):
    """Retained synthetic alias. Windows junctions need no symlink privilege."""
    import os
    import subprocess
    if os.name == "nt":
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(target)],
                       check=True, capture_output=True)
    else:
        alias.symlink_to(target, target_is_directory=True)
    assert alias.resolve(strict=True) == target.resolve(strict=True)


def test_direct_dotdot_pin_is_rejected_without_changing_artifact(strategy_root):
    target = strategy_root / "target.json"
    target.write_bytes(b"synthetic-pinned-bytes")
    bridge = strategy_root / "bridge"
    bridge.mkdir()
    alias = bridge / ".." / target.name
    assert alias != alias.resolve(strict=True)
    with pytest.raises(ValueError, match="resolved|canonical"):
        ArtifactPin(alias, hashlib.sha256(target.read_bytes()).hexdigest())
    assert target.read_bytes() == b"synthetic-pinned-bytes"


def test_direct_missing_pin_is_not_a_resolved_existing_artifact(strategy_root):
    with pytest.raises(ValueError, match="resolved|canonical"):
        ArtifactPin(strategy_root / "missing.json", "0" * 64)


def test_direct_symlink_or_junction_pin_is_rejected_but_capture_resolves(strategy_root):
    target = strategy_root / "target-directory"
    target.mkdir()
    artifact = target / "pinned.json"
    artifact.write_bytes(b"synthetic-alias-test")
    alias = strategy_root / "directory-alias"
    directory_alias(alias, target)
    with pytest.raises(ValueError, match="resolved|canonical"):
        ArtifactPin(alias / artifact.name, hashlib.sha256(artifact.read_bytes()).hexdigest())
    assert ArtifactPin.capture(alias / artifact.name).path == artifact.resolve(strict=True)
    assert artifact.read_bytes() == b"synthetic-alias-test"


def test_capture_cannot_hide_vault_artifact_behind_alias(accepted_source, strategy_root):
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    vault = strategy_root / "synthetic-vault"
    vault.mkdir()
    alias = strategy_root / "vault-alias"
    directory_alias(alias, vault)
    inside = vault / "audit.json"
    inside.write_bytes(authority.human_audit.artifact.read())
    binding = replace(authority.human_audit, artifact=ArtifactPin.capture(alias / inside.name))
    with pytest.raises(ValueError, match="outside"):
        reconfigure(authority, human_audit=binding, vault_roots=(vault,))
    assert not authority.log_path.exists()


@pytest.mark.parametrize("alias", [False, True])
def test_manifest_collision_is_refused_before_any_write(alias, accepted_source, strategy_root):
    authority, strategies, _, _ = setup_admission(strategy_root, accepted_source[0])
    manifest = strategies.comparison_authority._path.resolve(strict=True)
    log_path = manifest
    if alias:
        bridge = manifest.parent / "bridge"
        bridge.mkdir()
        log_path = bridge / ".." / manifest.name
    raw = manifest.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        reconfigure(authority, log_path=log_path)
    assert manifest.read_bytes() == raw
    assert not authority.log_path.exists()


def test_comparison_manifest_is_in_vault_boundary_guard(accepted_source, strategy_root):
    authority, strategies, _, _ = setup_admission(strategy_root, accepted_source[0])
    manifest = strategies.comparison_authority._path.resolve(strict=True)
    with pytest.raises(ValueError, match="outside"):
        reconfigure(authority, vault_roots=(manifest.parent,))
    assert not authority.log_path.exists()


@pytest.mark.parametrize("collision", ["evidence", "review"])
def test_configured_closure_pin_collision_is_refused_before_write(collision, accepted_source, strategy_root):
    from linkloom.experiments.strategy_admission import AdmissionClosureAuthority
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    evidence = pin_json(strategy_root / "synthetic-closure.json", {"synthetic": True})
    review_path = strategy_root / "synthetic-closure-review.md"
    review_path.write_text("SYNTHETIC TEST ONLY\n\n## VERDICT\n\nAPPROVE\n", encoding="utf-8")
    review = ArtifactPin.capture(review_path)
    closure = AdmissionClosureAuthority(evidence, review)
    target = getattr(closure, collision).path
    raw = target.read_bytes()
    with pytest.raises(ValueError, match="overwrite"):
        reconfigure(authority, log_path=target, closure_authority=closure)
    assert target.read_bytes() == raw and not authority.log_path.exists()


def test_late_closure_configuration_rechecks_collisions(accepted_source, strategy_root):
    from linkloom.experiments.strategy_admission import AdmissionClosureAuthority
    authority, _, _, _ = setup_admission(strategy_root, accepted_source[0])
    store = ExperimentalAdmissionStore(authority)
    store.record_admission()
    raw = authority.log_path.read_bytes()
    authority.closure_authority = AdmissionClosureAuthority(ArtifactPin.capture(authority.log_path),
                                                          authority.proofs["mechanism_review"])
    with pytest.raises(ValueError, match="overwrite"):
        store.close_admission()
    assert authority.log_path.read_bytes() == raw
