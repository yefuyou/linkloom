"""Bounded Product Ask routing and presentation; all authority stays in existing services."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
import re
from threading import Lock
from typing import Any, Callable

from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.scope_discovery import AUTO_EMBEDDER, decision_slot_id, resolve_query_date
from linkloom.ui.question_routing import classify_task, route_question
from linkloom.ui.feedback_memory import FeedbackMemory
from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.ui.ask_admission import (admission_restriction, parse_context,
    source_context, validate_context)

_RELATION_QUESTION = re.compile(r"\b(owner|owns|leader|supervisor|alias)\b|\breports?\s+(?:directly\s+)?to\b|负责人|领导|汇报|别名", re.I)

@dataclass(frozen=True)
class _AskRun:
    query: str
    workspace_id: str
    scope: dict[str, Any]
    route: dict[str, Any]
    discovery: dict[str, Any] | None = None


class UnifiedAskService:
    def __init__(self, app: ProductApplication,
                 investigation: Callable[[], ProductInvestigationService | None], *,
                 slot_embedder: Any = AUTO_EMBEDDER) -> None:
        self.app = app
        self.investigation = investigation
        self.slot_embedder = slot_embedder
        self._runs: dict[str, tuple[_AskRun, ProductInvestigationService]] = {}
        self._lock = Lock()
        self.feedback_memory = (FeedbackMemory(app.repository.database_path + ".feedback-memory.jsonl")
                                if app is not None and app.repository.database_path != ":memory:" else None)

    def feedback_list(self, workspace_id: str) -> dict[str, Any]:
        with self.app._store_lock:
            self._check_workspace(workspace_id)
            if self.feedback_memory is None:
                raise ProductApplicationError("FEEDBACK_UNAVAILABLE", "Feedback needs a persistent product database.", status=503)
            return self.feedback_memory.listing(workspace_id)

    def feedback_action(self, action: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
        with self.app._store_lock:
            workspace_id = payload.get("workspace_id")
            self._check_workspace(workspace_id)
            if self.feedback_memory is None:
                raise ProductApplicationError("FEEDBACK_UNAVAILABLE", "Feedback needs a persistent product database.", status=503)
            return self.feedback_memory.action(action, payload, workspace_id,
                self.app.relation_resolver, self._trusted_slots(workspace_id))

    def config(self) -> dict[str, Any]:
        service = self.investigation()
        with self.app._store_lock:
            workspace_id = self.app._workspace_id()
            slots = self._trusted_slots(workspace_id)
            context = source_context(self.app, workspace_id)
        return {"workspace_id": workspace_id,
                "investigation_context": context,
                "scopes": [{"subject": slot["subject"], "relation": slot["relation"]} for slot in slots],
                "agent": service.config() if service else {
            "available": False, "external_provider": False, "run_mode": "MODEL_NOT_CONFIGURED",
            "message": "The investigation model is not configured.",
        }}

    def _trusted_slots(self, workspace_id: str) -> list[dict[str, Any]]:
        # Reuse the authority boundary, including receipt identity and evidence
        # verification. Raw store rows and pending proposals are never an index.
        records = self.app.store.list_decisions(workspace_id)
        slots = sorted({(normalize_temporal_component(record.subject),
                         normalize_temporal_component(record.relation))
                        for record in records
                        if record.subject and record.relation})
        trusted = []
        for subject, relation in slots:
            view = self.app.recover_decision(subject, relation, _workspace_id=workspace_id)
            history = view["history"]
            last_effective_at = max(record.valid_from for record in records
                                    if record.subject and record.relation
                                    and normalize_temporal_component(record.subject) == subject
                                    and normalize_temporal_component(record.relation) == relation)
            if last_effective_at > datetime.fromisoformat(view["as_of"]):
                # Approved scheduled slots are trusted identities too. Query
                # validity still uses the request date, never this index date.
                history = self.app.recover_decision(subject, relation, last_effective_at.isoformat(),
                                                   _workspace_id=workspace_id)["history"]
            if not history:
                continue
            trusted.append({"workspace_id": workspace_id, "reviewed": True,
                            "slot_id": decision_slot_id(history[-1]["subject"], history[-1]["relation"]),
                            "subject": history[-1]["subject"], "relation": history[-1]["relation"],
                            "values": list(dict.fromkeys(item["value"] for item in history)),
                            # Name existence is separate from decision authority.
                            # These verified proposal mentions are never indexed
                            # or offered as an approved value to the selector.
                            "reference_values": list(dict.fromkeys(item["value"] for item in view.get("proposals", [])
                                if item.get("evidence", {}).get("verified") is True)),
                            "decision_ids": [item["decision_id"] for item in history],
                            "current_value": (view.get("effective") or {}).get("value")})
        return trusted

    @staticmethod
    def _base(run: _AskRun) -> dict[str, Any]:
        return {"schema_version": "product-ask/v1", "query": run.query,
                "workspace_id": run.workspace_id, "scope": deepcopy(run.scope),
                "path": {"DECISION": "DETERMINISTIC", "CURRENT": "DETERMINISTIC",
                         "AS_OF": "DETERMINISTIC", "INVESTIGATION": "AGENT",
                         "SOURCE_RELATION": "SOURCE_RELATION_INVESTIGATION"}.get(run.route["intent"]),
                "route": dict(run.route, decided_by=run.route.get("decided_by", "rule")),
                "decided_by": run.route.get("decided_by", "rule"),
                "status": "NEEDS_SCOPE", "conclusions": [],
                "discovery": deepcopy(run.discovery),
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
                      {"intent": "CURRENT", "code": "REVIEWED_STATE_LOOKUP", "supported": False,
                       "reason": "Question routing has not completed.", "decided_by": "rule"})
        result = self._base(run)
        try:
            # Keep the current-workspace check and setup atomic with workspace selection.
            with self.app._store_lock:
                self._check_workspace(run.workspace_id)
                context = parse_context(payload.get("investigation_context"))
                restriction = admission_restriction(run.query)
                if restriction:
                    result["path"] = None
                    return self._failure(result, *restriction, status="NEEDS_SCOPE"), 200
                plan = plan_relation_investigation(run.query)
                if context:
                    validate_context(self.app, run.workspace_id, context)
                    if context["domain"] == "tasks":
                        # The explicit structured domain owns task wording.  A
                        # task question may mention an owner/leader naturally;
                        # only an advanced decision scope or an independently
                        # recognized relation plan is a real admission conflict.
                        if values["subject"] or values["relation"] or plan is not None:
                            result["path"] = None
                            return self._failure(result, "INVESTIGATION_DOMAIN_CONFLICT",
                                "Clear the advanced decision selection and choose the task domain for a task question.", status="NEEDS_SCOPE"), 200
                        return self._task_request(run, payload, context=context)
                if plan is not None or _RELATION_QUESTION.search(run.query):
                    return self._source_request(run, payload, plan)
                slots = self._trusted_slots(run.workspace_id)
                resolver, memory = self.app.relation_resolver, None
                if self.feedback_memory is not None and not (values["subject"] or values["relation"]):
                    resolver, memory = self.feedback_memory.snapshot(run.workspace_id, run.query, resolver, slots)
                # This point is deliberately AFTER the unchanged task/relation
                # dispatch. Selection reuses only the explicitly configured model.
                service = self.investigation()
                selector = None
                if (service is not None and service.model_factory is not None
                        and not getattr(service, "task_only", False)
                        and (not service.external_provider or payload.get("allow_external_provider") is True)
                        and not (memory and memory["conflicts"])):
                    selector = lambda: service.model_factory(run.query)
                routed = route_question(run.query, slots, resolver, workspace_id=run.workspace_id,
                    subject=values["subject"], relation=values["relation"],
                    as_of=payload.get("as_of"), provider_factory=selector, embedder=self.slot_embedder)
                discovery = routed["discovery"]
                if memory is not None:
                    discovery["memory"] = memory
                    if memory["conflicts"]:
                        discovery.update(status="AMBIGUOUS", scope=None, candidates=[], candidate_count=0, decided_by="fallback",
                            reason="Conflicting approved terminology. Revoke the conflict or choose an explicit reviewed decision.")
                        routed.update(route="CLARIFY", scope=None, decided_by="fallback")
                route = {key: routed[key] for key in
                         ("intent", "route", "code", "reason", "decided_by", "llm_attempted", "llm_error")}
                run = _AskRun(run.query, run.workspace_id, run.scope, route)
                result["route"] = route
                result["decided_by"] = routed["decided_by"]
                result["path"] = ({"CURRENT": "DETERMINISTIC", "AS_OF": "DETERMINISTIC",
                                   "INVESTIGATION": "AGENT"}.get(routed["route"]))
                result["discovery"] = discovery
                if discovery["scope"] is None:
                    result["path"] = None
                    return self._failure(result, "DECISION_SCOPE_" + discovery["status"],
                                         discovery["reason"], status="NEEDS_SCOPE"), 200
                values.update(discovery["scope"])
                result["scope"].update(discovery["scope"])
                as_of, date_error = resolve_query_date(run.query, payload.get("as_of"))
                if date_error:
                    result["path"] = None
                    return self._failure(result, date_error["code"], date_error["message"],
                                         status="NEEDS_SCOPE"), 200
                if not routed["supported"]:
                    return self._failure(result, "TASK_UNCLASSIFIED", run.route["reason"], status="NEEDS_SCOPE"), 200
                view = self.app.recover_decision(values["subject"], values["relation"], as_of, _workspace_id=run.workspace_id)
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
                pinned = _AskRun(run.query, run.workspace_id, dict(result["scope"]), run.route, discovery)
                with self._lock:
                    self._runs[snapshot["run"]["id"]] = (pinned, service)
                return self._from_snapshot(pinned, snapshot), 202
        except ProductApplicationError as error:
            return self._failure(result, error.code, str(error)), error.status

    def _task_request(self, run, payload, *, context=None):
        route = dict(intent='TASK_INVESTIGATION', code='SOURCE_TASK_INVESTIGATION',
                     reason='Bounded task investigation; observations and plans remain unconfirmed.')
        route['admission'] = dict(origin='USER_SCOPED' if context else 'RECORD_SHORTCUT',
                                  context=deepcopy(context))
        run = _AskRun(run.query, run.workspace_id, run.scope, route)
        result = self._base(run)
        result['path'] = 'TASK_INVESTIGATION'
        if run.scope.get('as_of') is not None:
            return self._failure(result, 'TASK_DATE_SCOPE_UNSUPPORTED',
                'Task investigation cannot bind the advanced decision date. Clear it and state the investigation period in the question.',
                category='validation', status='BLOCKED'), 200
        service = self.investigation()
        config = service.config() if service else {}
        if not config.get('available') or not config.get('task_investigation'):
            return self._failure(result, 'TASK_INVESTIGATION_UNAVAILABLE',
                config.get('task_provider', {}).get('setup', 'Configure task Provider stages in the Product Host.'),
                category='configuration', status='BLOCKED'), 200
        authorization = None
        if config.get('external_provider'):
            if payload.get('allow_external_provider') is not True:
                return self._failure(result, 'EXTERNAL_PROVIDER_PERMISSION_REQUIRED',
                    'Authorize this question to send the selected workspace source snapshot within the displayed budgets.',
                    category='permission', status='BLOCKED'), 403
            from linkloom.semantic_ingestion.task_relation_models import canonical_hash
            authorization = dict(workspace_id=run.workspace_id, snapshot_hash=context['snapshot_hash'],
                                 question_sha256=canonical_hash(run.query))
        try:
            snapshot = service.start(run.query, run.workspace_id, task_investigation=True,
                                     task_source_authorization=authorization)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) else getattr(exc, 'code', 'TASK_PREPARATION_FAILED')
            return self._failure(result, code, 'Task preparation stopped; no result is publishable.',
                category='validation', status='BLOCKED'), 200
        with self._lock:
            if snapshot['kind'] == 'error':
                return self._from_snapshot(run, snapshot), 200
            self._runs[snapshot['run']['id']] = (run, service)
        service.save_task_ask(snapshot['run']['id'], dict(query=run.query, workspace_id=run.workspace_id,
            scope=run.scope, route=run.route, discovery=run.discovery))
        return self._from_snapshot(run, snapshot), 202

    def _source_request(self, run, payload, plan):
        route = {"intent": "SOURCE_RELATION", "code": "SOURCE_RELATION_MULTIHOP",
                 "reason": "A bounded directed source-relation chain; no business approval is implied.",
                 "plan": plan.to_dict() if plan else None}
        run = _AskRun(run.query, run.workspace_id, run.scope, route)
        result = self._base(run)
        if plan is None:
            return self._failure(result, "UNSUPPORTED_QUERY",
                "This relationship question cannot be reliably decomposed. Supported paths: project → owner → direct leader; alias → project → owner → direct leader.", status="NEEDS_SCOPE"), 200
        if run.scope["subject"] or run.scope["relation"]:
            return self._failure(result, "TASK_SCOPE_CONFLICT",
                "A source-relation question cannot use an approved decision slot. Clear the advanced decision selection or clarify the question.", status="NEEDS_SCOPE"), 200
        as_of, error = resolve_query_date(run.query, payload.get("as_of"))
        if error:
            return self._failure(result, error["code"], error["message"], status="NEEDS_SCOPE"), 200
        if as_of and re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of) is None:
            return self._failure(result, "DATE_SCOPE_REQUIRED", "Source relationships require an exact snapshot date, without a time of day.", status="NEEDS_SCOPE"), 200
        runtime_query = run.query
        if as_of and not re.search(r"\d{4}-\d{2}-\d{2}", run.query):
            runtime_query = f"As of {as_of}, {run.query}"
            plan = plan_relation_investigation(runtime_query)
        if plan is None:
            return self._failure(result, "UNSUPPORTED_QUERY", "The dated relationship plan exceeds the supported bounds.", status="NEEDS_SCOPE"), 200
        route["plan"] = plan.to_dict()
        route["relations"] = [goal.relation_path[0] for goal in plan.subgoals]
        result["scope"] = {"subject": "", "relation": "", "as_of": as_of}
        service = self.investigation()
        config = service.config() if service else {}
        route.update(requires_external_provider=config.get("external_provider", False),
                     external_provider_authorized=payload.get("allow_external_provider") is True,
                     can_start=bool(config.get("available")) and (not config.get("external_provider") or payload.get("allow_external_provider") is True))
        result["route"] = dict(route)
        if not config.get("available"):
            return self._failure(result, "AGENT_NOT_CONFIGURED", "No investigation model is configured.", category="configuration"), 503
        if not route["can_start"]:
            return self._failure(result, "EXTERNAL_PROVIDER_PERMISSION_REQUIRED", "Allow this request before sources are sent or model costs are incurred.", category="permission"), 403
        try:
            snapshot = service.start(runtime_query, run.workspace_id, task_plan=plan)
        except ProductApplicationError as error:
            return self._failure(result, error.code, str(error)), error.status
        except Exception:
            return self._failure(result, "INVESTIGATION_START_FAILED", "The bounded source investigation could not start.", category="runtime"), 500
        pinned = _AskRun(run.query, run.workspace_id, dict(result["scope"]), route)
        with self._lock:
            self._runs[snapshot["run"]["id"]] = (pinned, service)
        return self._from_snapshot(pinned, snapshot), 202

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
        if run.route['intent'] == 'TASK_INVESTIGATION':
            result.update(path='TASK_INVESTIGATION', agent_run_id=snapshot['run']['id'],
                status='INVESTIGATING' if snapshot['kind'] == 'running' else 'BLOCKED',
                metrics=deepcopy(snapshot.get('metrics', {})), trace=deepcopy(snapshot.get('trace', [])),
                preparation=deepcopy(snapshot.get('preparation')), error=deepcopy(snapshot.get('error')),
                working_context_audit=deepcopy(snapshot.get('working_context_audit')))
            review = snapshot.get('reviewer', {})
            result['reviewer'] = dict(review, accepted=review.get('decision') == 'evidence_sufficient')
            if snapshot.get('task_investigation') and result['reviewer']['accepted']:
                task_result = snapshot['task_investigation']
                analysis = task_result.get('semantic_analysis') or {}
                # An executed semantic batch may contain both evidence-valid
                # candidates and blocked/insufficient proposals. Keep the
                # candidate layer visible as PARTIAL; only a provider/receipt
                # failure should close the whole product result.
                release_status = ('PARTIAL' if (task_result.get('investigation_status') == 'BLOCKED'
                                                and analysis.get('analysis_status') == 'EXECUTED')
                                  else ('BLOCKED' if task_result.get('investigation_status') == 'BLOCKED'
                                        else (snapshot.get('preparation') or {}).get('status', 'PARTIAL')))
                result.update(status=release_status, task_investigation=deepcopy(task_result),
                    evidence=deepcopy(snapshot['evidence']), uncertainty=deepcopy(snapshot['uncertainty']),
                    task_review=deepcopy(snapshot.get('task_review')))
                if snapshot.get('recovery'):
                    result['recovery'] = deepcopy(snapshot['recovery'])
            return result
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
            if run.route["intent"] == "SOURCE_RELATION":
                source = snapshot.get("source_investigation")
                if not result["reviewer"]["accepted"] or source is None:
                    return self._failure(result, "FINAL_NOT_VERIFIED", "The source relation Final was not accepted.", category="validation")
                result.update(status="COMPLETED" if kind == "success" else "INSUFFICIENT",
                    source_investigation=deepcopy(source), metrics=deepcopy(snapshot["metrics"]),
                    uncertainty=deepcopy(snapshot["uncertainty"]), conclusions=[{"kind": "SOURCE_RELATION", **fact} for fact in source["facts"]])
                result["evidence"] = [{"evidence_ref": e["ref"], "citation_id": e["id"],
                    "source_name": e["source_name"], "source_id": e["source_id"],
                    "source_version": e["source_version"], "line_start": e["line_start"], "line_end": e["line_end"],
                    "quote": e["source"]["quote"], "content_sha256": e["content_sha256"],
                    "quote_sha256": e["quote_sha256"], "verified": True} for e in snapshot["evidence"]]
                return result
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
            with self.app._store_lock:
                self._check_workspace(workspace_id)
                service = self.investigation()
                saved = service.read_saved_task(run_id, workspace_id) if service else None
                if saved is None:
                    raise ProductApplicationError("RUN_NOT_FOUND", "The unified Ask run was not found.", status=404)
                data, snapshot = saved
                return self._from_snapshot(_AskRun(**data), snapshot), 200
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
