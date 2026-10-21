"""Receipt-only projection for the existing RetrievalAgent and Reviewer."""
from __future__ import annotations

import json

from linkloom.runtime.errors import ValidationError


def _parse_final_json(final_answer):
    """Parse the exact receipt object, tolerating one JSON Markdown fence."""
    if not isinstance(final_answer, str):
        raise ValueError("Final answer must be text")
    text = final_answer.strip()
    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
        text = "\n".join(lines[1:-1]).strip()
    return json.loads(text)


def project_task_relation_result(final_answer, records, visible_results, workspace_id, *, require_semantic=False, prior_visible_results=()):
    try:
        final = _parse_final_json(final_answer)
        keys = ("context_call_id", "relations_call_id", "semantic_call_id") if require_semantic else ("context_call_id", "relations_call_id")
        tool_ids = ("inspect_task_context", "query_task_relations", "analyze_task_semantics") if require_semantic else ("inspect_task_context", "query_task_relations")
        if (not isinstance(final, dict) or set(final) != set(keys)
                or any(not isinstance(v, str) for v in final.values())):
            raise ValueError("receipt Final shape")
        by_id = {r.call_id: r for r in records}
        selected = [by_id[final[key]] for key in keys]
        for record, tool_id in zip(selected, tool_ids):
            visible = list(visible_results) + (list(prior_visible_results) if require_semantic and tool_id != "analyze_task_semantics" else [])
            if (record.tool_id != tool_id or record.status != "completed"
                    or record.result.get("status") != "ok"
                    or record.result.get("value", {}).get("workspace_id") != workspace_id
                    or record.result not in visible):
                raise ValueError("unobserved or unsuccessful tool receipt")
        if any(a.sequence >= b.sequence for a, b in zip(selected, selected[1:])):
            raise ValueError("inspect must precede relation query")
        context, relations = [r.result["value"] for r in selected[:2]]
        output = {"schema_version": "task-relations-result/v1", "workspace_id": workspace_id,
                "answer_status": "PARTIAL", "context": context, "relation_query": relations,
                "receipts": [{"call_id": r.call_id, "tool_id": r.tool_id,
                              "arguments": r.arguments, "sequence": r.sequence} for r in selected],
                "limitations": ["BOUNDED_TOOL_RECEIPT_PROJECTION", "LLM_SEMANTIC_PROPOSALS_NOT_CONNECTED",
                                "UNKNOWN_IS_NOT_BUSINESS_REFUTATION"]}
        if require_semantic:
            analysis = selected[2].result["value"]
            blocked = analysis["analysis_status"] != "EXECUTED" or any(p["evidence_status"] != "EVIDENCE_VALID" for p in analysis["proposals"])
            output.update(semantic_analysis=analysis, investigation_status="BLOCKED" if blocked else "PENDING_SEMANTIC_REVIEW",
                limitations=["BOUNDED_TOOL_RECEIPT_PROJECTION", "UNKNOWN_IS_NOT_BUSINESS_REFUTATION",
                             "MODEL_PROPOSED_ONLY", "SEMANTIC_NOT_EVALUATED", "NO_P4_PROMOTION"])
        return output
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValidationError("Task relation Final must select observed successful receipts.") from error


def review_task_relation_result(payload, tools, *, records, run_id):
    """Re-read source and lifecycle views; no semantic assertion is supplied by the model."""
    semantic_checks = []
    try:
        if payload.get("schema_version") != "task-relations-result/v1" or payload["workspace_id"] != tools.workspace_id:
            raise ValueError("workspace or schema mismatch")
        fields = ("context", "relation_query", "semantic_analysis") if tools.semantic_analysis is not None else ("context", "relation_query")
        if len(payload["receipts"]) != len(fields):
            raise ValueError("missing receipts")
        by_id = {r.call_id: r for r in records if r.run_id == run_id and r.agent_id == "retrieval_agent"}
        selected = [by_id[r["call_id"]] for r in payload["receipts"]]
        for receipt, record, field in zip(payload["receipts"], selected, fields):
            if (receipt != dict(call_id=record.call_id, tool_id=record.tool_id,
                                arguments=record.arguments, sequence=record.sequence)
                    or record.status != "completed" or record.result.get("status") != "ok"
                    or record.result.get("value") != payload[field]
                    or record.task_id != selected[0].task_id):
                raise ValueError("receipt differs from current run ledger")
        if any(a.sequence >= b.sequence for a, b in zip(selected, selected[1:])):
            raise ValueError("receipt sequence changed")
        keys = ("context_call_id", "relations_call_id", "semantic_call_id") if tools.semantic_analysis is not None else ("context_call_id", "relations_call_id")
        expected_payload = project_task_relation_result(json.dumps(dict(zip(keys, (r.call_id for r in selected)))),
            selected, [r.result for r in selected], tools.workspace_id, require_semantic=tools.semantic_analysis is not None)
        if payload != expected_payload:
            raise ValueError("result differs from its fixed ledger projection")
        expected = (("inspect_task_context", "context"), ("query_task_relations", "relation_query"))
        for receipt, (tool_id, field) in zip(payload["receipts"], expected):
            if receipt["tool_id"] != tool_id:
                raise ValueError("unexpected receipt tool")
            executor = getattr(tools, tool_id)
            if executor(**receipt["arguments"]) != payload[field]:
                return {"status": "fail", "reason": "task_source_or_lifecycle_changed"}
        semantic = tools.semantic_analysis
        if len(payload["receipts"]) != (3 if semantic is not None else 2):
            raise ValueError("missing receipts")
        if semantic is not None:
            receipt = payload["receipts"][2]
            if receipt["tool_id"] != "analyze_task_semantics":
                raise ValueError("wrong semantic receipt")
            check = semantic.review(payload["semantic_analysis"], call_id=receipt["call_id"])
            if check["status"] != "pass":
                return check
            semantic_checks = check["checks"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return {"status": "fail", "reason": "task_relation_review_failed"}
    return {"status": "pass", "result_type": "task-relations-result/v1",
            "checks": ["CURRENT_SOURCE_VERSIONS", "P4_LIFECYCLE_REPLAY", "RECEIPT_PROJECTION", *semantic_checks]}
