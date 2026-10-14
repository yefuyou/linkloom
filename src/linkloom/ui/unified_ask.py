"""Bounded Product Ask routing and presentation; all authority stays in existing services."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from threading import Lock
from typing import Any, Callable

from linkloom.ui.product import ProductApplication, ProductApplicationError, _parse_as_of
from linkloom.ui.investigation import ProductInvestigationService

# Supported task families, not general intent understanding. Investigation wins
# for mixed questions so a current-state lookup cannot masquerade as an explanation.
_INVESTIGATION = re.compile(
    r"\b(why|reason|rationale|explain|investigat\w*|conflict\w*|contradict\w*|"
    r"compare|difference\w*|distinguish|uncertain\w*|unverified)\b|"
    r"sources?\s+support|original\s+(sources?|evidence)|"
    r"为什么|为何|原因|调查|冲突|矛盾|区别|差异|比较|无法证实|未证实|不确定|原始来源|支持.*变更",
    re.IGNORECASE,
)
_DECISION = re.compile(
    r"\b(current\w*|now|approved|adopted|effective|receipt\w*|valid|formal)\b|"
    r"\bwhat\b.*\b(using|use|supplier|decision)\b|show.*\b(sources?|evidence)\b|"
    r"当前|现行|正式|采用|生效|审核|审批|收据|查看.*来源",
    re.IGNORECASE,
)


def classify_task(query: str) -> dict[str, str]:
    if _INVESTIGATION.search(query):
        return {"intent": "INVESTIGATION", "code": "SOURCE_INVESTIGATION",
                "reason": "Explanation, comparison or uncertainty requires source investigation."}
    if _DECISION.search(query):
        return {"intent": "DECISION", "code": "REVIEWED_STATE_LOOKUP",
                "reason": "Reviewed state, validity and receipts can be queried directly."}
    return {"intent": "UNKNOWN", "code": "TASK_UNCLASSIFIED",
            "reason": "This bounded router cannot reliably classify the task. Ask for a reviewed decision, effective date, receipt, reason or source conflict."}


@dataclass(frozen=True)
class _AskRun:
    query: str
    workspace_id: str
    scope: dict[str, Any]
    route: dict[str, str]


class UnifiedAskService:
    def __init__(self, app: ProductApplication,
                 investigation: Callable[[], ProductInvestigationService | None]) -> None:
        self.app = app
        self.investigation = investigation
        self._runs: dict[str, tuple[_AskRun, ProductInvestigationService]] = {}
        self._lock = Lock()

    def config(self) -> dict[str, Any]:
        service = self.investigation()
        with self.app._store_lock:
            workspace_id = self.app._workspace_id()
            slots = sorted({(record.subject, record.relation)
                            for record in self.app.store.list_decisions(workspace_id)
                            if record.subject and record.relation})
        return {"workspace_id": workspace_id,
                "scopes": [{"subject": subject, "relation": relation} for subject, relation in slots],
                "agent": service.config() if service else {
            "available": False, "external_provider": False, "run_mode": "MODEL_NOT_CONFIGURED",
            "message": "The investigation model is not configured.",
        }}

    @staticmethod
    def _base(run: _AskRun) -> dict[str, Any]:
        return {"schema_version": "product-ask/v1", "query": run.query,
                "workspace_id": run.workspace_id, "scope": deepcopy(run.scope),
                "path": {"DECISION": "DETERMINISTIC", "INVESTIGATION": "AGENT"}.get(run.route["intent"]),
                "route": dict(run.route), "status": "NEEDS_SCOPE", "conclusions": [],
                "evidence": [], "receipts": [], "uncertainty": {"unknown_fields": [], "statement": ""},
                "agent_run_id": None, "reviewer": {"performed": False, "accepted": False},
                "timeline": None, "progress": None, "error": None}

    @staticmethod
    def _failure(result: dict[str, Any], code: str, message: str, *, category: str = "request",
                 status: str = "FAILED") -> dict[str, Any]:
        result["status"] = status
        result["error"] = {"code": code, "category": category, "message": message}
        return result

    def _check_workspace(self, workspace_id: str) -> None:
        if not workspace_id:
            raise ProductApplicationError("WORKSPACE_REQUIRED", "Select a workspace first.", status=409)
        if workspace_id != self.app._workspace_id():
            raise ProductApplicationError("WORKSPACE_MISMATCH", "The selected workspace changed. Ask again in the current workspace.", status=409)

    def ask(self, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        query = payload.get("query")
        if not isinstance(query, str) or not query.strip() or len(query.strip()) > 4000:
            raise ProductApplicationError("INVALID_QUERY", "Enter a question of at most 4000 characters.")
        values = {}
        for key, limit in (("workspace_id", 128), ("subject", 512), ("relation", 512)):
            value = payload.get(key)
            if value is not None and (not isinstance(value, str) or len(value) > limit):
                raise ProductApplicationError("INVALID_SCOPE", "Workspace and decision scope must be bounded text.")
            values[key] = (value or "").strip()
        run = _AskRun(query.strip(), values["workspace_id"],
                      {"subject": values["subject"], "relation": values["relation"], "as_of": payload.get("as_of") or None},
                      classify_task(query.strip()))
        result = self._base(run)
        try:
            # Keep the current-workspace check and setup atomic with workspace selection.
            with self.app._store_lock:
                self._check_workspace(run.workspace_id)
                timestamp = _parse_as_of(payload.get("as_of"))
                if not values["subject"] or not values["relation"]:
                    return self._failure(result, "DECISION_SCOPE_REQUIRED", "Select the decision subject and relation.", status="NEEDS_SCOPE"), 200
                dates = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", query)
                if dates and (timestamp is None or any(day != timestamp.date().isoformat() for day in dates)):
                    return self._failure(result, "DATE_SCOPE_REQUIRED", "Match the selected valid-time date to the date in your question.", status="NEEDS_SCOPE"), 200
                if run.route["intent"] == "UNKNOWN":
                    return self._failure(result, "TASK_UNCLASSIFIED", run.route["reason"], status="NEEDS_SCOPE"), 200
                view = self.app.recover_decision(values["subject"], values["relation"], payload.get("as_of"), _workspace_id=run.workspace_id)
                result["scope"]["as_of"] = view["as_of"]
                if result["path"] == "DETERMINISTIC":
                    result["status"] = "COMPLETED"
                    self._verified_view(result, view)
                    return result, 200
                service = self.investigation()
                if service is None or not service.config()["available"]:
                    return self._failure(result, "AGENT_NOT_CONFIGURED", "This question requires investigation, but no model is configured.", category="configuration"), 503
                if service.config()["external_provider"] and payload.get("allow_external_provider") is not True:
                    return self._failure(result, "EXTERNAL_PROVIDER_PERMISSION_REQUIRED", "Allow this bounded external investigation before source text is sent or model costs are incurred.", category="permission"), 403
                try:
                    snapshot = service.start_temporal(run.query, run.workspace_id, subject=values["subject"], relation=values["relation"], as_of=view["as_of"])
                except ProductApplicationError:
                    raise
                except Exception:
                    return self._failure(result, "INVESTIGATION_START_FAILED", "The configured investigation could not start.", category="runtime"), 500
                pinned = _AskRun(run.query, run.workspace_id, dict(result["scope"]), run.route)
                with self._lock:
                    self._runs[snapshot["run"]["id"]] = (pinned, service)
                return self._from_snapshot(pinned, snapshot), 202
        except ProductApplicationError as error:
            return self._failure(result, error.code, str(error)), error.status

    @staticmethod
    def _verified_view(result: dict[str, Any], view: dict[str, Any]) -> None:
        result["timeline"] = deepcopy(view)
        effective = view.get("effective")
        if effective:
            result["conclusions"] = [{"kind": "REVIEWED_DECISION", "value": effective["value"],
                                      "decision_id": effective["decision_id"], "effective_at": effective["valid_from"]}]
            result["evidence"] = deepcopy(effective["evidence"])
            result["receipts"] = [dict(effective["authorization"], decision_id=effective["decision_id"])]
        if view.get("transitions"):
            result["uncertainty"] = {"unknown_fields": ["replacement_reason"],
                                     "statement": "The formal replacement is verified; its reason is not source-verified."}
        if not effective:
            result["uncertainty"] = {"unknown_fields": ["effective_decision"],
                                     "statement": "No uniquely authorized, source-verified effective decision: " + view["status"]}

    def _from_snapshot(self, run: _AskRun, snapshot: dict[str, Any]) -> dict[str, Any]:
        result = self._base(run)
        result["agent_run_id"] = snapshot["run"]["id"]
        result["progress"] = snapshot.get("progress")
        result["metrics"] = snapshot.get("metrics", {})
        disposition = snapshot.get("reviewer", {})
        result["reviewer"] = {key: deepcopy(disposition.get(key)) for key in
                              ("review_id", "reviewer_agent_id", "decision", "checks", "reason", "created_at")}
        result["reviewer"].update(performed=disposition.get("performed") is True,
                                   accepted=disposition.get("performed") is True and disposition.get("decision") == "evidence_sufficient")
        kind = snapshot["kind"]
        if kind == "running":
            result["status"] = "INVESTIGATING"
            return result
        if kind in {"success", "insufficient"}:
            view = snapshot.get("temporal_decision")
            if not result["reviewer"]["accepted"] or not view or view.get("status") != "FOUND":
                return self._failure(result, "FINAL_NOT_VERIFIED", "No temporal final was accepted by Validator and Reviewer.", category="validation")
            result["status"] = "COMPLETED"
            self._verified_view(result, view)
            # Keep every accepted Agent citation, including supporting sources
            # outside the formal decision receipt. Snapshot projection already
            # checked these references against observed evidence.
            for item in snapshot.get("evidence", []):
                citation = {"evidence_ref": item["ref"], "citation_id": item["id"],
                            "source_name": item.get("source_name", item["relative_path"]),
                            "source_id": item.get("source_id"), "source_version": item.get("source_version"),
                            "line_start": item["line_start"], "line_end": item["line_end"],
                            "quote": item.get("source", {}).get("quote", ""), "verified": True}
                existing = next((evidence for evidence in result["evidence"]
                                 if evidence.get("source_id") == citation["source_id"]
                                 and evidence.get("source_version") == citation["source_version"]
                                 and evidence.get("line_start") == citation["line_start"]
                                 and evidence.get("line_end") == citation["line_end"]
                                 and evidence.get("quote") == citation["quote"]), None)
                if existing is not None:
                    existing["citation_id"] = citation["citation_id"]
                    existing["agent_evidence_ref"] = citation["evidence_ref"]
                else:
                    result["evidence"].append(citation)
            result["rationale"] = deepcopy(snapshot.get("rationale", []))
            result["uncertainty"] = deepcopy(snapshot.get("uncertainty") or result["uncertainty"])
            return result
        error = snapshot.get("error") or {}
        uncertain = error.get("classification") == "provider_outcome_uncertain"
        result["uncertainty"] = {"unknown_fields": ["investigation_conclusion"],
                                 "statement": "Investigation interrupted; no final conclusion was verified."}
        return self._failure(result, error.get("code", "INVESTIGATION_STOPPED"),
                             error.get("message", "The investigation stopped before final verification."),
                             category=error.get("classification", "runtime"),
                             status="PROVIDER_UNCERTAIN" if uncertain else "FAILED")

    def inspect(self, run_id: str, workspace_id: str) -> tuple[dict[str, Any], int]:
        with self._lock:
            entry = self._runs.get(run_id)
        if entry is None:
            raise ProductApplicationError("RUN_NOT_FOUND", "The unified Ask run was not found.", status=404)
        run, service = entry
        with self.app._store_lock:
            # Check before reading/projecting any evidence from an old workspace.
            self._check_workspace(workspace_id)
            if run.workspace_id != workspace_id:
                raise ProductApplicationError("WORKSPACE_MISMATCH", "This run belongs to another workspace.", status=409)
            snapshot = service.inspect(run_id)
            if snapshot is None:
                raise ProductApplicationError("RUN_NOT_FOUND", "The investigation run was not found.", status=404)
            result = self._from_snapshot(run, snapshot)
        failure_status = {"provider_outcome_uncertain": 503, "provider_failure": 502, "tool_policy_denied": 403}
        status = (failure_status.get((result.get("error") or {}).get("category"), 500)
                  if result["status"] in {"FAILED", "PROVIDER_UNCERTAIN"} else 200)
        return result, status
