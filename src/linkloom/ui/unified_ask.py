"""Bounded Product Ask routing and presentation; all authority stays in existing services."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import os
import re
from threading import Lock
from typing import Any, Callable

from linkloom.fact_memory import fact_memory_enabled
from linkloom.ui.product import ProductApplication, ProductApplicationError
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.scope_discovery import AUTO_EMBEDDER, decision_slot_id, resolve_query_date
from linkloom.ui.question_routing import classify_task, route_question
from linkloom.ui.feedback_memory import FeedbackMemory
from linkloom.ui.structured_router import rule_date_blocker, rule_needs_router
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
                 graph_service=None, router_provider=None,
                 slot_embedder: Any = AUTO_EMBEDDER) -> None:
        self.app = app
        self.investigation = investigation
        # Beta: startup switch; off keeps the legacy Ask path and creates no worker.
        if os.environ.get("LINKLOOM_GRAPH_QUERY", "on").strip().casefold() == "off":
            graph_service = False
        if graph_service is None and app is not None and app.repository.database_path != ":memory:":
            from linkloom.ui.graph_investigation import product_graph_service
            graph_service = product_graph_service(app, investigation)
        self.graph_service = graph_service if graph_service is not False else None
        self.router_provider = router_provider
        self.slot_embedder = slot_embedder
        self._runs: dict[str, tuple[_AskRun, ProductInvestigationService]] = {}
        self._lock = Lock()
        self._graph_runs = {}
        self._graph_fallbacks: dict[str, dict[str, str]] = {}
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
        result = {"workspace_id": workspace_id,
                "investigation_context": context,
                "scopes": [{"subject": slot["subject"], "relation": slot["relation"]} for slot in slots],
                "agent": service.config() if service else {
            "available": False, "external_provider": False, "run_mode": "MODEL_NOT_CONFIGURED",
            "message": "The investigation model is not configured.",
        }}
        if self.router_provider is not None:
            result["router"] = {"available": self.router_provider.adapter is not None,
                                "external_provider": self.router_provider.external_provider,
                                "mode": self.router_provider.mode}
        return result

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
        result = {"schema_version": "product-ask/v1", "query": run.query,
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
        if "router" in run.route:
            result["router"] = deepcopy(run.route["router"])
        return result

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
        security = {}
        if self.graph_service is None and self.router_provider is None:
            result, status = self._ask(payload, security=security)
        else:
            from linkloom.ui.graph_investigation import graph_request_budget
            with graph_request_budget():
                result, status = self._ask(payload, security=security)
        if security:
            result["security_gate"] = security
        return result, status

    def _classify_security(self, run, payload):
        from linkloom.ui.security_gate import classify, request_for
        from linkloom.ui.structured_router import RouterInvalid
        from linkloom.ui.lifecycle import BackgroundWorkStopped
        from time import monotonic
        provider = self.router_provider
        result = dict(status="INCOMPLETE", attempted=False)
        if provider.security_adapter is None:
            return dict(result, fallback_reason="MODEL_UNAVAILABLE")
        if provider.external_provider and payload.get("allow_external_provider") is not True:
            return dict(result, fallback_reason="PROVIDER_PERMISSION_REQUIRED")
        owner = self.investigation()
        if provider.external_provider and owner is None:
            return dict(result, fallback_reason="MODEL_UNAVAILABLE")
        snapshot = source_context(self.app, run.workspace_id)["snapshot_hash"]
        def validate():
            self._check_workspace(run.workspace_id)
            if source_context(self.app, run.workspace_id)["snapshot_hash"] != snapshot:
                raise ProductApplicationError("INVESTIGATION_CONTEXT_CHANGED", "Refresh the selected sources.", status=409)
        session = None
        started = monotonic()
        try:
            request = request_for(run.query, run.workspace_id)
            result["attempted"] = True
            if provider.external_provider:
                with owner._starts.operation(request.run_id):
                    session = provider.reserve(self.app, owner, request, validate)
                    decision = classify(session.bind(provider.security_adapter, "ask-security"), request)
            else:
                decision = classify(provider.security_adapter, request)
            result.update(status="COMPLETED", **decision)
        except (RouterInvalid, BackgroundWorkStopped, OSError, ValueError) as error:
            result["fallback_reason"] = str(error) if isinstance(error, RouterInvalid) else "PROVIDER_UNAVAILABLE"
        finally:
            if session is not None:
                session.close(timeout=0)
            result["seconds"] = round(monotonic() - started, 4)
        validate()
        return result

    def _ask(self, payload: dict[str, Any], *, use_graph: bool = True, use_router: bool = True,
             allow_selector: bool = True, security=None) -> tuple[dict[str, Any], int]:
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
                # Only the public Ask entry supplies this state. Internal graph/router
                # fallbacks re-enter _ask without it and cannot classify twice.
                if security is not None and self.router_provider is not None and (
                        self.router_provider.security_adapter is not None or self.router_provider.external_provider):
                    if context:
                        validate_context(self.app, run.workspace_id, context)
                    security.update(self._classify_security(run, payload))
                    if security.get("decision") == "BLOCK":
                        blocked, status = self._router_stop(run, None, security["reason_code"])
                        blocked["router"]["source"] = blocked["decided_by"] = "llm"
                        return blocked, status
                ordinary_router = (use_router and self.router_provider is not None and not context
                                   and not (values["subject"] or values["relation"]))
                cascade = ordinary_router and self.router_provider.mode == "cascade"
                if ordinary_router:
                    from linkloom.ui.ask_admission import router_safety_reason
                    safety = router_safety_reason(run.query)
                    if safety:
                        return self._router_stop(run, None, safety)
                if ordinary_router and not cascade:
                    return self._route_ordinary(run, payload)
                restriction = admission_restriction(run.query)
                if restriction:
                    if cascade:
                        from linkloom.ui.ask_admission import legacy_denial_reason
                        return self._router_stop(run, None, legacy_denial_reason(run.query))
                    result["path"] = None
                    return self._failure(result, *restriction, status="NEEDS_SCOPE"), 200
                if cascade:
                    from linkloom.ui.router_context import blocked_entity_reason
                    blocker = blocked_entity_reason(run.query, self._router_blocked_entities(run.workspace_id))
                    if blocker:
                        return self._router_stop(run, None, blocker)
                if use_graph and not cascade and self.graph_service is not None and not context and not (values['subject'] or values['relation']):
                    routed_graph = self._run_graph_request(run, payload)
                    if routed_graph is not None:
                        return routed_graph
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
                if plan is not None or (not cascade and _RELATION_QUESTION.search(run.query)):
                    if cascade:
                        from linkloom.ui.router_context import router_query_date
                        _, time_error = router_query_date(run.query, payload.get("as_of"))
                        invalid_date = rule_date_blocker(time_error, has_scope=False)
                        if invalid_date:
                            return self._router_stop(run, None, invalid_date)
                    return self._source_request(run, payload, plan)
                slots = self._trusted_slots(run.workspace_id)
                resolver, memory = self.app.relation_resolver, None
                if self.feedback_memory is not None and not (values["subject"] or values["relation"]):
                    resolver, memory = self.feedback_memory.snapshot(run.workspace_id, run.query, resolver, slots)
                # This point is deliberately AFTER the unchanged task/relation
                # dispatch. Selection reuses only the explicitly configured model.
                service = self.investigation()
                selector = None
                if (allow_selector and not cascade and service is not None and service.model_factory is not None
                        and not getattr(service, "task_only", False)
                        and (not service.external_provider or payload.get("allow_external_provider") is True)
                        and not (memory and memory["conflicts"])):
                    from linkloom.ui.graph_investigation import bind_graph_fallback
                    selector = lambda: bind_graph_fallback(service.model_factory(run.query), 'legacy-selector')
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
                date_blocker = None
                if cascade:
                    from linkloom.ui.router_context import router_query_date
                    _, time_error = router_query_date(run.query, payload.get("as_of"))
                    date_blocker = rule_date_blocker(time_error, has_scope=bool(routed["scope"]))
                if cascade and rule_needs_router(routed["route"], blocker=date_blocker):
                    return self._route_ordinary(run, payload)
                if date_blocker:
                    result["router"] = dict(source="host", reason_code=date_blocker, reason_class="CAPABILITY",
                                            decision="CLARIFY", attempted=False)
                route = {key: routed[key] for key in
                         ("intent", "route", "code", "reason", "decided_by", "llm_attempted", "llm_error")}
                run = _AskRun(run.query, run.workspace_id, run.scope, route)
                result["route"] = route
                result["decided_by"] = routed["decided_by"]
                result["path"] = ({"CURRENT": "DETERMINISTIC", "AS_OF": "DETERMINISTIC",
                                   "INVESTIGATION": "AGENT"}.get(routed["route"]))
                result["discovery"] = discovery
                if discovery["scope"] is None:
                    if date_blocker:
                        return self._router_stop(run, None, date_blocker)
                    if (fact_memory_enabled() and discovery["status"] == "NO_MATCH"
                            and not (values["subject"] or values["relation"])):
                        # Only after the unchanged decision discovery found nothing.
                        fact = self._fact_request(run, result, payload)
                        if fact is not None:
                            return fact
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
                return self._decision_request(run, result, payload, values, as_of)
        except ProductApplicationError as error:
            if use_router and self.router_provider is not None:
                reason = {"WORKSPACE_REQUIRED": "WORKSPACE_DENIED", "WORKSPACE_MISMATCH": "WORKSPACE_DENIED",
                          "INVESTIGATION_CONTEXT_CHANGED": "EVIDENCE_INVALID"}.get(error.code)
                if reason:
                    return self._router_stop(run, None, reason, http=error.status)
            return self._failure(result, error.code, str(error)), error.status

    def _fact_request(self, run, result, payload):
        """Answer from reviewed fact records; None leaves the existing NO_MATCH reply."""
        from linkloom.fact_memory.ask import answer
        from linkloom.ui.product import _parse_as_of
        records = self.app.fact_records(run.workspace_id)
        if not records:
            return None
        as_of = _parse_as_of(payload.get("as_of")) or datetime.now(UTC)
        service = self.investigation()
        factory = None
        if service is not None and service.model_factory is not None and not getattr(service, "task_only", False):
            if service.external_provider and payload.get("allow_external_provider") is not True:
                return self._failure(result, "EXTERNAL_PROVIDER_PERMISSION_REQUIRED", "Allow this bounded external investigation before source text is sent or model costs are incurred.", category="permission"), 403
            factory = lambda: service.model_factory(run.query)
        outcome = answer(run.query, as_of, records, factory)
        if outcome["status"] == "NO_MATCH":
            return None
        result.update(path="FACT", scope=dict(result["scope"], as_of=as_of.isoformat()))
        result["route"] = dict(result["route"], code=outcome["code"])
        result["evidence"] = [{"fact_id": record.fact_id, "subject": record.subject_label,
                               "attribute": record.attribute_name, "attribute_key": record.attribute_key,
                               "value": record.value_text, "effective_at": record.effective_at.isoformat(),
                               "supersedes_fact_id": record.supersedes_fact_id,
                               "superseded_by_fact_id": record.superseded_by_fact_id,
                               "evidence_sentence": record.evidence_sentence,
                               "quote": record.quote, "evidence_ref": record.evidence_ref,
                               "source_id": record.artifact_id, "source_version": record.artifact_version_id}
                              for record in outcome["cited"]]
        result["facts_offered"] = outcome["offered"]
        if outcome["status"] == "ANSWERED":
            result["status"] = "COMPLETED"
            result["conclusions"] = [{"kind": "FACT_ANSWER", "value": outcome["answer"],
                                      "fact_ids": [record.fact_id for record in outcome["cited"]]}]
            return result, 200
        if outcome["status"] == "UNKNOWN":
            result["status"] = "INSUFFICIENT"
            result["uncertainty"] = {"unknown_fields": ["value"],
                                     "statement": "The reviewed facts do not answer this question."}
            return result, 200
        return self._failure(result, outcome["code"], "The fact answer could not be verified.", category="runtime"), 200

    def _decision_request(self, run, result, payload, values, as_of):
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
        pinned = _AskRun(run.query, run.workspace_id, dict(result["scope"]), run.route, result.get("discovery"))
        with self._lock:
            self._runs[snapshot["run"]["id"]] = (pinned, service)
        return self._from_snapshot(pinned, snapshot), 202

    def _run_graph_request(self, run, payload):
        configured = self.investigation()
        permission = payload.get('allow_external_provider') is True
        if configured is not None and configured.external_provider and not permission:
            return None
        graph = self.graph_service.run(run.query, run.workspace_id, as_of=run.scope['as_of'],
                                       allow_external_provider=permission)
        result = self._base(run)
        if graph['status'] == 'FALLBACK':
            return None
        if graph['status'] == 'UNAUTHORIZED':
            return self._failure(result, graph['reason'], 'Graph permission is unavailable.', category='permission'), 403
        if graph['status'] in {'BLOCKED', 'REFUSED'}:
            return self._failure(result, graph['reason'], 'The graph request could not be verified.', status='NEEDS_SCOPE'), 200
        with self._lock:
            self._graph_runs[graph['run_id']] = run
        if graph.get('graph_status') == 'AMBIGUOUS' and any(
            step.get('reason_code') == 'CONFIRMED_CANDIDATES_NEED_CLARIFICATION'
            for step in graph['source_investigation']['graph_query']['steps']
        ):
            return self._graph_result(run, graph), 200
        if graph['status'] == 'INSUFFICIENT' and graph.get('graph_status') in {'UNKNOWN', 'AMBIGUOUS', 'NOT_FOUND'}:
            return self._try_graph_fallback(run, payload, graph)
        return self._graph_result(run, graph), 200

    def _router_blocked_entities(self, workspace_id, slots=None):
        from linkloom.ui.structured_router import normalize_mention
        # Use workspace names already exposed by the workspace selector.
        # Never enumerate or inspect decision/task records from another workspace.
        slots = self._trusted_slots(workspace_id) if slots is None else slots
        known = {normalize_mention(name) for slot in slots
                 for name in (slot["subject"], *slot.get("subject_aliases", []))}
        other_workspaces = ((item["name"], "WORKSPACE_DENIED")
                            for item in self.app.repository.list_workspaces()
                            if item["workspace_id"] != workspace_id)
        proposals = ((item["subject"], "NO_CONFIRMED_RECORD") for item in self.app._supporting_proposals()
                     if item["subject"] and item["source"]["active"])
        return tuple(sorted({(name, reason) for name, reason in (*other_workspaces, *proposals)
                             if normalize_mention(name) not in known},
                            key=lambda fact: (fact[1] != "WORKSPACE_DENIED", fact[0])))

    def _router_context(self, run, payload, snapshot_hash, *, query_time=None):
        from linkloom.ui.router_context import build_context
        from linkloom.ui.structured_router import RouterInvalid
        slots = self._trusted_slots(run.workspace_id)
        resolver = self.app.relation_resolver
        if self.feedback_memory is not None:
            resolver, memory = self.feedback_memory.snapshot(run.workspace_id, run.query, resolver, slots)
            if memory['conflicts']:
                raise RouterInvalid('ROUTER_FEEDBACK_CONFLICT')
        graph = self.graph_service
        catalogue = graph.store_factory(run.workspace_id).load_edges(run.workspace_id, None) if graph else None
        return build_context(run.query, run.workspace_id, slots, resolver,
            view=lambda subject, relation, as_of: self.app.recover_decision(subject, relation, as_of,
                                                                          _workspace_id=run.workspace_id),
            graph_snapshot=catalogue, selected_as_of=payload.get('as_of'), query_time=query_time,
            source_snapshot=snapshot_hash, embedder=self.slot_embedder, graph_available=graph is not None,
            blocked_entities=self._router_blocked_entities(run.workspace_id, slots),
            proposals=tuple(item for item in self.app._supporting_proposals()
                            if item.get("source", {}).get("active")))

    def _legacy_router_fallback(self, run, payload, reason, prior=None):
        result, status = self._ask(payload, use_graph=False, use_router=False, allow_selector=False)
        metadata = dict(source='rule', fallback_reason=reason, attempted=prior is not None)
        legacy_code = (result.get('error') or {}).get('code')
        if legacy_code == 'INVESTIGATION_SCOPE_DENIED':
            from linkloom.ui.ask_admission import legacy_denial_reason
            metadata.update(decision='REFUSE', reason_code=legacy_denial_reason(run.query), reason_class='SAFETY')
        elif legacy_code == 'EXTERNAL_PROVIDER_PERMISSION_REQUIRED':
            metadata.update(decision='REFUSE', reason_code='PROVIDER_PERMISSION_REQUIRED', reason_class='SAFETY')
        if prior:
            metadata['llm'] = prior
        result['router'] = metadata
        return result, status

    def _route_ordinary(self, run, payload):
        from linkloom.ui.structured_router import RouterInvalid, request_for, route_with_adapter
        from linkloom.ui.router_context import blocked_entity_reason, handoff_blocker
        from linkloom.ui.task_provider import pass_router_reservation
        from linkloom.ui.lifecycle import BackgroundWorkStopped
        from linkloom.ui.ask_admission import router_safety_reason
        safety = router_safety_reason(run.query)
        if safety:
            return self._router_stop(run, None, safety)
        provider = self.router_provider
        owner = self.investigation()
        if provider.adapter is None or provider.external_provider and owner is None:
            return self._legacy_router_fallback(run, payload, 'MODEL_UNAVAILABLE')
        if provider.external_provider and payload.get('allow_external_provider') is not True:
            return self._legacy_router_fallback(run, payload, 'PROVIDER_PERMISSION_REQUIRED')
        snapshot_hash = source_context(self.app, run.workspace_id)['snapshot_hash']
        def validate_sources():
            self._check_workspace(run.workspace_id)
            if source_context(self.app, run.workspace_id)['snapshot_hash'] != snapshot_hash:
                raise ProductApplicationError('INVESTIGATION_CONTEXT_CHANGED', 'Refresh the source selection and ask again.', status=409)
        session = None
        attempted = False
        try:
            context = self._router_context(run, payload, snapshot_hash)
            blocker = blocked_entity_reason(run.query, context.blocked_entities)
            if blocker:
                return self._router_stop(run, None, blocker)
            if provider.mode == "cascade":
                reason = handoff_blocker(context)
                if reason == "NO_ROUTER_CANDIDATES":
                    return self._legacy_router_fallback(run, payload, reason)
                if reason:
                    return self._router_stop(run, None, reason)
            request = request_for(context)
            def validate_snapshot():
                validate_sources()
                fresh = self._router_context(run, payload, snapshot_hash, query_time=context.query_time)
                if fresh.snapshot != context.snapshot:
                    raise ProductApplicationError('INVESTIGATION_CONTEXT_CHANGED', 'Refresh confirmed records and ask again.', status=409)
            validate_snapshot()
            attempted = True
            if provider.external_provider:
                with owner._starts.operation(request.run_id):
                    session = provider.reserve(self.app, owner, request, validate_snapshot)
                    route = route_with_adapter(session.bind(provider.adapter, 'ask-router'), request, context)
            else:
                route = route_with_adapter(provider.adapter, request, context)
            validate_snapshot()
        except BackgroundWorkStopped:
            return self._router_stop(run, None, 'POLICY_BLOCKED', http=503)
        except PermissionError:
            return self._router_stop(run, None, 'WORKSPACE_DENIED', http=403)
        except RouterInvalid as error:
            validate_sources()
            if str(error) == "EVIDENCE_INVALID":
                return self._router_stop(run, None, "EVIDENCE_INVALID", http=409)
            with pass_router_reservation(session):
                result, status = self._legacy_router_fallback(run, payload, str(error))
            result['router']['attempted'] = attempted
            return result, status
        except (ImportError, OSError, ValueError):
            # Optional local retrieval can fail without any usable catalogue.
            validate_sources()
            with pass_router_reservation(session):
                return self._legacy_router_fallback(run, payload, 'RETRIEVAL_UNAVAILABLE')
        with pass_router_reservation(session):
            return self._execute_router(run, payload, context, route)

    def _router_stop(self, run, route, reason, *, http=200):
        from linkloom.ui.structured_router import SAFETY
        safety = reason in SAFETY
        result = self._base(run)
        metadata = route.public() if route is not None else dict(intent='OTHER', domain='NONE',
                                                                 candidate_ids=[], time_ref_ids=[], time_role='NONE')
        metadata.update(source='llm' if route else 'host', decision='REFUSE' if safety or route and route.decision == 'REFUSE' else 'CLARIFY',
                        reason_code=reason, reason_class='SAFETY' if safety else 'CAPABILITY')
        result.update(path=None, router=metadata, decided_by='llm' if route else 'host')
        return self._failure(result, reason, 'The request cannot proceed under the selected scope.',
                             category='permission' if safety else 'validation', status='NEEDS_SCOPE'), http

    def _execute_router(self, run, payload, context, route):
        from linkloom.ui.router_context import admit_route
        admission = admit_route(context, route)
        candidate, as_of = admission.candidate, admission.as_of
        if admission.executor is None:
            result, status = self._router_stop(run, route, admission.reason)
            result['router']['candidate_ids'] = list(route.candidate_ids) if candidate else []
            if candidate:
                result['router']['entities'] = [dict(kind=candidate.kind, label=candidate.label)]
            return result, status
        metadata = dict(route.public(), source='llm', attempted=True,
                        entities=[dict(kind=candidate.kind, label=candidate.label)])
        # Old response contracts are preserved; the entry routing decision is
        # reported independently from the downstream graph/legacy execution.
        run = replace(run, route=dict(intent=route.intent, code='STRUCTURED_ROUTER',
                                      decided_by='llm', router=metadata))
        if admission.executor == "graph":
            response = self._run_graph_request(replace(run, scope=dict(run.scope, as_of=as_of)), payload)
            if response is not None:
                return response
            return self._legacy_router_fallback(run, payload, 'GRAPH_NOT_AVAILABLE', metadata)
        subject, relation = candidate.identity
        scoped = dict(subject=subject, relation=relation, as_of=as_of)
        run = replace(run, scope=scoped)
        return self._decision_request(run, self._base(run), payload, scoped, as_of)

    def _graph_result(self, run, graph):
        result = self._base(run)
        result.update({k: v for k, v in graph.items() if k != 'run_id'})
        result.update(path='SOURCE_RELATION_INVESTIGATION', agent_run_id=graph['run_id'],
            decided_by='graph', route=dict(intent='SOURCE_RELATION', code='TEMPORAL_GRAPH', decided_by='graph'))
        return result

    def _try_graph_fallback(self, run, payload, graph):
        context = dict(graph_run_id=graph['run_id'], graph_status=graph['graph_status'],
                       reason='GRAPH_INSUFFICIENT_' + graph['graph_status'])
        # graph_query_status was already counted by the worker; count this attempt once.
        self.graph_service.metrics.record('legacy_fallback', context['reason'])
        # Re-enter the unchanged admission/routing path without a second graph call
        # or a new Provider reservation. The outer graph_request_budget stays active.
        legacy, status = self._ask(payload, use_graph=False, use_router=False, allow_selector="router" not in run.route)
        if "router" in run.route:
            legacy["router"] = deepcopy(run.route["router"])
        if legacy.get('agent_run_id'):
            with self._lock:
                self._graph_fallbacks[legacy['agent_run_id']] = context
        return self._select_graph_fallback(legacy, status, context, run.workspace_id)

    def _select_graph_fallback(self, legacy, status, context, workspace):
        # Never mask a permission failure with a cached graph view.
        error = legacy.get('error') or {}
        if error.get('category') in {'permission', 'tool_policy_denied'} or error.get('code') in {
                'WORKSPACE_MISMATCH', 'WORKSPACE_REQUIRED'}:
            return legacy, status
        answered = (legacy.get('status') == 'COMPLETED' and legacy.get('conclusions')
                    and legacy.get('evidence') and error == {})
        if legacy.get('source_investigation') is not None:
            answered = (answered and legacy['source_investigation'].get('answer')
                        and legacy.get('reviewer', {}).get('accepted') is True)
        if answered or legacy.get('status') == 'INVESTIGATING':
            result = deepcopy(legacy)
            result.update(answer_source='legacy', decided_by='legacy', graph_fallback=dict(context))
            result['route'] = dict(result['route'], decided_by='legacy')
            return result, status
        # Async legacy failure/insufficiency keeps the original graph answer, but
        # reopening must still pass current workspace/source/receipt validation.
        result, graph_status = self.inspect(context['graph_run_id'], workspace)
        result['graph_fallback'] = dict(context, legacy_status=legacy.get('status', 'FAILED'))
        return result, graph_status

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
            graph_run = self._graph_runs.get(run_id)
        if graph_run is not None and self.graph_service is not None:
            with self.app._store_lock:
                self._check_workspace(workspace_id)
                result = self._base(graph_run)
                graph = self.graph_service.inspect(run_id, workspace_id)
                if graph is None or graph['status'] in {'BLOCKED', 'UNAUTHORIZED'}:
                    return self._failure(result, 'GRAPH_FINAL_NOT_CURRENTLY_VERIFIED',
                        'The prior graph answer is no longer verified.'), 409
                result.update({k: v for k, v in graph.items() if k != 'run_id'})
                result.update(path='SOURCE_RELATION_INVESTIGATION', agent_run_id=run_id, decided_by='graph',
                    route=dict(intent='SOURCE_RELATION', code='TEMPORAL_GRAPH', decided_by='graph'))
                return result, 200
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
        with self._lock:
            fallback = self._graph_fallbacks.get(run_id)
        if fallback is not None:
            return self._select_graph_fallback(result, status, fallback, workspace_id)
        return result, status
