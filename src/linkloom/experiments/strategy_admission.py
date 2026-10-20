"""Gate 6.2 admission metadata only, not execution or promotion authority.

Trust is independently pinned local host configuration, not authentication or
protection against a hostile Python process. No Provider, renderer or Runtime
entrypoint imports this module. Later execution/binding/serializer gates remain
mandatory and deliberately have no implementation here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from linkloom.experience.store import ExperienceStore
from linkloom.strategy.generation import validate_sources
from linkloom.strategy.models import (
    ComparisonSummary, ExperienceApprovalReference, StrategyCandidate,
    canonical_hash, canonical_json, require_digest, require_fields, require_timestamp,
)
from linkloom.strategy.store import StrategyStore


def closed_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique)
        canonical_json(value)
        return value
    except (UnicodeError, json.JSONDecodeError, TypeError) as exc:
        raise ValueError("invalid admission JSON") from exc


@dataclass(frozen=True)
class ArtifactPin:
    path: Path
    sha256: str

    def __post_init__(self):
        if not isinstance(self.path, Path) or not self.path.is_absolute():
            raise ValueError("resolved absolute pinned artifact required")
        self.validate_path()
        require_digest(self.sha256)

    def validate_path(self):
        try:
            resolved = self.path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("existing canonical resolved artifact required") from exc
        if self.path != resolved:
            raise ValueError("artifact path must already be canonical and resolved")

    @classmethod
    def capture(cls, path):
        """Host/bootstrap helper, not approval of the artifact's contents."""
        path = Path(path).resolve(strict=True)
        return cls(path, hashlib.sha256(path.read_bytes()).hexdigest())

    def read(self):
        self.validate_path()
        try:
            raw = self.path.read_bytes()
        except OSError as exc:
            raise ValueError("missing configured admission artifact") from exc
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("configured artifact pin changed")
        return raw


@dataclass(frozen=True)
class HumanAuditBinding:
    artifact: ArtifactPin
    thread_id: str
    turn_id: str
    message_sha256: str
    reviewed_at: str

    def __post_init__(self):
        if type(self.artifact) is not ArtifactPin or not self.thread_id or not self.turn_id:
            raise ValueError("independently configured human audit required")
        require_digest(self.message_sha256)
        require_timestamp(self.reviewed_at)

    def resolve(self, strategy_id):
        audit = closed_json(self.artifact.read())
        fields = {
            "schema_version", "audit_id", "evidence_ref", "source_thread_id", "source_turn_id",
            "source_message_role", "strategy_id", "decision", "actor", "source", "reviewed_at",
            "reviewed_at_source", "recorded_at", "approval_message", "approval_message_sha256",
            "approval_message_capture", "record_role", "receipt_state_at_capture",
            "provider_or_spend_authorized", "strategy_accepted",
        }
        if not isinstance(audit, dict) or set(audit) != fields:
            raise ValueError("closed captured human decision required")
        expected = {
            "schema_version": "gate6-experimental-admission-human-audit/v1",
            "source_thread_id": self.thread_id, "source_turn_id": self.turn_id,
            "source_message_role": "user", "strategy_id": strategy_id,
            "decision": "approved_for_experiment", "actor": "human:user", "source": "human",
            "reviewed_at": self.reviewed_at, "approval_message_sha256": self.message_sha256,
            "record_role": "human_decision_capture_only_not_admission_receipt",
            "receipt_state_at_capture": "not_persisted_missing_executable_authority",
        }
        if any(audit[k] != v for k, v in expected.items()):
            raise ValueError("audit is not the exact configured human admission decision")
        if (audit["provider_or_spend_authorized"] is not False or audit["strategy_accepted"] is not False
                or not isinstance(audit["audit_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", audit["audit_id"])
                or audit["evidence_ref"] != "audit:" + audit["audit_id"]
                or not isinstance(audit["approval_message"], str)
                or hashlib.sha256(audit["approval_message"].encode("utf-8")).hexdigest() != self.message_sha256):
            raise ValueError("human message/audit binding or non-authority mismatch")
        require_timestamp(audit["recorded_at"])
        return audit


def reviewed_scope(candidate):
    """Fixed reviewed plan; no caller knobs or execution permission."""
    return {
        "schema_version": "gate6-admission-scope/v1", "candidate_count": 1,
        "cases": ["A:Origin", "B:Transfer", "C:Control"],
        "condition_order": ["baseline:A,B,C", "review_baseline", "treatment:A,B,C"],
        "workflows_per_slot": 1, "semantic_retry": 0, "resampling": False,
        "provider": "deepseek", "endpoint": "https://api.deepseek.com", "model": "deepseek-flash",
        "thinking": "disabled", "json_output": True,
        "sampling": {"temperature": "omitted", "top_p": "omitted", "seed": "omitted",
                     "unverified_backend_or_defaults": "INSUFFICIENT_EVIDENCE"},
        "run_limits": {"steps": 8, "logical_requests": 8, "input_tokens": 100000,
                       "output_tokens": 16384, "output_per_request": 2048, "usd": "0.10"},
        "transport": {"sdk_retries": 0, "wrapper_retries": 1, "timeout_seconds": 45,
                      "initial_backoff_seconds": 0.25, "identical_payload_only": True},
        "cost": {"proposed_total_usd": "0.60", "authorized_spend": False,
                 "requires_separate_explicit_cost_authority": True,
                 "unknown_outcome_liability_retained": True},
        "tools": ["search_notes", "read_verified_note"], "read_only": True,
        "workflow": "team_decision", "applicability": candidate.applicability.to_dict(),
        "exclusions": list(candidate.exclusions),
        "context": {"top_k": 1, "max_chars": 2000, "max_utf8_bytes": 16000,
                    "final_serializer_enforces_bounds": True},
        "review_barriers": ["6.2", "6.3", "6.4", "6.5", "6.6", "6.7", "6.8", "6.9"],
        "execution_binding_required": True, "reserve_before_each_send": True,
        "source_context": "one shared resolved read-only vault/index per pair; full non-strategy parity",
        "gold": "private; rules frozen before outputs; read only after all condition terminals sealed",
        "normal_retrieval_authorized": False, "production_eligible": False,
        "final_promotion": "separate unchanged Gate 5 proof/policy/human review only",
    }


@dataclass(frozen=True)
class StrategyExperimentalAdmissionReceipt:
    admission_id: str
    schema_version: str
    strategy_id: str
    candidate_payload_sha256: str
    candidate_event_id: str
    strategy_store_identity: str
    source_approval_references_json: str
    experiment_id: str
    proof_pins_json: str
    scope_json: str
    offline_comparison_json: str
    comparison_manifest_sha256: str
    evidence_limitations_json: str
    decision: str
    actor: str
    source: str
    reviewed_at: str
    rationale: str
    audit_ref: str
    approval_message_sha256: str
    authority_identity: str

    def __post_init__(self):
        for name in ("admission_id", "strategy_id", "candidate_payload_sha256", "candidate_event_id",
                     "strategy_store_identity", "experiment_id", "approval_message_sha256", "authority_identity",
                     "comparison_manifest_sha256"):
            require_digest(getattr(self, name))
        require_timestamp(self.reviewed_at)
        if (self.schema_version != "strategy-experimental-admission/v1"
                or (self.decision, self.actor, self.source) != ("approved_for_experiment", "human:user", "human")
                or not re.fullmatch(r"audit:[0-9a-f]{32}", self.audit_ref)
                or not isinstance(self.rationale, str) or not 1 <= len(self.rationale) <= 1000):
            raise ValueError("invalid closed experimental permission")
        for name in ("source_approval_references_json", "proof_pins_json", "scope_json",
                     "offline_comparison_json", "evidence_limitations_json"):
            raw = getattr(self, name)
            if not isinstance(raw, str) or canonical_json(closed_json(raw)) != raw:
                raise ValueError("canonical nested admission metadata required")
        references = closed_json(self.source_approval_references_json)
        if not isinstance(references, list) or not references:
            raise ValueError("accepted source provenance required")
        for ref in references:
            ExperienceApprovalReference.from_dict(ref)
        summary = ComparisonSummary.from_dict(closed_json(self.offline_comparison_json))
        if summary.strategy_id != self.strategy_id:
            raise ValueError("foreign offline comparison")
        if self.admission_id != canonical_hash({k: v for k, v in self.to_dict().items() if k != "admission_id"}):
            raise ValueError("admission identity mismatch")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        return cls(**require_fields(cls, value))


@dataclass(frozen=True)
class AdmissionClosureAuthority:
    """Independently pinned closure envelope, not an effectiveness evaluator.

The future evidence owner must supply/review its real verdict provenance at
6.8/6.9. This authority only archives its pinned closure decision and label;
it cannot create promotion proof or authorize execution. Test envelopes are
explicitly synthetic. No real closure/verdict is issued by Gate 6.2.
"""
    evidence: ArtifactPin
    review: ArtifactPin

    def __post_init__(self):
        if type(self.evidence) is not ArtifactPin or type(self.review) is not ArtifactPin:
            raise ValueError("independently pinned closure evidence/review required")

    def resolve(self, receipt):
        if not re.search(rb"(?m)^## VERDICT\r?\n\s*APPROVE\s*(?:\r?\n|$)", self.review.read()):
            raise ValueError("closure review must be APPROVE")
        value = closed_json(self.evidence.read())
        required = {"schema_version", "admission_id", "experiment_id", "strategy_id", "reason",
                    "experiment_verdict", "closed_at", "actor", "source", "audit_ref", "evidence_role"}
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("closed closure evidence envelope required")
        if (value["schema_version"] != "gate6-admission-closure-evidence/v1"
                or value["admission_id"] != receipt.admission_id or value["experiment_id"] != receipt.experiment_id
                or value["strategy_id"] != receipt.strategy_id
                or value["evidence_role"] != "closure_only_not_promotion_or_effectiveness_validation"):
            raise ValueError("closure evidence has foreign identity/authority")
        require_timestamp(value["closed_at"])
        if value["reason"] == "experiment_completed":
            if (value["experiment_verdict"] not in {"EFFECTIVE", "NO_EFFECT", "REGRESSION", "INSUFFICIENT_EVIDENCE"}
                    or value["source"] != "independent_reviewer" or not isinstance(value["actor"], str)
                    or not value["actor"].startswith("reviewer:") or value["audit_ref"] is not None):
                raise ValueError("independently reviewed experiment closure label required")
        elif value["reason"] == "human_withdrawal":
            if (value["experiment_verdict"] is not None or (value["actor"], value["source"]) != ("human:user", "human")
                    or not isinstance(value["audit_ref"], str) or not re.fullmatch(r"audit:[0-9a-f]{32}", value["audit_ref"])):
                raise ValueError("explicit pinned human withdrawal required")
        else:
            raise ValueError("unknown closure reason")
        return dict(value, closure_evidence_sha256=self.evidence.sha256, closure_review_sha256=self.review.sha256)


class AdmissionAuthority:
    """Host-configured read-only authority; cannot issue human permission."""
    def __init__(self, *, strategy_store, strategy_id, experiment_id, log_path,
                 candidate_event_id, candidate_payload_sha256, comparison_id,
                 human_audit, proofs, vault_roots=(), closure_authority=None):
        if (type(strategy_store) is not StrategyStore or type(human_audit) is not HumanAuditBinding
                or type(strategy_store.source_store) is not ExperienceStore):
            raise ValueError("configured existing governance and captured human permission required")
        for value in (strategy_id, experiment_id, comparison_id, candidate_event_id, candidate_payload_sha256):
            require_digest(value)
        required = {"architecture", "spec", "architecture_review", "mechanism_review", "source_audit", "implementation"}
        if not isinstance(proofs, dict) or set(proofs) != required or any(type(p) is not ArtifactPin for p in proofs.values()):
            raise ValueError("all independent architecture/mechanism/source/code pins required")
        self.strategy_store, self.strategy_id, self.experiment_id = strategy_store, strategy_id, experiment_id
        self.log_path = Path(log_path).resolve(strict=False)
        self.comparison_id, self.human_audit, self.proofs = comparison_id, human_audit, dict(proofs)
        self.candidate_event_id, self.candidate_payload_sha256 = candidate_event_id, candidate_payload_sha256
        self.closure_authority = closure_authority
        self.vault_roots = tuple(Path(p).resolve() for p in vault_roots)
        self.validate_paths()

    def validate_paths(self):
        """Check canonical boundary/collisions again after late closure configuration."""
        configured = self.strategy_store
        pins = [self.human_audit.artifact, *self.proofs.values()]
        if self.closure_authority is not None:
            if type(self.closure_authority) is not AdmissionClosureAuthority:
                raise ValueError("configured typed closure authority required")
            pins.extend((self.closure_authority.evidence, self.closure_authority.review))
        for pin in pins:
            pin.validate_path()
        resolved_log = self.log_path.resolve(strict=False)
        if self.log_path != resolved_log:
            raise ValueError("admission log path must remain canonical and resolved")
        paths = [resolved_log, configured.log_path.resolve(), configured.source_store.log_path.resolve(),
                 configured.comparison_authority._path.resolve(), *[p.path.resolve(strict=True) for p in pins]]
        if any(p == root or p.is_relative_to(root) for p in paths for root in self.vault_roots):
            raise ValueError("admission/governance artifacts must remain outside all vaults")
        if self.log_path in paths[1:]:
            raise ValueError("admission log must not overwrite governance/evidence")

    @property
    def identity(self):
        return canonical_hash({
            "log": str(self.log_path), "strategy_store": self.strategy_store.store_identity,
            "source_store": str(self.strategy_store.source_store.log_path.resolve()),
            "strategy": self.strategy_id, "experiment": self.experiment_id, "comparison": self.comparison_id,
            "candidate_event_id": self.candidate_event_id, "candidate_payload_sha256": self.candidate_payload_sha256,
            "comparison_manifest": {"path": str(self.strategy_store.comparison_authority._path.resolve()),
                                    "sha256": self.strategy_store.comparison_authority._pin},
            "human_audit": {**asdict(self.human_audit), "artifact": {"path": str(self.human_audit.artifact.path),
                            "sha256": self.human_audit.artifact.sha256}},
            "proofs": {k: {"path": str(p.path), "sha256": p.sha256} for k, p in self.proofs.items()},
            "vault_roots": [str(p) for p in self.vault_roots],
        })

    def build_receipt(self, *, require_candidate=True):
        self.validate_paths()
        for name, pin in self.proofs.items():
            raw = pin.read()
            if name.endswith("review") and not re.search(rb"(?m)^## VERDICT\r?\n\s*APPROVE\s*(?:\r?\n|$)", raw):
                raise ValueError("independently pinned APPROVE verdict required")
        if self.proofs["implementation"].path != Path(__file__).resolve():
            raise ValueError("reviewed admission implementation must be pinned")
        audit = self.human_audit.resolve(self.strategy_id)
        configured = self.strategy_store
        source = configured.source_store
        fresh_sources = ExperienceStore(source.log_path, authority=source._authority, clock=source._clock)
        if fresh_sources._valid_log_bytes is not None:
            raise ValueError("incomplete source Experience history retained; admission fails closed")
        replay = StrategyStore(configured.log_path, source_store=fresh_sources,
                               comparison_authority=configured.comparison_authority)
        if replay._torn_tail:
            raise ValueError("incomplete underlying Strategy history")
        current = replay.get(self.strategy_id)
        if current is None or (require_candidate and current.status != "candidate"):
            raise ValueError("exact persisted candidate required")
        # Read original event through freshly validated Gate 5 replay, not a caller object.
        saved = [e for e in replay._events if e["event_type"] == "candidate_saved"
                 and e["payload"]["strategy_id"] == self.strategy_id]
        if len(saved) != 1:
            raise ValueError("unique original candidate event required")
        if (saved[0]["event_id"] != self.candidate_event_id
                or canonical_hash(saved[0]["payload"]) != self.candidate_payload_sha256):
            raise ValueError("original candidate differs from independently reviewed event/payload pins")
        original = StrategyCandidate.from_dict(saved[0]["payload"])
        validate_sources(original, replay.source_store)
        summary = replay.comparison_authority.resolve(self.comparison_id)
        replay.comparison_authority.validate(original, summary)
        limitations = {
            "recommendation": summary.recommendation, "proof_kind": summary.proof_kind,
            "not_effectiveness_proof": True, "insufficient_evidence_not_waived": True,
            "non_pass_metrics": [r.to_dict() for r in summary.rows if r.baseline != "PASS" or r.candidate != "PASS"],
            "actual_context_costs": [c.to_dict() for c in summary.context_costs],
            "real_results_supplemental_only": True,
        }
        body = dict(
            schema_version="strategy-experimental-admission/v1", strategy_id=self.strategy_id,
            candidate_payload_sha256=canonical_hash(saved[0]["payload"]), candidate_event_id=saved[0]["event_id"],
            strategy_store_identity=replay.store_identity,
            source_approval_references_json=canonical_json([r.to_dict() for r in original.source_review_receipts]),
            experiment_id=self.experiment_id, proof_pins_json=canonical_json({k: p.sha256 for k, p in self.proofs.items()}),
            scope_json=canonical_json(reviewed_scope(original)), offline_comparison_json=canonical_json(summary.to_dict()),
            comparison_manifest_sha256=replay.comparison_authority._pin,
            evidence_limitations_json=canonical_json(limitations), decision=audit["decision"], actor=audit["actor"],
            source=audit["source"], reviewed_at=audit["reviewed_at"], audit_ref=audit["evidence_ref"],
            approval_message_sha256=self.human_audit.message_sha256, authority_identity=self.identity,
            rationale="Human reviewed provenance, genericity, leakage controls, applicability and bounded safety; "
                      "experimental permission only, evidence limits retained, no acceptance or spend authorization.",
        )
        return StrategyExperimentalAdmissionReceipt(admission_id=canonical_hash(body), **body)


@contextmanager
def locked_log(path, *, create=False):
    """Lock the log itself: no lock-file removal or silent torn-tail recovery."""
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = path.open("x+b")
        except FileExistsError:
            handle = path.open("r+b")
    else:
        try:
            handle = path.open("r+b")
        except FileNotFoundError:
            yield None
            return
    with handle:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("admission log busy; no implicit retry or new sample") from exc
        try:
            yield handle
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class ExperimentalAdmissionStore:
    def __init__(self, authority):
        if type(authority) is not AdmissionAuthority:
            raise ValueError("independently configured admission authority required")
        self.authority = authority
        self.log_path = authority.log_path
        self.store_identity = canonical_hash(str(self.log_path))
        self.authority_identity = authority.identity
        self._read_fresh()

    def _read(self, handle):
        if self.authority.identity != self.authority_identity or self.log_path != self.authority.log_path:
            raise ValueError("configured authority identity changed")
        expected = self.authority.build_receipt(require_candidate=False)
        raw = b"" if handle is None else handle.read()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("torn admission log retained; explicit recovery required")
        events, receipt, state = [], None, "absent"
        for line in raw.splitlines():
            event = closed_json(line)
            fields = {"schema_version", "event_id", "previous_event_id", "store_identity", "authority_identity",
                      "event_type", "timestamp", "payload"}
            if not isinstance(event, dict) or set(event) != fields:
                raise ValueError("closed admission event required")
            require_timestamp(event["timestamp"])
            if (event["schema_version"] != "strategy-experimental-admission-event/v1"
                    or event["store_identity"] != self.store_identity
                    or event["authority_identity"] != self.authority_identity
                    or event["previous_event_id"] != (events[-1]["event_id"] if events else "0" * 64)
                    or event["event_id"] != canonical_hash({k: v for k, v in event.items() if k != "event_id"})
                    or canonical_json(event).encode("utf-8") != line):
                raise ValueError("admission log chain/store/configuration mismatch")
            if event["event_type"] == "admission_recorded" and state == "absent":
                receipt = StrategyExperimentalAdmissionReceipt.from_dict(event["payload"])
                if receipt != expected:
                    raise ValueError("receipt differs from fresh configured governance/human evidence")
                state = "approved_for_experiment"
            elif event["event_type"] == "admission_closed" and state == "approved_for_experiment":
                if type(self.authority.closure_authority) is not AdmissionClosureAuthority:
                    raise ValueError("configured closure evidence authority required for replay")
                if event["payload"] != self.authority.closure_authority.resolve(receipt):
                    raise ValueError("closure differs from configured pinned evidence")
                state = "closed"
            else:
                raise ValueError("unsupported or duplicate admission transition")
            events.append(event)
        return events, receipt, state

    def _read_fresh(self):
        self.authority.validate_paths()
        with locked_log(self.log_path) as handle:
            return self._read(handle)

    @property
    def state(self):
        return self._read_fresh()[2]

    @property
    def history(self):
        return tuple(self._read_fresh()[0])

    def _append(self, handle, events, event_type, payload):
        body = {
            "schema_version": "strategy-experimental-admission-event/v1", "event_type": event_type,
            "previous_event_id": events[-1]["event_id"] if events else "0" * 64,
            "store_identity": self.store_identity, "authority_identity": self.authority_identity,
            "timestamp": datetime.now(timezone.utc).isoformat(), "payload": payload,
        }
        event = dict(body, event_id=canonical_hash(body))
        handle.seek(0, os.SEEK_END)
        handle.write(canonical_json(event).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())

    def record_admission(self):
        receipt = self.authority.build_receipt()
        with locked_log(self.log_path, create=True) as handle:
            events, _, state = self._read(handle)
            if state != "absent":
                raise ValueError("one admission only; never reissue for resampling")
            # Revalidate immediately before append; no Gate 5 mutation is invoked.
            if self.authority.build_receipt() != receipt:
                raise ValueError("governance changed before admission persistence")
            self._append(handle, events, "admission_recorded", receipt.to_dict())
        return receipt

    def resolve_for_experiment(self, experiment_id, *, route="gate6_experiment"):
        if route != "gate6_experiment" or experiment_id != self.authority.experiment_id:
            raise ValueError("only the exact experiment may resolve this metadata overlay")
        _, receipt, state = self._read_fresh()
        if state != "approved_for_experiment" or self.authority.build_receipt() != receipt:
            raise ValueError("open admission and current candidate required")
        return receipt

    def close_admission(self):
        if type(self.authority.closure_authority) is not AdmissionClosureAuthority:
            raise ValueError("closure requires independently configured pinned evidence, not caller verdict")
        self.authority.validate_paths()
        with locked_log(self.log_path) as handle:
            events, receipt, state = self._read(handle)
            if state != "approved_for_experiment":
                raise ValueError("only an open persisted admission may close once")
            payload = self.authority.closure_authority.resolve(receipt)
            self._append(handle, events, "admission_closed", payload)
        return payload
