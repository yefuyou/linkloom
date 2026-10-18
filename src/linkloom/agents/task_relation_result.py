"""Receipt-only projection for the existing RetrievalAgent and Reviewer."""
from __future__ import annotations

import json

from linkloom.runtime.errors import ValidationError


def project_task_relation_result(final_answer, records, visible_results, workspace_id):
    try:
        final = json.loads(final_answer)
        if (not isinstance(final, dict) or set(final) != {"context_call_id", "relations_call_id"}
                or any(not isinstance(v, str) for v in final.values())):
            raise ValueError("receipt Final shape")
        by_id = {r.call_id: r for r in records}
        selected = [by_id[final[key]] for key in ("context_call_id", "relations_call_id")]
        for record, tool_id in zip(selected, ("inspect_task_context", "query_task_relations")):
            if (record.tool_id != tool_id or record.status != "completed"
                    or record.result.get("status") != "ok"
                    or record.result.get("value", {}).get("workspace_id") != workspace_id
                    or record.result not in visible_results):
                raise ValueError("unobserved or unsuccessful tool receipt")
        if selected[0].sequence >= selected[1].sequence:
            raise ValueError("inspect must precede relation query")
        context, relations = [r.result["value"] for r in selected]
        return {"schema_version": "task-relations-result/v1", "workspace_id": workspace_id,
                "answer_status": "PARTIAL", "context": context, "relation_query": relations,
                "receipts": [{"call_id": r.call_id, "tool_id": r.tool_id,
                              "arguments": r.arguments} for r in selected],
                "limitations": ["BOUNDED_TOOL_RECEIPT_PROJECTION", "LLM_SEMANTIC_PROPOSALS_NOT_CONNECTED",
                                "UNKNOWN_IS_NOT_BUSINESS_REFUTATION"]}
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise ValidationError("Task relation Final must select observed successful receipts.") from error


def review_task_relation_result(payload, tools):
    """Re-read source and lifecycle views; no semantic assertion is supplied by the model."""
    try:
        if payload.get("schema_version") != "task-relations-result/v1" or payload["workspace_id"] != tools.workspace_id:
            raise ValueError("workspace or schema mismatch")
        expected = (("inspect_task_context", "context"), ("query_task_relations", "relation_query"))
        for receipt, (tool_id, field) in zip(payload["receipts"], expected):
            if receipt["tool_id"] != tool_id:
                raise ValueError("unexpected receipt tool")
            executor = getattr(tools, tool_id)
            if executor(**receipt["arguments"]) != payload[field]:
                return {"status": "fail", "reason": "task_source_or_lifecycle_changed"}
        if len(payload["receipts"]) != 2:
            raise ValueError("missing receipts")
    except (ValueError, KeyError, TypeError, AttributeError):
        return {"status": "fail", "reason": "task_relation_review_failed"}
    return {"status": "pass", "result_type": "task-relations-result/v1",
            "checks": ["CURRENT_SOURCE_VERSIONS", "P4_LIFECYCLE_REPLAY", "RECEIPT_PROJECTION"]}
