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
from linkloom.ui.scope_discovery import discover_scope, resolve_query_date
from linkloom.ui.feedback_memory import FeedbackMemory
from linkloom.decision_memory.models import normalize_temporal_component
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.runtime.adaptive_planning import source_scope_violation
from linkloom.ui.ask_admission import (admission_restriction, parse_context,
    source_context, validate_context, refers_to_records)

_RELATION_QUESTION = re.compile(r"\b(owner|owns|leader|supervisor|alias)\b|\breports?\s+(?:directly\s+)?to\b|负责人|领导|汇报|别名", re.I)

# Legacy shortcuts only. Explicit source-bound domains bypass intent vocabulary.
_TASK_QUESTION = re.compile(r'\b(tasks?|deadlines?|deliverables?)\b|任务|交付|截止', re.I)

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
    r"\b(what|which)\b.*\b(using|use|supplier|vendor|decision)\b|show.*\b(sources?|evidence)\b|"
    r"当前|现行|正式|采用|生效|审核|审批|收据|供应商|供货商|用的是|查看.*来源",
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
            "reason": "Select a reviewed decision or an investigation domain and current sources. Unrecognized wording does not establish that no task exists."}


@dataclass(frozen=True)
class _AskRun:
    query: str
    workspace_id: str
    scope: dict[str, Any]
    route: dict[str, Any]
    discovery: dict[str, Any] | None = None


class UnifiedAskService:
    def __init__(self, app: ProductApplication,
                 investigation: Callable[[], ProductInvestigationService | None]) -> None:
        self.app = app
        self.investigation = investigation
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
            trusted.append({"subject": history[-1]["subject"], "relation": history[-1]["relation"],
                            "values": list(dict.fromkeys(item["value"] for item in history)),
                            "decision_ids": [item["decision_id"] for item in history],
                            "current_value": (view.get("effective") or {}).get("value")})
        return trusted

    @staticmethod
    def _base(run: _AskRun) -> dict[str, Any]:
        return {"schema_version": "product-ask/v1", "query": run.query,
                "workspace_id": run.workspace_id, "scope": deepcopy(run.scope),
                "path": {"DECISION": "DETERMINISTIC", "INVESTIGATION": "AGENT",
                         "SOURCE_RELATION": "SOURCE_RELATION_INVESTIGATION"}.get(run.route["intent"]),
                "route": dict(run.route), "status": "NEEDS_SCOPE", "conclusions": [],
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
                      classify_task(query.strip()))
        result = self._base(run)
        try:
            # Keep the current-workspace check and setup atomic with workspace selection.
            with self.app._store_lock:
                self._check_workspace(run.workspace_id)
                context = parse_context(payload.get('investigation_context'))
                restriction = admission_restriction(run.query)
                if restriction:
                    result['path'] = None
                    return self._failure(result, *restriction, status='NEEDS_SCOPE'), 200
                plan = plan_relation_investigation(run.query)
                if plan is not None:
                    if context:
                        validate_context(self.app, run.workspace_id, context)
                        if context['domain'] != 'source_relations':
                            return self._failure(result, 'INVESTIGATION_DOMAIN_CONFLICT',
                                'This question requests a source relationship chain; select that domain.', status='NEEDS_SCOPE'), 200
                    return self._source_request(run, payload, plan)
                slots = self._trusted_slots(run.workspace_id)
                resolver, memory = self.app.relation_resolver, None
                if self.feedback_memory is not None and not (values["subject"] or values["relation"]):
                    resolver, memory = self.feedback_memory.snapshot(run.workspace_id, run.query, resolver, slots)
                discovery = discover_scope(run.query, slots,
                                           resolver, subject=values["subject"],
                                           relation=values["relation"])
                if memory is not None:
                    discovery["memory"] = memory
                    if memory["conflicts"]:
                        discovery.update(status="AMBIGUOUS", scope=None, candidates=[], candidate_count=0,
                            reason="Conflicting approved terminology. Revoke the conflict or choose an explicit reviewed decision.")
                result["discovery"] = discovery
                explicit = bool(values['subject'] or values['relation'])
                if discovery['status'] == 'AMBIGUOUS':
                    result['path'] = None
                    return self._failure(result, 'DECISION_SCOPE_AMBIGUOUS', discovery['reason'], status='NEEDS_SCOPE'), 200
                # A selected capability cannot override an identity-bound formal
                # lookup. Unknown lexical terms still cannot bind an identity.
                fast_path = discovery['scope'] is not None and run.route['intent'] == 'DECISION'
                if context and not fast_path:
                    if explicit:
                        result['path'] = None
                        return self._failure(result, 'TASK_SCOPE_CONFLICT',
                            'Clear the advanced decision selection before investigating source records.', status='NEEDS_SCOPE'), 200
                    validate_context(self.app, run.workspace_id, context)
                    if context['domain'] == 'tasks':
                        return self._task_request(run, context=context)
                    service = self.investigation()
                    if not service or not service.config().get('adaptive_planning'):
                        result['path'] = None
                        return self._failure(result, 'INVESTIGATION_CAPABILITY_UNAVAILABLE',
                            'Source relationship planning is unavailable. No alternate investigation was started.', status='BLOCKED'), 200
                    return self._source_request(run, payload, None)
                if (not explicit and _TASK_QUESTION.search(run.query)
                        and run.route['intent'] == 'INVESTIGATION' and refers_to_records(run.query)):
                    return self._task_request(run)
                relation_shortcut = False
                if not explicit and _RELATION_QUESTION.search(run.query):
                    service = self.investigation()
                    # Preserve the existing deterministic capability refusal
                    # even when no record reference permits adaptive planning.
                    relation_shortcut = (refers_to_records(run.query, relationships=True)
                        or service is None or not service.config().get('adaptive_planning'))
                if discovery["scope"] is None:
                    if discovery["status"] == "NO_MATCH" and relation_shortcut:
                        return self._source_request(run, payload, None)
                    result["path"] = None
                    code = 'DECISION_SCOPE_' + discovery['status'] if explicit else 'INVESTIGATION_DOMAIN_REQUIRED'
                    return self._failure(result, code,
                        discovery['reason'] if explicit else
                        'Select a reviewed decision or an investigation domain bound to the current sources; no investigation was started.',
                        status='NEEDS_SCOPE'), 200
                values.update(discovery["scope"])
                result["scope"].update(discovery["scope"])
                if relation_shortcut and run.route["intent"] != "DECISION":
                    return self._source_request(run, payload, None)
                as_of, date_error = resolve_query_date(run.query, payload.get("as_of"))
                if date_error:
                    result["path"] = None
                    return self._failure(result, date_error["code"], date_error["message"],
                                         status="NEEDS_SCOPE"), 200
                if run.route["intent"] == "UNKNOWN":
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

    def _task_request(self, run, *, context=None):
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
        if not config.get('available') or not config.get('task_investigation') or (config.get('external_provider') and not config.get('canary_live')):
            return self._failure(result, 'TASK_INVESTIGATION_UNAVAILABLE',
                'Task investigation needs explicit offline stage adapters. New live Provider execution is disabled.',
                category='configuration', status='BLOCKED'), 200
        try:
            snapshot = service.start(run.query, run.workspace_id, task_investigation=True)
        except Exception as exc:
            code = str(exc) if isinstance(exc, ValueError) else getattr(exc, 'code', 'TASK_PREPARATION_FAILED')
            if config.get('canary_live'):
                result['canary_budget'] = service.canary_host.ledger.snapshot()
            return self._failure(result, code, 'Task preparation stopped; no result is publishable.',
                category='validation', status='BLOCKED'), 200
        with self._lock:
            if snapshot['kind'] == 'error':
                return self._from_snapshot(run, snapshot), 200
            self._runs[snapshot['run']['id']] = (run, service)
        return self._from_snapshot(run, snapshot), 202

    def _source_request(self, run, payload, plan):
        service = self.investigation()
        config = service.config() if service else {}
        adaptive = plan is None and config.get("adaptive_planning") is True
        route = {"intent": "SOURCE_RELATION", "code": "SOURCE_RELATION_MULTIHOP",
                 "reason": "A bounded directed source-relation chain; no business approval is implied.",
                 "plan": plan.to_dict() if plan else None}
        run = _AskRun(run.query, run.workspace_id, run.scope, route)
        result = self._base(run)
        if source_scope_violation(run.query):
            return self._failure(result, "UNSUPPORTED_QUERY",
                "Source investigation only reads the current workspace; clarify a read-only relationship question.",
                status="NEEDS_SCOPE"), 200
        if plan is None and not adaptive:
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
            if not adaptive:
                plan = plan_relation_investigation(runtime_query)
        if plan is None and not adaptive:
            return self._failure(result, "UNSUPPORTED_QUERY", "The dated relationship plan exceeds the supported bounds.", status="NEEDS_SCOPE"), 200
        route["plan"] = plan.to_dict() if plan else None
        route["relations"] = [goal.relation_path[0] for goal in plan.subgoals] if plan else []
        route["planning"] = {"origin": "model" if adaptive else "rules", "validated": plan is not None}
        result["scope"] = {"subject": "", "relation": "", "as_of": as_of}
        route.update(requires_external_provider=config.get("external_provider", False),
                     external_provider_authorized=payload.get("allow_external_provider") is True,
                     can_start=bool(config.get("available")) and (not config.get("external_provider") or payload.get("allow_external_provider") is True))
        result["route"] = dict(route)
        if not config.get("available"):
            return self._failure(result, "AGENT_NOT_CONFIGURED", "No investigation model is configured.", category="configuration"), 503
        if not route["can_start"]:
            return self._failure(result, "EXTERNAL_PROVIDER_PERMISSION_REQUIRED", "Allow this request before sources are sent or model costs are incurred.", category="permission"), 403
        try:
            snapshot = service.start(runtime_query, run.workspace_id, task_plan=plan, **({"adaptive": True} if adaptive else {}))
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
            campaign = (snapshot.get('preparation') or {}).get('campaign_budget')
            if campaign:
                result['canary_budget'] = deepcopy(campaign)
                result['metrics'].update(accounting_scope='CANARY_CAMPAIGN_ALL_STAGES',
                    provider_requests=campaign['request_count'],
                    provider_input_tokens_accounted=campaign['input_tokens'],
                    billable_output_tokens_accounted=campaign['output_tokens'],
                    thinking_tokens=campaign['thinking_tokens'],
                    estimated_cost_usd=campaign['estimated_cost_usd'],
                    application_reserved_cost_usd=campaign['reserved_cost_usd'],
                    application_budget_usd=campaign['max_total_cost_usd'],
                    provider_final_bill_usd=None)
            review = snapshot.get('reviewer', {})
            result['reviewer'] = dict(review, accepted=review.get('decision') == 'evidence_sufficient')
            if snapshot.get('task_investigation') and result['reviewer']['accepted']:
                task_result = snapshot['task_investigation']
                release_status = ('BLOCKED' if task_result.get('investigation_status') == 'BLOCKED'
                                  else (snapshot.get('preparation') or {}).get('status', 'PARTIAL'))
                result.update(status=release_status, task_investigation=deepcopy(task_result),
                    evidence=deepcopy(snapshot['evidence']), uncertainty=deepcopy(snapshot['uncertainty']))
            return result
        result["agent_run_id"] = snapshot["run"]["id"]
        result["progress"] = snapshot.get("progress")
        result["metrics"] = snapshot.get("metrics", {})
        if snapshot.get("planning"):
            result["route"]["planning"] = deepcopy(snapshot["planning"])
            if snapshot["planning"].get("plan"):
                result["route"]["plan"] = deepcopy(snapshot["planning"]["plan"])
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
        if error.get("code") == "MODEL_PLAN_DECLINED":
            return self._failure(result, "MODEL_PLAN_DECLINED",
                "This question needs clarification or a relationship outside the supported project-owner-leader paths.",
                status="NEEDS_SCOPE")
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
