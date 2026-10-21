"""One host-authorized offline semantic batch per run, durably correlated.

These are Runtime audit artifacts, not P4 receipts or Decision Memory. A pending
reservation is never retried externally. Source/semantic validation stays in A.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
import json
import math
import os
from types import SimpleNamespace

from linkloom.agents.model_adapter import ModelResponse, ModelUsage
from linkloom.semantic_ingestion.extraction import ProviderRequestAuthorization
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_semantic_models import SemanticResult
from linkloom.semantic_ingestion.task_relation_semantic_proposer import SemanticProposer, build_request, _decode
from linkloom.semantic_ingestion.task_relation_semantic_validation import validate_proposals, compare_proposed_revision
from linkloom.tools.semantic_context import build_semantic_context, evidence_coverage


@dataclass(frozen=True)
class SemanticCallPolicy:
    execution_kind: str
    max_requests: int = 1
    max_input_tokens: int = 32768
    max_output_tokens: int = 8192
    max_cost_usd: float = 0.0
    request_cost_upper_bound_usd: float = 0.0
    live_authorized: bool = False

    def __post_init__(self):
        if self.execution_kind not in {"FAKE", "REPLAY"} and not (self.execution_kind == 'LIVE' and self.live_authorized is True):
            raise ValueError("new live semantic Provider execution is not enabled")
        if type(self.max_requests) is not int or self.max_requests not in (0, 1):
            raise ValueError("one semantic batch per run; request budget must be 0 or 1")
        if any(type(v) is not int or v < 1 for v in (self.max_input_tokens, self.max_output_tokens)):
            raise ValueError("explicit positive semantic token budgets required")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0
               for v in (self.max_cost_usd, self.request_cost_upper_bound_usd)):
            raise ValueError("invalid semantic cost budget")


def _restore_result(data, context, model_id):
    """Reuse A's canonical decoder, not a second proposal model/parser."""
    usage = ModelUsage.from_dict(data["usage"])
    proposals = ()
    if data["proposals"]:
        calls = data["untrusted_output"]["tool_calls"]
        proposals = _decode(calls[0]["arguments"], context, model_id,
                            data["proposals"][0]["actual_model_version"], usage)
    values = {f.name: data[f.name] for f in fields(SemanticResult) if f.name not in {"proposals", "usage"}}
    values["errors"] = tuple(values["errors"])
    restored = SemanticResult(**values, proposals=proposals, usage=usage)
    if restored.to_dict() != data:
        raise ValueError("semantic result payload does not match its durable response")
    return restored


def _proposal_view(result, context):
    reports = validate_proposals(result, context)
    rows = []
    coverage_by_id = {p.proposal_id: evidence_coverage(p, context) for p in result.proposals}
    for proposal, report in zip(result.proposals, reports):
        coverage = coverage_by_id[proposal.proposal_id]
        valid = report.stage == "EVIDENCE_VALID" and coverage["status"] == "COMPLETE"
        row = proposal.to_dict()
        row.pop("usage")  # usage belongs to the invocation, not each proposal
        row.update(proposal_id=proposal.proposal_id, source_validation=report.to_dict(),
            coverage=coverage, evidence_status="EVIDENCE_VALID" if valid else "EVIDENCE_INVALID",
            semantic_status="SEMANTIC_NOT_EVALUATED", publishable=False,
            displayable_as_model_analysis=valid, release_status="PENDING_SEMANTIC_REVIEW" if valid else "BLOCKED")
        if proposal.predicate == "REVISES_PLAN":
            comparison = compare_proposed_revision(proposal, result, context)
            # A conditional identity premise also needs the declared coverage.
            if not valid or any(c["status"] != "COMPLETE" for c in coverage_by_id.values()):
                comparison.update(status="BLOCKED", delta_calendar_days=None)
            row["comparison"] = comparison
        rows.append(row)
    return rows


def _compact_evidence(view):
    spans = {}
    def visit(value):
        if isinstance(value, dict):
            if {"evidence_ref", "artifact_version_id", "quote", "quote_sha256", "char_start"} <= value.keys():
                key = "span_" + canonical_hash(value)
                spans[key] = value
                return {"span_ref": key}
            return {k: visit(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [visit(v) for v in value]
        return value
    output = visit(view)
    output["evidence_spans"] = spans
    if len(json.dumps(output, ensure_ascii=False).encode("utf8")) > 32 * 1024:
        output.update(analysis_status="BLOCKED", proposals=[], evidence_spans={},
                      errors=[*output["errors"], "SEMANTIC_DISPLAY_BUDGET_EXCEEDED"])
    return output


class SemanticAnalysis:
    def __init__(self, investigation_id, workspace_id, snapshot_provider, adapter, *, model_id, policy):
        if policy.execution_kind not in {'FAKE', 'REPLAY', 'LIVE'}:
            raise ValueError('new live semantic Provider execution is not enabled')
        self.investigation_id = investigation_id
        self.workspace_id = workspace_id
        self.snapshot_provider = snapshot_provider
        self.adapter = adapter
        self.model_id = model_id
        self.policy = policy

    def bind(self, run_id, artifact_store, question):
        return BoundSemanticAnalysis(self, run_id, artifact_store, question)


class BoundSemanticAnalysis:
    def __init__(self, owner, run_id, store, question):
        self.owner, self.run_id, self.store, self.question = owner, run_id, store, question
        self.workspace_id, self.investigation_id = owner.workspace_id, owner.investigation_id
        self.identity = dict(run_id=run_id, workspace_id=self.workspace_id, investigation_id=self.investigation_id,
                             model_id=owner.model_id, policy=asdict(owner.policy))
        if owner.policy.execution_kind != 'LIVE':
            self.identity['policy'].pop('live_authorized')
        self.invocation_id = "semantic_" + canonical_hash(self.identity)
        # One slot per outer run, even if a caller attempts a different job.
        self.prefix = "semantic/" + canonical_hash(run_id)[:24]
        self.reservation_ref = self.prefix + "/reservation.json"
        self.response_ref = self.prefix + "/response.json"
        self.result_ref = self.prefix + "/result.json"

    def _build(self):
        snapshot = self.owner.snapshot_provider()
        built = build_semantic_context(snapshot, expected_workspace_id=self.workspace_id)
        if built.ready and built.context.question != self.question:
            return replace(built, errors=("INVESTIGATION_QUESTION_MISMATCH",))
        if built.ready:
            policy = self.owner.policy
            limits = replace(built.context.limits,
                max_input_tokens=min(built.context.limits.max_input_tokens, policy.max_input_tokens),
                max_output_tokens=min(built.context.limits.max_output_tokens, policy.max_output_tokens))
            built = replace(built, context=replace(built.context, limits=limits))
        return built

    def _exists(self, ref):
        return self.store._path_for(ref).exists()

    def _blocked(self, call, built, errors):
        return dict(schema_version="task-semantic-analysis/v1", workspace_id=self.workspace_id,
            invocation_id=self.invocation_id, investigation_id=self.investigation_id, outer_call_id=call.call_id,
            analysis_status="BLOCKED", errors=list(errors), proposals=[], context_manifest=built.manifest(),
            semantic_status="SEMANTIC_NOT_EVALUATED", publishable=False, cache_hit=False,
            result_artifact=None, execution=dict(kind=self.owner.policy.execution_kind,
                reserved_requests=int(self._exists(self.reservation_ref)), new_live_provider_requests=0,
                cost_usd=None, new_billable_cost_usd=None if self.owner.policy.execution_kind == 'LIVE' else 0.0))

    def _view(self, call_id, built, payload, artifact, cache_hit):
        request = build_request(built.context, self.owner.model_id)
        binding = dict(identity=self.identity, request_sha256=canonical_hash(request.to_dict()),
                       source_snapshot_hash=built.context.source_snapshot_hash)
        if payload["binding"] != binding:
            raise ValueError("semantic result binding changed")
        stored_request = self.store.read(payload["request_artifact"]["ref"],
                                         expected_sha256=payload["request_artifact"]["sha256"])
        reserved = self.store.read(self.reservation_ref)
        if (stored_request["binding"] != binding or reserved["binding"] != binding
                or stored_request["model_request"] != request.to_dict()
                or stored_request["outer_tool_call"]["call_id"] != payload["origin_call_id"]
                or reserved["origin_call_id"] != payload["origin_call_id"]):
            raise ValueError("semantic request receipt changed")
        response_ref = payload["response_artifact"]
        if response_ref:
            response = self.store.read(response_ref["ref"], expected_sha256=response_ref["sha256"])
            if response["binding"] != binding:
                raise ValueError("semantic response belongs to another request")
            if not response.get("exception") and canonical_hash(response["response"]) != payload["result"]["response_sha256"]:
                raise ValueError("semantic response hash changed")
        result = _restore_result(payload["result"], built.context, self.owner.model_id)
        live = self.owner.policy.execution_kind == 'LIVE'
        usage_key = 'provider_usage' if live else "imported_usage" if self.owner.policy.execution_kind == "REPLAY" else "simulated_usage"
        return _compact_evidence(dict(schema_version="task-semantic-analysis/v1", workspace_id=self.workspace_id,
            invocation_id=self.invocation_id, investigation_id=self.investigation_id, outer_call_id=call_id,
            origin_tool_call_id=payload["origin_call_id"], cache_hit=cache_hit,
            analysis_status=result.status, errors=list(result.errors), context_manifest=built.manifest(),
            proposals=_proposal_view(result, built.context), semantic_status="SEMANTIC_NOT_EVALUATED",
            publishable=False, result_artifact=artifact, execution=dict(kind=self.owner.policy.execution_kind,
                reserved_requests=1, adapter_requests=result.provider_calls,
                new_live_provider_requests=int(live and not cache_hit and result.provider_calls > 0),
                **{usage_key: result.usage.to_dict()}, cost_usd=None,
                new_billable_cost_usd=None if live else 0.0,
                request_sha256=result.request_sha256, response_sha256=result.response_sha256,
                provider_request_id=result.provider_request_id, provider_response_id=result.provider_response_id,
                original_receipt=payload["original_receipt"])))

    def execute(self, call):
        built = self._build()
        if (call.run_id != self.run_id or call.arguments != dict(workspace_id=self.workspace_id,
                investigation_id=self.investigation_id)):
            return self._blocked(call, built, ["SEMANTIC_CALL_IDENTITY_MISMATCH"])
        if not built.ready:
            return self._blocked(call, built, built.errors)
        request = build_request(built.context, self.owner.model_id)
        request_hash = canonical_hash(request.to_dict())
        binding = dict(identity=self.identity, request_sha256=request_hash,
                       source_snapshot_hash=built.context.source_snapshot_hash)
        exists = self._exists(self.reservation_ref)
        if exists:
            try:
                reserved = self.store.read(self.reservation_ref)
            except Exception:
                return self._blocked(call, built, ["SEMANTIC_OUTCOME_UNKNOWN"])
            if reserved["binding"] != binding:
                return self._blocked(call, built, ["SEMANTIC_RESERVATION_BINDING_CHANGED"])
            if self._exists(self.result_ref):
                payload = self.store.read(self.result_ref)
                artifact = self.store.write(self.result_ref, payload, kind="tool_result").to_dict()
                return self._view(call.call_id, built, payload, artifact, True)
            if not self._exists(self.response_ref):
                return self._blocked(call, built, ["SEMANTIC_OUTCOME_UNKNOWN"])
        else:
            if self.owner.policy.max_requests == 0:
                return self._blocked(call, built, ["SEMANTIC_REQUEST_BUDGET_EXHAUSTED"])
            if self.owner.policy.request_cost_upper_bound_usd > self.owner.policy.max_cost_usd:
                return self._blocked(call, built, ["SEMANTIC_COST_BUDGET_EXHAUSTED"])
            if self.owner.adapter is None:
                return self._blocked(call, built, ["SEMANTIC_PROVIDER_NOT_CONFIGURED"])

        reserved_here = False
        def guard(actual):
            nonlocal reserved_here
            if canonical_hash(actual.to_dict()) != request_hash:
                raise ValueError("semantic exact request changed")
            if not exists:
                path = self.store._path_for(self.reservation_ref)
                path.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive reservation, fsynced before any provider boundary.
                with path.open("x", encoding="utf8") as stream:
                    json.dump(dict(binding=binding, origin_call_id=call.call_id,
                        original_receipt=getattr(self.owner.adapter, "provenance", None)), stream, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                self.store.write(self.prefix + "/request.json", dict(binding=binding,
                    outer_tool_call=call.to_dict(), model_request=actual.to_dict()), kind="request")
                reserved_here = True
            return ProviderRequestAuthorization.approve(actual, guard_id="m3-host",
                reservation_id=self.invocation_id, estimated_cost_upper_bound_usd=self.owner.policy.request_cost_upper_bound_usd)

        def complete(actual):
            if exists:
                saved = self.store.read(self.response_ref)
                if saved["binding"] != binding:
                    raise ValueError("semantic response binding changed")
                if saved.get("exception"):
                    raise RuntimeError("recorded semantic provider exception")
                return ModelResponse.from_dict(saved["response"])
            try:
                response = self.owner.adapter.complete(actual)
            except Exception:
                self.store.write(self.response_ref, dict(binding=binding, exception=True), kind="response")
                raise
            self.store.write(self.response_ref, dict(binding=binding, response=response.to_dict()), kind="response")
            return response

        result = SemanticProposer(SimpleNamespace(complete=complete), model_id=self.owner.model_id,
            request_guard=guard, guard_id="m3-host").propose(built.context)
        if not self._exists(self.reservation_ref) or (not exists and not reserved_here):
            return self._blocked(call, built, result.errors)
        request_ref = self.prefix + "/request.json"
        request_artifact = self.store.write(request_ref, self.store.read(request_ref), kind="request").to_dict()
        response_artifact = (self.store.write(self.response_ref, self.store.read(self.response_ref), kind="response").to_dict()
                             if self._exists(self.response_ref) else None)
        payload = dict(binding=binding, origin_call_id=self.store.read(self.reservation_ref)["origin_call_id"],
                       request_artifact=request_artifact, response_artifact=response_artifact,
                       original_receipt=self.store.read(self.reservation_ref)["original_receipt"], result=result.to_dict())
        artifact = self.store.write(self.result_ref, payload, kind="tool_result").to_dict()
        return self._view(call.call_id, built, payload, artifact, exists)

    def review(self, value, *, call_id):
        """Read artifacts and live sources only; never call execute/Provider."""
        try:
            built = self._build()
            if (value["invocation_id"] != self.invocation_id or value["outer_call_id"] != call_id
                    or value["context_manifest"] != built.manifest()):
                raise ValueError("source or identity changed")
            artifact = value["result_artifact"]
            if artifact is None:
                errors = value["errors"]
                if (not isinstance(errors, list) or not errors or any(not isinstance(e, str) for e in errors)
                        or value != self._blocked(SimpleNamespace(call_id=call_id), built, errors)):
                    raise ValueError("blocked semantic diagnostic projection changed")
            else:
                if not built.ready or artifact["ref"] != self.result_ref:
                    raise ValueError("semantic source or receipt invalid")
                payload = self.store.read(artifact["ref"], expected_sha256=artifact["sha256"])
                if payload["binding"]["identity"] != self.identity:
                    raise ValueError("receipt belongs to another run")
                if self._view(call_id, built, payload, artifact, value["cache_hit"]) != value:
                    raise ValueError("semantic receipt projection changed")
        except Exception:
            return dict(status="fail", reason="semantic_source_receipt_or_evidence_changed")
        return dict(status="pass", result_type="task-semantic-analysis/v1",
                    checks=["INNER_RECEIPT_IDENTITY", "CURRENT_RAW_SOURCE_SNAPSHOT", "A_EVIDENCE_VALIDATION",
                            "DECLARED_SOURCE_COVERAGE", "SEMANTIC_NOT_EVALUATED"])
