"""P2-A/B joint Product proof over one shared synthetic source.

The fixture keeps two distinct v2 work observations in one source.  They share
one exact owner/date quote and message, while their work ranges differ.
A second source carries a scoped task key matching a pre-existing P4 record
for A only. No record is created for B.  The sidecar is seeded through the public P4 assessment and store
APIs; the Product path itself only reads that durable record.
"""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.agents.providers import GeminiProviderAdapter
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.runtime.checkpoint import SQLiteCheckpointer
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_field_binding import FIELD_ROLES
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, canonical_hash
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.tools.contracts import ToolCall
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_provider import compose_task_provider
from tests.integration.test_m3q_provider_host import ScriptedGeminiTransport, loopback_only
from tests.m3p_support import product_http
from tests.integration.test_product_application_flow import _client
from tests.integration.test_inspect_ledger_cold_process import _fresh_cold_read
from tests.unit.test_m3q_provider_budget import limits
from tests.unit.test_task_observation_discovery import claim


SHARED_SOURCE = (
    "[2037-08-01T09:00:00Z] 记录员: 星河项目任务 FLOW-31：乔雨负责兼容矩阵核验和索引重建，"
    "这两项工作都在2037-08-19之前交齐。\n"
)
SHARED_TEXT = SHARED_SOURCE.split('记录员: ', 1)[1].strip()
MATCH_SOURCE = (
    "[2037-08-03T09:00:00Z] 周岚: 任务 FLOW-31："
    "兼容矩阵核验，项目：星河项目，负责人：乔雨，截止日期：2037-08-19。\n"
)
WORK_A = "兼容矩阵核验"
WORK_B = "索引重建"
OWNER = "乔雨"
PROJECT = "星河项目"
DEADLINE = "2037-08-19"


def _field_bound_claim(text: str, work: str, *, ordinal: int, source_text_owner: bool = False):
    """Build the wire proposal consumed by the real v3 discovery service."""

    owner_binding = dict(
        field="owner",
        value=OWNER,
        role=FIELD_ROLES["owner"],
        state="ASSERTED",
        quote="负责人：乔雨" if source_text_owner else '乔雨负责',
        task_quote=work,
        basis="SOURCE_TEXT",
        message_ordinal=ordinal,
    )
    bindings = [
        owner_binding,
        dict(field="deadline", value=DEADLINE, role=FIELD_ROLES["deadline"], state="ASSERTED",
             quote=("截止日期：" + DEADLINE) if source_text_owner else '都在' + DEADLINE + '之前交齐', task_quote=work, basis="SOURCE_TEXT",
             message_ordinal=ordinal),
        dict(field="project", value=PROJECT, role=FIELD_ROLES["project"], state="ASSERTED",
             quote=("项目：" + PROJECT) if source_text_owner else PROJECT + '任务', task_quote=work, basis="SOURCE_TEXT",
             message_ordinal=ordinal),
        dict(field="vendor", value=None, role=FIELD_ROLES["vendor"], state="NOT_STATED",
             quote="", task_quote=work, basis="SOURCE_TEXT", message_ordinal=ordinal),
    ]
    row = claim(text, work, owner=OWNER, project=PROJECT, deadline_text=DEADLINE)
    row["task"].update(work_state="ASSERTED", field_bindings=bindings)
    return row


class JointDiscoveryStage:
    stage = 'discovery'
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request.to_dict() if hasattr(request, "to_dict") else {})
        if SHARED_TEXT in request.user_input:
            claims = [
                _field_bound_claim(
                    SHARED_TEXT,
                    WORK_A, ordinal=0,
                ),
                _field_bound_claim(
                    SHARED_TEXT,
                    WORK_B, ordinal=0,
                ),
            ]
        elif "FLOW-31" in request.user_input:
            claims = [_field_bound_claim(
                "任务 FLOW-31：兼容矩阵核验，项目：星河项目，负责人：乔雨，截止日期：2037-08-19。",
                WORK_A, ordinal=0, source_text_owner=True,
            )]
        else:
            raise AssertionError("joint discovery received an unexpected source")
        return ModelResponse(
            action=ModelAction.tool(ToolCall("joint-discovery", request.available_tools[0].tool_id,
                                              {"claims": claims})),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )


class JointPlanningStage:
    stage = 'planning'
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request.to_dict() if hasattr(request, "to_dict") else {})
        payload = json.loads(request.user_input.split("INPUT_JSON\n", 1)[1])
        endpoints = payload["endpoints"]
        assert len(endpoints) == 3, endpoints
        a = next(e['handle'] for e in endpoints
                 if e['task']['raw_text'] == SHARED_TEXT and e['task']['description'] == WORK_A)
        b = next(e['handle'] for e in endpoints if e['task']['description'] == WORK_B)
        a2 = next(e['handle'] for e in endpoints if e['task']['raw_text'] != SHARED_TEXT)
        steps = [
            dict(id="inspect_A", kind="inspect", question="读取第一项工作的 owner 原文。",
                 handles=[a], source_handles=[], depends_on=[], predicate="", field="owner",
                 target_kind="", missing_entities=[]),
            dict(id="inspect_B", kind="inspect", question="读取第二项工作的 owner 原文。",
                 handles=[b], source_handles=[], depends_on=[], predicate="", field="owner",
                 target_kind="", missing_entities=[]),
            dict(id="same_A", kind="relation", question="第一项工作与外部记录是否是同一业务任务？",
                 handles=[a, a2], source_handles=[], depends_on=["inspect_A"], predicate="SAME_TASK",
                 field="", target_kind="", missing_entities=[]),
            dict(id="same_B", kind="relation", question="第二项工作与外部记录是否是同一业务任务？",
                 handles=[b, a2], source_handles=[], depends_on=["inspect_B"], predicate="SAME_TASK",
                 field="", target_kind="", missing_entities=[]),
            dict(id="different_work", kind="relation", question="共用角色引文的两个工作是否有独立 P4 同任务证明？",
                 handles=[a, b], source_handles=[], depends_on=["inspect_A"], predicate="SAME_TASK",
                 field="", target_kind="", missing_entities=[]),
        ]
        return ModelResponse(
            action=ModelAction.tool(ToolCall("joint-planner", request.available_tools[0].tool_id,
                                              dict(steps=steps, uncertainties=["仅 A 有外部同任务声明，B 的关系保持未知。"],
                                                   refusal_reasons=[]))),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )


class JointSemanticsStage:
    stage = 'semantics'
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request.to_dict() if hasattr(request, "to_dict") else {})
        payload = json.loads(request.user_input.split("AUTHORIZED_SNAPSHOT_JSON\n", 1)[1])
        proposals = [
            dict(predicate=query["predicate"], subject=query["subject"], object=query["object"],
                 proposed_verdict="UNKNOWN", supporting_evidence=[
                     dict(span_id=span["span_id"], quote=span["source"]["quote"])
                     for span in payload["allowed_evidence"]
                 ], counter_evidence=[], missing_evidence=["语义模型不替代 P4 关系证明。"],
                 reason_codes=["MODEL_PROPOSED_ONLY"], changed_fields=[])
            for query in payload["queries"]
        ]
        return ModelResponse(
            action=ModelAction.tool(ToolCall("joint-semantics", request.available_tools[0].tool_id,
                                              {"proposals": proposals})),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )


class JointTransport(ScriptedGeminiTransport):
    """Use the normal Gemini wire adapter but select only A for inspection."""

    def __init__(self, stages, anchor_id):
        super().__init__(stages)
        self.anchor_id = anchor_id

    def generate_content(self, *, model, contents, config):
        response = super().generate_content(model=model, contents=contents, config=config)
        for candidate in response.get("candidates", ()):
            parts = candidate.get("content", {}).get("parts", ())
            for part in parts:
                call = part.get("function_call")
                if call and call.get("name") == "inspect_task_context":
                    call.setdefault("args", {})["anchor_ids"] = [self.anchor_id]
        return response


def _prepare_joint_product(root):
    app = ProductApplication(database_path=root / "product.sqlite", extractor=None)
    workspace = app.repository.create_workspace("Synthetic P2 joint source")["workspace_id"]
    app.repository.select_workspace(workspace)
    shared_id = "src_" + uuid4().hex
    match_id = "src_" + uuid4().hex
    shared = RawArtifact(workspace, shared_id, "timestamped_text", SHARED_SOURCE,
                         source_uri="product://joint-shared.txt")
    matched = RawArtifact(workspace, match_id, "timestamped_text", MATCH_SOURCE,
                          source_uri="product://joint-match.txt")
    for artifact, name in ((shared, "joint-shared.txt"), (matched, "joint-match.txt")):
        app.repository.create_source(workspace, artifact.artifact_id, name, artifact,
                                     tuple(s.evidence_span for s in parse_source_text(artifact).segments))
        app.repository.set_ingestion_result(workspace, artifact.artifact_id,
                                            artifact.artifact_version_id, status="COMPLETE",
                                            candidate_count=0, review_required_count=0)
    from linkloom.ui.task_investigation import ProductTaskSources

    sources = ProductTaskSources(workspace, app.snapshot_investigation_sources(workspace)[1],
                                 lambda: app.snapshot_investigation_sources(workspace)[1])
    shared_segments = parse_source_text(shared).segments
    match_segment = parse_source_text(matched).segments[0]
    proposals = [
        TaskProposal.from_dict(_field_bound_claim(
            SHARED_TEXT,
            WORK_A, ordinal=0)["task"]),
        TaskProposal.from_dict(_field_bound_claim(
            SHARED_TEXT,
            WORK_B, ordinal=0)["task"]),
        TaskProposal.from_dict(_field_bound_claim(
            "任务 FLOW-31：兼容矩阵核验，项目：星河项目，负责人：乔雨，截止日期：2037-08-19。",
            WORK_A, ordinal=0, source_text_owner=True)["task"]),
    ]
    observations = (
        build_task_observation(proposals[0], shared_segments[0], shared, identity_version="v2"),
        build_task_observation(proposals[1], shared_segments[0], shared, identity_version="v2"),
        build_task_observation(proposals[2], match_segment, matched, identity_version="v2"),
    )
    assert all(observation.verify(artifact) for observation, artifact in
               zip(observations, (shared, shared, matched)))
    registry = SourceReferenceRegistry()
    for artifact in (shared, matched):
        for segment in parse_source_text(artifact).segments:
            registry.register_evidence(workspace, segment.evidence_span.evidence_ref,
                                       content_hash=artifact.content_hash,
                                       source_version=artifact.artifact_version_id)
    identity = assess_same_task(observations[0], observations[2],
                                artifacts=(shared, matched), source_registry=registry,
                                context_observations=observations)
    assert identity.verdict == "SUPPORTED", identity.to_dict()
    assert identity.subject in (ObservationRef.from_observation(observations[0]),
                                ObservationRef.from_observation(observations[2]))
    sidecar = root / "relations.sqlite"
    with TaskRelationStore(sidecar) as store:
        store.put_assessment(identity)

    stages = [JointDiscoveryStage(), JointPlanningStage(), JointSemanticsStage()]
    # The planning fixture needs the exact anchor ref before the Provider wire
    # starts; it is the same immutable v2 ref F will build from the source.
    transport = JointTransport(stages, observations[0].observation_id)
    # Exact existing P2-A HTTP fixture budget; no integration budget increase.
    budget = replace(limits(), max_requests=10, max_input_tokens=500000,
                     max_output_tokens=65536, max_cost_usd=3, max_seconds=90,
                     request_timeout_seconds=10)
    service = compose_task_provider(app, GeminiProviderAdapter(transport), budget)
    service.task_relation_paths = {workspace: sidecar}
    return app, service, transport, workspace, observations, sidecar


def _start(base, workspace):
    status, config = _client(base, "/api/product/ask/config")
    assert status == 200, config
    context = config["investigation_context"]
    return _client(base, "/api/product/ask", method="POST", data=dict(
        query="请分别核对两项工作并调查第一项工作的同任务关系。",
        workspace_id=workspace,
        allow_external_provider=True,
        investigation_context=dict(domain="tasks", snapshot_hash=context["snapshot_hash"]),
    ))


def _poll(base, workspace, result):
    from time import monotonic, sleep

    deadline = monotonic() + 120
    while result["status"] == "INVESTIGATING" and monotonic() < deadline:
        sleep(.05)
        status, payload = _client(base,
                                  f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
        assert status == 200, payload
        result = payload["result"]
    assert result["status"] != "INVESTIGATING", result
    return result


def _ledger_for(root, run_id):
    pointer = json.loads((root / "work" / "investigations" / f"{run_id}.json").read_text(encoding="utf8"))
    runtime_root = root / "work" / "investigations" / pointer["root"] / "runtime"
    state = SQLiteCheckpointer(runtime_root).get_latest(pointer["thread_id"])
    assert state is not None
    return pointer, state, [record.to_dict() for record in state.tool_ledger]


def _persist_receipt(value, suffix=''):
    target = os.environ.get("LINKLOOM_P2_JOINT_RECEIPT", "").strip()
    if target:
        path = Path(target)
        if suffix:
            path = path.with_stem(path.stem + suffix)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf8")


def _run_joint(root, *, persist=True):
    app, service, transport, workspace, observations, sidecar = _prepare_joint_product(root)
    try:
        with product_http(app, service) as base:
            status, body = _start(base, workspace)
            assert status in {200, 202}, body
            result = _poll(base, workspace, body["result"])
            hot_status, hot = _client(base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}")
            assert hot_status == 200, hot
        pointer, state, ledger = _ledger_for(root, result["agent_run_id"])
        if persist and os.environ.get('LINKLOOM_P2_JOINT_RECEIPT'):
            from tests.m3p_support import cold_read
            status, cold = cold_read(app.repository.database_path, result['agent_run_id'], workspace, sidecar)
            prep = root / 'work' / 'investigations' / pointer['root'] / 'preparation' / 'preparation.json'
            _persist_receipt(dict(http=result, ledger=ledger, ledger_sha256=canonical_hash(ledger),
                reviewer=result.get('reviewer'), cold_http_status=status, cold=cold,
                preparation=json.loads(prep.read_text(encoding='utf8')),
                source_module=__import__('linkloom.tools.task_relations', fromlist=['']).__file__))
        return app, service, transport, workspace, observations, sidecar, result, hot, pointer, state, ledger
    except Exception:
        app.close()
        raise


def test_joint_product_http_p4_runtime_reviewer_and_fresh_cold_read(tmp_path):
    (app, _service, transport, workspace, observations, sidecar, result, hot, pointer,
     state, ledger) = _run_joint(tmp_path)
    try:
        inspect_record, query_record, semantic_record = ledger
        assert [row["tool_id"] for row in ledger] == [
            "inspect_task_context", "query_task_relations", "analyze_task_semantics"
        ]
        assert all(row["status"] == "completed" for row in ledger)
        inspected = inspect_record["result"]["value"]
        assert len(inspected["observations"]) == 1
        inspected_ref = inspected["observations"][0]["ref"]
        assert set(inspected_ref) == {
            "workspace_id", "observation_id", "artifact_id", "artifact_version_id",
            "evidence_ref", "content_hash", "payload_hash",
        }
        assert inspected_ref == ObservationRef.from_observation(observations[0]).to_dict()
        assert observations[0].observation_id != observations[1].observation_id
        assert all(o.observation_id.startswith('task_observation_v2_') for o in observations)
        assert observations[0].raw_text == observations[1].raw_text == SHARED_TEXT
        assert observations[0].provenance == observations[1].provenance
        owner_proofs = [next(e for e in o.field_evidence if e.field == 'owner') for o in observations[:2]]
        assert owner_proofs[0] == owner_proofs[1]
        receipts = inspected["investigation_receipts"]
        assert [receipt["step_id"] for receipt in receipts] == ["inspect_A"]
        assert observations[1].observation_id not in {
            row["ref"]["observation_id"] for row in inspected["observations"]
        }

        query = query_record["result"]["value"]
        steps = {row["step_id"]: row for row in query["investigation_steps"]}
        assert steps["same_A"]["status"] == "SUPPORTED"
        assert steps["same_A"]["dependency_outcomes"] == {"inspect_A": "COMPLETED"}
        assert steps["same_A"]["current_supported"] is True
        assert steps["same_A"]["relation_reads"]
        assert steps["same_A"]["relation_reads"][0]["subject"] in (
            {'observation_ref': 'ref_' + canonical_hash(ObservationRef.from_observation(observations[0]).to_dict())},
            {'observation_ref': 'ref_' + canonical_hash(ObservationRef.from_observation(observations[2]).to_dict())},
        )
        assert steps["same_B"]["status"] == "BLOCKED"
        assert steps["same_B"]["dependency_outcomes"] == {"inspect_B": "BLOCKED"}
        assert "DEPENDENCY_NOT_SUPPORTED:inspect_B" in steps["same_B"]["blocking_reasons"]
        assert not steps["same_B"]["assessment_ids"]
        assert not steps["same_B"]["relation_reads"]
        assert steps['different_work']['dependency_outcomes'] == {'inspect_A': 'COMPLETED'}
        assert steps['different_work']['status'] in {'UNKNOWN', 'BLOCKED'}
        assert not steps['different_work']['assessment_ids']
        assert not steps['different_work']['current_supported']

        assert result["status"] == "PARTIAL"
        assert result["reviewer"]["accepted"] is True
        assert result["task_review"] == hot["result"]["task_review"]
        assert semantic_record["result"]["value"]["analysis_status"] == "EXECUTED"
        request_count = len(transport.requests)

        from tests.m3p_support import cold_read

        persisted_status, persisted = cold_read(
            app.repository.database_path, result["agent_run_id"], workspace, sidecar
        )
        assert persisted_status == 200
        fresh_status, fresh = _fresh_cold_read(
            app.repository.database_path, result["agent_run_id"], workspace, sidecar
        )
        assert fresh_status == 200
        for cold in (persisted, fresh):
            assert cold["result"]["task_review"] == result["task_review"]
            assert cold["result"]["reviewer"] == result["reviewer"]
            assert cold["result"]["status"] == result["status"]
        _persist_receipt(dict(
            http=result, hot=hot, ledger=ledger, reviewer=result["reviewer"],
            persisted=persisted, fresh_process=fresh,
            inspected_observation_ref=inspected_ref,
            p4_relation=steps["same_A"], wrong_work=steps["same_B"],
            provider_requests=len(transport.requests), checkpoint=pointer,
            ledger_sha256=canonical_hash(ledger),
            no_p4_for_shared_roles=steps['different_work'],
        ))
        assert request_count == len(transport.requests)
    finally:
        app.close()


def test_joint_product_old_inspection_cannot_bypass_source_version_change(tmp_path):
    (app, service, transport, workspace, _observations, sidecar, result, _hot,
     _pointer, _state, _ledger) = _run_joint(tmp_path, persist=False)
    try:
        request_count = len(transport.requests)
        source = app.snapshot_investigation_sources(workspace)[1][0]
        artifact = app._artifact_from_version(
            dict(source, content=source["content"] + "来源已更新，旧 inspection 不再适用。")
        )
        app.repository.update_source(
            workspace, source["source_id"], source["name"], artifact,
            tuple(segment.evidence_span for segment in parse_source_text(artifact).segments),
        )
        # The source update invalidates both the old inspection receipt and its
        # dependent relation before either hot or cold projection can expose it.
        with product_http(app, service) as base:
            status, blocked = _client(
                base,
                f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id={workspace}",
            )
        assert status == 200, blocked
        assert blocked["result"]["status"] == "BLOCKED", blocked
        assert not blocked["result"].get("task_review")
        assert not blocked["result"].get("trace")
        assert not blocked["result"].get("evidence")
        from tests.m3p_support import cold_read

        cold_status, cold = cold_read(
            app.repository.database_path, result["agent_run_id"], workspace, sidecar
        )
        assert cold_status == 200, cold
        assert cold["result"]["status"] == "BLOCKED", cold
        assert not cold["result"].get("task_review")
        assert not cold["result"].get("trace")
        assert not cold["result"].get("evidence")
        fresh_status, fresh = _fresh_cold_read(
            app.repository.database_path, result['agent_run_id'], workspace, sidecar)
        assert fresh_status == 200 and fresh['result']['status'] == 'BLOCKED'
        assert not fresh['result'].get('task_review')
        assert not fresh['result'].get('trace') and not fresh['result'].get('evidence')
        assert len(transport.requests) == request_count
        _persist_receipt(dict(previous_http=result, ledger=_ledger,
            ledger_sha256=canonical_hash(_ledger), changed_source_version=artifact.artifact_version_id,
            hot=blocked, cold=cold, fresh_process=fresh, provider_requests_unchanged=True), '-source-change')
    finally:
        app.close()
