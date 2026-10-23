"""Runtime request preparation using C's context contracts and D's audit store.

No model loop, source database or semantic reasoning lives here. A summary can
only replace operational history; fresh originals and lifecycle checks remain
mandatory. The hook runs only before a NEW durable model request.
"""
from dataclasses import asdict, dataclass, field, replace
import json

from linkloom.context.assembler import ContextBudget
from linkloom.context.policy import ContextPolicy, EvidenceGroup, RequestUsage, estimate_tokens
from linkloom.context.recovery import EvidenceRecovery, EvidenceRef
from linkloom.context.working import (WorkingContext, RecordRef, ToolState, EvidenceConflict,
    CompactionProposal, compact_working_context)
from linkloom.retrieval_v2.models import RetrievedEvidence
from linkloom.runtime.context_sources import TaskEvidenceBridge
from linkloom.runtime.errors import ValidationError
from linkloom.runtime.task_plan import TaskPlan, Subgoal
from linkloom.semantic_ingestion.task_relation_models import canonical_hash


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class FakeHistoryCompactor:
    """Deterministic wiring adapter, never a model or independent evidence."""
    def compact(self, request):
        state = request.state
        return CompactionProposal(
            f"Archived {len(state.operations)} operational interactions. Read the immutable ledger reference "
            "for details. Protected goal, unresolved questions and failures remain in WorkingContext. "
            "Only freshly recovered originals support conclusions.",
            tuple(r.evidence_id for r in state.evidence_refs))


@dataclass(frozen=True)
class ContextRuntimeConfig:
    policy: ContextPolicy = field(default_factory=lambda: ContextPolicy(model_id="offline-task-context",
        context_window_tokens=65536, output_reserve_tokens=2048))
    local_budget: ContextBudget = field(default_factory=ContextBudget)
    envelope: RequestUsage = field(default_factory=lambda: RequestUsage(tokens=64, bytes=256))
    history_soft_bytes: int = 24 * 1024
    working_max_tokens: int = 4096
    compactor: object = field(default_factory=FakeHistoryCompactor)
    compactor_id: str = "DETERMINISTIC_FAKE_V1"
    tokenizer_id: str = "LINKLOOM_MULTILINGUAL_HEURISTIC_V1"

    def __post_init__(self):
        if (type(self.history_soft_bytes) is not int or self.history_soft_bytes < 1
                or type(self.working_max_tokens) is not int or self.working_max_tokens < 1
                or not callable(getattr(self.compactor, "compact", None)) or not self.compactor_id):
            raise ValueError("explicit context budgets and injected compactor identity required")
        if self.policy.max_request_bytes > 96 * 1024 or self.local_budget.max_evidence > 8:
            raise ValueError("context policy cannot raise existing Runtime evidence/request caps")
        if not self.tokenizer_id or (self.policy.token_counter is not estimate_tokens
                and self.tokenizer_id == "LINKLOOM_MULTILINGUAL_HEURISTIC_V1"):
            raise ValueError("injected tokenizers require an explicit immutable implementation identity")

    def identity(self):
        policy = {k: v for k, v in vars_from_slots(self.policy).items() if k != "token_counter"}
        return dict(policy=policy, local_budget=asdict(self.local_budget), envelope=asdict(self.envelope),
            history_soft_bytes=self.history_soft_bytes, working_max_tokens=self.working_max_tokens,
            compactor_id=self.compactor_id, tokenizer_id=self.tokenizer_id,
            tokenizer_probe=[self.configured_count(s) for s in ("ASCII budget", "跨文档正反证😀", encoded({"evidence_id": "context_ev_v1_012345"}))])

    def configured_count(self, text):
        return self.policy.count(text)


def vars_from_slots(value):
    return {name: getattr(value, name) for name in value.__dataclass_fields__}


class RuntimeWorkingContext:
    def __init__(self, config, tools, store, question, assemble):
        self.config, self.tools, self.store, self.question, self.assemble = config, tools, store, question, assemble
        self.bridge = TaskEvidenceBridge(tools)
        self.recovery = EvidenceRecovery(self.bridge.read_source, max_total_quote_bytes=24 * 1024)
        self.mapping = None
        self.latest = None
        self.questions = ()
        self.plan = TaskPlan(question, (Subgoal("sources_current", "Recover the declared authorized original sources."),
            Subgoal("relations_pending", "Inspect relation receipts; semantic acceptance remains unresolved.", ("sources_current",))))

    def _plan_from_endpoints(self):
        semantic = self.bridge.semantic_snapshot()
        if semantic is None:
            return
        # Typed host questions label investigations, never supply a verdict.
        goals = [Subgoal("sources_current", "Recover every declared authorized source and competing observation.")]
        goals.extend(Subgoal("relation_" + str(i), f"Investigate {q.predicate}: {q.subject} -> {q.object}; semantics not accepted.",
            ("sources_current",)) for i, q in enumerate(semantic.queries[:3]))
        goals.append(Subgoal("relations_pending", "Independent semantic acceptance and unresolved evidence gaps.", ("sources_current",)))
        self.plan = TaskPlan(self.question, tuple(goals))
        self.questions = tuple(f"Unaccepted {q.predicate}: {q.subject} -> {q.object}" for q in semantic.queries)

    def _polarities(self, state):
        mapped = {r["origin_ref"]: r["context_ref"]["evidence_id"] for r in self.mapping or []}
        support, counter, conflicts = set(), set(), []
        def nodes(value):
            if isinstance(value, dict):
                yield value
                for v in value.values(): yield from nodes(v)
            elif isinstance(value, list):
                for v in value: yield from nodes(v)
        for record in state.tool_ledger:
            root = (record.result or {}).get("value", {})
            if not isinstance(root, dict): continue
            spans = root.get("evidence_spans", {})
            def refs(value):
                found = set()
                for node in nodes(value):
                    origin = node.get("evidence_ref")
                    if origin in mapped: found.add(mapped[origin])
                    if node.get("span_ref") in spans:
                        origin = spans[node["span_ref"]].get("evidence_ref")
                        if origin in mapped: found.add(mapped[origin])
                return found
            for node in nodes(root):
                positive, negative = refs(node.get("supporting_evidence", [])), refs(node.get("counter_evidence", []))
                support.update(positive); counter.update(negative)
                if node.get("role") in {"IDENTITY_COUNTER", "PLAN_COUNTER"}: counter.update(refs(node))
                if node.get("proposed_verdict", node.get("verdict")) == "CONFLICT" and positive and negative:
                    conflicts.append(EvidenceConflict("Observed " + str(node.get("predicate", "relation")) + " conflict; not semantically accepted.",
                        tuple(sorted(positive)), tuple(sorted(negative))))
        return support, counter, tuple(dict.fromkeys(conflicts))

    @staticmethod
    def _retain_required_receipts(request):
        # Preserve the newest actual receipt per task tool, so summaries never
        # substitute for D's receipt-only Final. Older duplicates can be archived.
        required = {"inspect_task_context", "query_task_relations", "analyze_task_semantics"}
        selected = []
        seen = {request.observation.tool_id} if request.observation else set()
        for interaction in reversed(request.tool_history):
            tool = interaction.tool_call.tool_id
            if tool in required and tool not in seen:
                selected.insert(0, interaction)
                seen.add(tool)
        return replace(request, tool_history=selected, tool_turns=request.tool_turns[-3:])

    def _check_lifecycle(self, state):
        for record in state.tool_ledger:
            if record.status != "completed" or record.result.get("status") != "ok":
                continue
            if record.error is not None:
                raise ValidationError('Tool checkpoint uncertainty requires recovery.',
                    details={'code': 'CONTEXT_LIFECYCLE_CHANGED'})
            if record.tool_id == "query_task_relations":
                from linkloom.agents.task_relation_result import task_relation_task_id
                if self.tools.query_task_relations_from_record(
                        record, state.tool_ledger, run_id=state.run_id,
                        task_id=task_relation_task_id(state.model_executions, state.run_id)) != record.result["value"]:
                    raise ValidationError("Relation lifecycle changed after observation.", details={"code": "CONTEXT_LIFECYCLE_CHANGED"})
            if record.tool_id == "analyze_task_semantics" and self.tools.semantic_analysis is not None:
                if self.tools.semantic_analysis.review(record.result["value"], call_id=record.call_id)["status"] != "pass":
                    raise ValidationError("Semantic receipt is no longer current.", details={"code": "CONTEXT_SOURCE_CHANGED"})

    def _record(self, prefix, name, payload, kind="observation"):
        return self.store.write(prefix + "/" + name + ".json", json.loads(encoded(payload)), kind=kind).to_dict()

    def _working(self, state, prefix, refs):
        ledger = self._record(prefix, "ledger", {"run_id": state.run_id, "records": [r.to_dict() for r in state.tool_ledger]})
        checkpoint = self._record(prefix, "checkpoint", {"state": state.to_dict()})
        def raw_ref(ref, kind):
            return RecordRef(self.tools.workspace_id, state.run_id, kind, ref["ref"], ref["sha256"])
        tools = tuple(ToolState(r.call_id, r.status, (r.error or {}).get("code", "TOOL_FAILED")
            if r.status == "failed" else None) for r in state.tool_ledger)
        seen = tuple(r.tool_id for r in state.tool_ledger if r.status == "completed")
        completed = ("sources_current",) if refs else ()
        completed += tuple("observed_" + t for t in dict.fromkeys(seen))
        operations = tuple(encoded(dict(call_id=r.call_id, tool=r.tool_id, status=r.status,
            arguments_sha256=canonical_hash(r.arguments), result_bytes=len(encoded(r.result).encode("utf8")))) for r in state.tool_ledger)
        _, counter, conflicts = self._polarities(state)
        return WorkingContext(workspace_id=self.tools.workspace_id, run_id=state.run_id, user_goal=self.question,
            ledger_ref=raw_ref(ledger, "ledger"), checkpoint_ref=raw_ref(checkpoint, "checkpoint"),
            evidence_refs=refs, completed_subgoals=completed,
            unresolved_questions=tuple(g.question for g in self.plan.subgoals if g.subgoal_id != "sources_current") + self.questions,
            conflicts=conflicts, negative_evidence_ids=tuple(sorted(counter)), last_tool=tools[-1] if tools else None,
            failed_tools=tuple(t for t in tools if t.status == "failed"), operations=operations,
            next_investigations=("Re-read original sources and current relation receipts; independent semantic acceptance pending.",))

    def _sections(self, working):
        protected = replace(working, operations=(), summary_text="", summary_evidence_ids=()).render()
        operational = working.summary_text if working.summary_text else encoded(list(working.operations))
        mapping = encoded(self.mapping or [])
        return protected, operational, mapping

    def _compose(self, request, working, bundle_text=""):
        protected, operational, mapping = self._sections(working)
        return replace(request, user_input=request.user_input + "\nworking_context=" + protected
            + "\noperational_hint_not_evidence=" + operational + "\nexplicit_source_mapping=" + mapping
            + "\nfresh_original_source_context=" + bundle_text,
            generation_options=replace(request.generation_options,
                max_output_tokens=request.generation_options.max_output_tokens or self.config.policy.output_reserve_tokens))

    def _budget(self, prepared, original, working, bundle_text):
        p = self.config.policy
        def size(text): return dict(bytes=len(text.encode("utf8")), tokens=p.count(text))
        protected, operational, mapping = self._sections(working)
        body = encoded(prepared.to_dict())
        sections = dict(instruction=size(original.user_input), tools=size(encoded([t.to_dict() for t in prepared.available_tools])),
            latest_result=size(encoded(prepared.observation.to_dict() if prepared.observation else None)),
            verified_evidence=size(bundle_text + mapping), working_context=size(protected), compaction_summary=size(operational),
            history=size(encoded([h.to_dict() for h in prepared.tool_history]) + encoded([t.to_dict() for t in prepared.tool_turns])),
            existing_verified_context=size(encoded([r.to_dict() for r in prepared.evidence_context])),
            request_envelope=asdict(self.config.envelope))
        return dict(input_tokens=p.count(body) + self.config.envelope.tokens,
            output_reserve_tokens=p.output_reserve_tokens, input_bytes=len(body.encode("utf8")) + self.config.envelope.bytes,
            user_input_bytes=len(prepared.user_input.encode("utf8")), context_window_tokens=p.context_window_tokens,
            max_request_bytes=min(96 * 1024, p.max_request_bytes), segments=sections,
            token_count_kind=p.token_count_kind, actual_provider_tokens=False,
            normalized_request_bytes=len(body.encode("utf8")),
            serialization_overhead_bytes=len(body.encode("utf8")) - sum(v["bytes"] for k, v in sections.items() if k != "request_envelope"))

    def _fits(self, report, request):
        return (report["input_tokens"] + report["output_reserve_tokens"] <= report["context_window_tokens"]
            and report["input_bytes"] <= report["max_request_bytes"] and report["user_input_bytes"] <= 32 * 1024
            and request.generation_options.max_output_tokens <= report["output_reserve_tokens"])

    def prepare(self, request, state):
        try:
            with self.bridge.session():
                return self._prepare(request, state)
        except (ValidationError, ValueError) as error:
            details = getattr(error, "details", {})
            if details.get("code") == "CONTEXT_INCOMPLETE": raise
            prefix = "working/" + canonical_hash(state.run_id)[:20] + "/" + str(request.sequence) + "-" + canonical_hash(state.to_dict())[:16]
            working = self._working(state, prefix, tuple(EvidenceRef.from_dict(r["context_ref"]) for r in self.mapping or []))
            working = replace(working, completed_subgoals=tuple(g for g in working.completed_subgoals if g != "sources_current"),
                unresolved_questions=(*working.unresolved_questions, "Original authorization snapshot is incomplete or changed."))
            audit = dict(status="BLOCKED", errors=[details.get("code", "CONTEXT_SOURCE_INCOMPLETE")],
                source_errors=details.get("errors", []), run_id=state.run_id, turn_id=request.turn_id,
                coverage=[dict(subgoal_id=g.subgoal_id, complete=False,
                    missing_evidence_ids=[r["context_ref"]["evidence_id"] for r in self.mapping or []],
                    reason="AUTHORIZED_SNAPSHOT_UNAVAILABLE_OR_CHANGED") for g in self.plan.subgoals],
                working_ref=self._record(prefix, "blocked-state", {"working_json": working.to_json()}))
            audit["audit_ref"] = self._record(prefix, "blocked-audit", audit)
            self.latest = audit
            raise ValidationError("Original context snapshot is INCOMPLETE/BLOCKED.", details={"code": "CONTEXT_INCOMPLETE", "audit_ref": audit["audit_ref"]}) from error

    def _prepare(self, request, state):
        prefix = "working/" + canonical_hash(state.run_id)[:20] + "/" + str(request.sequence) + "-" + canonical_hash(state.to_dict())[:16]
        audit = dict(status="BLOCKED", errors=[], run_id=state.run_id, turn_id=request.turn_id,
            config=self.config.identity(), base_instruction_sha256=canonical_hash(request.user_input),
            compaction=None, recovery=None, coverage=[], request_usage=None)
        working = None
        try:
            self._check_lifecycle(state)
            current = self.bridge.catalog()
            self._plan_from_endpoints()
            if self.mapping is not None and self.mapping != current:
                raise ValidationError("Source mapping changed within the run.", details={"code": "CONTEXT_SOURCE_CHANGED"})
            self.mapping = current
            refs = tuple(EvidenceRef.from_dict(row["context_ref"]) for row in current)
            working = self._working(state, prefix, refs)
            audit["before_working_ref"] = self._record(prefix, "before", {"working_json": working.to_json()})
            restored = self.recovery.restore(workspace_id=self.tools.workspace_id, refs=refs,
                required_ids=tuple(r.evidence_id for r in refs))
            audit["recovery"] = json.loads(encoded(asdict(restored)))
            if restored.status != "complete":
                raise ValidationError("Required original evidence cannot be restored.", details={"code": "CONTEXT_RECOVERY_INCOMPLETE"})
            candidates = tuple(RetrievedEvidence(r.ref.evidence_id, r.ref.source_ref, r.ref.logical_path, 1.0, i,
                "runtime_recovery", r.ref.resource_id, dict(text=r.quote, workspace_id=self.tools.workspace_id,
                    status="verified", active=True, current=True, authorized=True)) for i, r in enumerate(restored.evidence))
            positive, negative, _ = self._polarities(state)
            all_ids = {r.evidence_id for r in refs}
            parts = (("observed_support_only", positive - negative), ("observed_counter_only", negative - positive),
                ("observed_shared_citations", positive & negative), ("unclassified_investigation", all_ids - positive - negative))
            groups = tuple(EvidenceGroup(name, tuple(sorted(ids)), tuple(g.subgoal_id for g in self.plan.subgoals), bool(working.conflicts))
                for name, ids in parts if ids)
            def assemble(candidate_request, candidate_working):
                other = self._compose(candidate_request, candidate_working)
                usage = RequestUsage(self.config.policy.count(encoded(other.to_dict())) + self.config.envelope.tokens,
                    len(encoded(other.to_dict()).encode("utf8")) + self.config.envelope.bytes)
                audit["request_usage"] = asdict(usage)
                # The default five is a preference, not a reason to discard a
                # complete required group. Explicit non-default caps stay hard.
                capacity = (8 if self.config.local_budget.max_evidence == ContextBudget().max_evidence
                    else self.config.local_budget.max_evidence)
                return self.assemble(query=self.question, retrieved_evidence=candidates, evidence_groups=groups,
                    task_subgoals=tuple(g.subgoal_id for g in self.plan.subgoals), request_usage=usage,
                    required_evidence_limit=min(capacity, self.config.policy.max_candidates))
            bundle = None
            try: bundle = assemble(request, working)
            except ValueError: pass
            audit["coverage_before_compaction"] = (json.loads(encoded([asdict(c) for c in bundle.selection.coverage]))
                if bundle and bundle.selection else [dict(subgoal_id=g.subgoal_id, complete=False,
                    missing_evidence_ids=[r.evidence_id for r in refs], reason="NO_REQUEST_ALLOCATION") for g in self.plan.subgoals])
            trial = self._compose(request, working, bundle.rendered_text if bundle else "")
            before_budget = self._budget(trial, request, working, bundle.rendered_text if bundle else "")
            history_bytes = len(encoded([h.to_dict() for h in request.tool_history]).encode("utf8")) + len(encoded([t.to_dict() for t in request.tool_turns]).encode("utf8"))
            history_pressure = history_bytes > self.config.history_soft_bytes
            pressure = history_pressure or not self._fits(before_budget, trial) or bundle is None
            if pressure and working.operations:
                compacted = compact_working_context(working, compactor=self.config.compactor, recovery=self.recovery,
                    max_tokens=self.config.working_max_tokens, policy=self.config.policy)
                audit["compaction"] = dict(trigger="HISTORY_ALLOCATION" if history_pressure else "FULL_REQUEST_ALLOCATION",
                    kind=self.config.compactor_id, status=compacted.status, before_tokens=compacted.before_tokens,
                    after_tokens=compacted.after_tokens, before_bytes=compacted.before_bytes, after_bytes=compacted.after_bytes,
                    before_budget=before_budget, dropped_history=len(request.tool_history),
                    dropped_tool_turns=0)
                if compacted.status != "complete":
                    raise ValidationError("Compaction cannot preserve recoverable protected state.", details={"code": "CONTEXT_COMPACTION_INCOMPLETE"})
                working = compacted.state
                retained = self._retain_required_receipts(request)
                audit["compaction"]["dropped_history"] = len(request.tool_history) - len(retained.tool_history)
                audit["compaction"]["dropped_tool_turns"] = len(request.tool_turns) - len(retained.tool_turns)
                request = retained
                bundle = assemble(request, working)
            audit["coverage"] = (json.loads(encoded([asdict(c) for c in bundle.selection.coverage]))
                if bundle and bundle.selection else audit["coverage_before_compaction"])
            if not bundle or not audit["coverage"] or not all(c["complete"] for c in audit["coverage"]):
                raise ValidationError("Required source group does not fit intact.", details={"code": "CONTEXT_COVERAGE_INCOMPLETE"})
            prepared = self._compose(request, working, bundle.rendered_text)
            report = self._budget(prepared, request, working, bundle.rendered_text)
            audit["budget"] = report
            if not self._fits(report, prepared):
                raise ValidationError("Complete request exceeds configured allocation.", details={"code": "CONTEXT_REQUEST_BUDGET_EXHAUSTED"})
            audit.update(status="COMPLETE", prepared_request_sha256=canonical_hash(prepared.to_dict()),
                mapping_ref=self._record(prefix, "mapping", {"entries": current}), task_plan=self.plan.to_dict(),
                task_plan_scope="OPERATIONAL_EVIDENCE_COVERAGE; typed questions are NOT accepted relations",
                authorized_snapshot_id=self.bridge.snapshot_id,
                groups=json.loads(encoded([asdict(g) for g in groups])))
            audit["working_ref"] = self._record(prefix, "state", {"working_json": working.to_json()})
            audit["audit_ref"] = self._record(prefix, "audit", audit)
            self.latest = audit
            return prepared, audit
        except (ValidationError, ValueError) as error:
            audit["errors"] = [getattr(error, "details", {}).get("code", "CONTEXT_ALLOCATION_UNAVAILABLE")]
            if not audit["coverage"]:
                audit["coverage"] = audit.get("coverage_before_compaction") or [dict(subgoal_id=g.subgoal_id,
                    complete=False, missing_evidence_ids=[r["context_ref"]["evidence_id"] for r in self.mapping or []],
                    reason="ORIGINAL_CONTEXT_UNAVAILABLE") for g in self.plan.subgoals]
            if working is None:
                working = self._working(state, prefix, tuple(EvidenceRef.from_dict(r["context_ref"]) for r in self.mapping or []))
                working = replace(working, completed_subgoals=tuple(g for g in working.completed_subgoals if g != "sources_current"),
                    unresolved_questions=(*working.unresolved_questions, "Required current original context is unavailable."))
            audit["working_ref"] = self._record(prefix, "blocked-state", {"working_json": working.to_json()})
            audit["audit_ref"] = self._record(prefix, "blocked-audit", audit)
            self.latest = audit
            raise ValidationError("Context is INCOMPLETE/BLOCKED; no new model request authorized.",
                details={"code": "CONTEXT_INCOMPLETE", "audit_ref": audit["audit_ref"]}) from error

    def _load_audit(self, record):
        outer = self.store.read(record.request_ref, expected_sha256=record.request_sha256)
        ref = outer["context_audit"]["engineering"]["audit_ref"]
        audit = self.store.read(ref["ref"], expected_sha256=ref["sha256"])
        identity = {key: getattr(record, key) for key in ("run_id", "turn_id", "task_id", "agent_id", "sequence")}
        if (outer["runtime_identity"] != identity or audit["run_id"] != record.run_id
                or audit["turn_id"] != record.turn_id
                or audit["prepared_request_sha256"] != canonical_hash(outer["model_request"])):
            raise ValidationError("WorkingContext audit does not match the actual durable model request.")
        return audit

    def validate_instruction(self, record, expected):
        audit = self._load_audit(record)
        outer = self.store.read(record.request_ref, expected_sha256=record.request_sha256)
        return (audit["base_instruction_sha256"] == canonical_hash(expected)
            and audit["prepared_request_sha256"] == canonical_hash(outer["model_request"]))

    def restore(self, state):
        with self.bridge.session():
            return self._restore(state)

    def _restore(self, state):
        if not state.model_executions:
            return
        record = max(state.model_executions, key=lambda r: r.sequence)
        audit = self._load_audit(record)
        if audit["run_id"] != state.run_id or audit["config"] != self.config.identity() or audit["status"] != "COMPLETE":
            raise ValidationError("WorkingContext identity or policy changed.")
        if audit["authorized_snapshot_id"] != self.bridge.snapshot_id:
            raise ValidationError("Authorized source contract changed since checkpoint.")
        ref = audit["working_ref"]
        working = WorkingContext.from_json(self.store.read(ref["ref"], expected_sha256=ref["sha256"])["working_json"])
        if working.run_id != state.run_id or working.workspace_id != self.tools.workspace_id or working.user_goal != self.question:
            raise ValidationError("WorkingContext belongs to another task.")
        ledger = self.store.read(working.ledger_ref.locator, expected_sha256=working.ledger_ref.sha256)
        snapshot = self.store.read(working.checkpoint_ref.locator, expected_sha256=working.checkpoint_ref.sha256)["state"]
        if (snapshot["run_id"] != state.run_id or snapshot["thread_id"] != state.thread_id
                or snapshot["source"] != state.source.to_dict() or snapshot["policy"] != state.policy.to_dict()
                or snapshot["tool_ledger"] != ledger["records"] or ledger["run_id"] != state.run_id):
            raise ValidationError("Immutable checkpoint/ledger reference identity changed.")
        records = {r.turn_id: r.to_dict() for r in state.model_executions}
        if any(records.get(r["turn_id"]) != r for r in snapshot["model_executions"]):
            raise ValidationError("Existing model execution record changed after context snapshot.")
        current = {r.call_id: r.to_dict() for r in state.tool_ledger}
        if any(current.get(r["call_id"]) != r for r in ledger["records"]):
            raise ValidationError("Original completed tool ledger changed.")
        self._check_lifecycle(state)
        restored = self.recovery.restore(workspace_id=self.tools.workspace_id, refs=working.evidence_refs,
            required_ids=tuple(r.evidence_id for r in working.evidence_refs))
        if restored.status != "complete":
            raise ValidationError("Cold recovery originals are no longer current.")
        mapping = audit["mapping_ref"]
        self.mapping = self.store.read(mapping["ref"], expected_sha256=mapping["sha256"])["entries"]
        if self.mapping != self.bridge.catalog():
            raise ValidationError("Cold recovery source mapping changed.")
        self.latest = audit

    def review(self, state):
        try:
            self.restore(state)
        except (ValidationError, ValueError, KeyError):
            return dict(status="fail", reason="working_context_or_recovered_source_changed")
        return dict(status="pass", checks=["WORKING_CONTEXT_RECORD_IDENTITY", "FRESH_AUTHORIZED_ORIGINAL_RECOVERY", "FULL_REQUEST_PREFLIGHT"])
