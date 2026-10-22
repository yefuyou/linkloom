"""Verified import of A's frozen public proposal output; no Provider connection.

The original response hash is imported metadata. We verify the committed output
file, exact normalized request, schema, source snapshot and reported usage; this
is not a new model execution or independent verification of original SDK wire.
"""
from copy import deepcopy
import hashlib
import json

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.semantic_ingestion.task_relation_semantic_proposer import TOOL_ID
from linkloom.tools.contracts import ToolCall


class RecordedSemanticAdapter:
    def __init__(self, predictions, frozen, case_id):
        if predictions["frozen_manifest_sha256"] != canonical_hash(frozen):
            raise ValueError("recorded frozen manifest mismatch")
        self.record = next(row["result"] for row in predictions["cases"] if row["case_id"] == case_id)
        self.expected = next(row for row in frozen["requests"] if row["case_id"] == case_id)
        if self.record["status"] != "EXECUTED" or self.record["request_sha256"] != self.expected["request_sha256"]:
            raise ValueError("recording has no matching executed request")
        self.model_id = predictions["model_id"]
        self.requests = []
        self.provenance = dict(execution_kind="REPLAY", original_case_id=case_id,
            predictions_sha256=canonical_hash(predictions), frozen_sha256=canonical_hash(frozen),
            original_request_sha256=self.record["request_sha256"],
            original_response_sha256_imported=self.record["response_sha256"],
            verification="COMMITTED_PUBLIC_OUTPUT; EXACT_REQUEST_SCHEMA_AND_SOURCE_SNAPSHOT; NOT_NEW_MODEL_EXECUTION")

    def complete(self, request):
        if (canonical_hash(request.to_dict()) != self.expected["request_sha256"]
                or canonical_hash(request.user_input) != self.record["prompt_sha256"]
                or canonical_hash(request.available_tools[0].input_schema) != self.expected["schema_sha256"]):
            raise ValueError("replay request or schema mismatch")
        output = self.record["untrusted_output"]
        calls = output["tool_calls"]
        if output["kind"] != "tool_calls" or len(calls) != 1 or calls[0]["tool_id"] != TOOL_ID:
            raise ValueError("invalid recorded structured emitter output")
        self.requests.append(request)
        return ModelResponse(action=ModelAction.tool(ToolCall("replayed-emitter", TOOL_ID, deepcopy(calls[0]["arguments"]))),
            usage=ModelUsage.from_dict(self.record["usage"]),
            provider_metadata=dict(model=self.model_id, model_version=self.record["proposals"][0]["actual_model_version"],
                                   execution_kind="REPLAY"),
            provider_request_id=self.record["provider_request_id"], provider_response_id=self.record["provider_response_id"])


def load_recorded_semantic_adapter(artifact_directory, case_id):
    """Only trusted host code supplies this artifact directory; no model paths."""
    directory = artifact_directory
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf8"))
    loaded = {}
    for name in ("predictions.json", "freeze.json"):
        content = (directory / name).read_bytes()
        # A recorded its Windows bytes. Git may check the same text out as LF.
        # Only line-ending conversion is permitted; all other bytes must match.
        lf = content.replace(b"\r\n", b"\n")
        variants = (content, lf, lf.replace(b"\n", b"\r\n"))
        if provenance["artifacts_sha256"][name] not in {hashlib.sha256(b).hexdigest() for b in variants}:
            raise ValueError("frozen public replay artifact changed: " + name)
        loaded[name] = json.loads(content)
    adapter = RecordedSemanticAdapter(loaded["predictions.json"], loaded["freeze.json"], case_id)
    if provenance["core_commit"] != loaded["freeze.json"]["git_base"]:
        raise ValueError("recorded source implementation commit mismatch")
    adapter.provenance["source_commit"] = provenance["core_commit"]
    adapter.provenance["imported_from_commit"] = "bd5de920e4bcf1a76c12253c7001910552baf287"
    return adapter
