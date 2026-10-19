"""Inspect frozen F/J artifacts and re-submit their SAME claims locally only."""
from pathlib import Path
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]
from tests.m3n_contract_support import artifact_for
from tests.unit.test_task_observation_discovery import setup
from linkloom.semantic_ingestion.extraction import TaskDiscoveryProfile
from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService


def main():
    old = ROOT / "docs/requirements/m3-task-discovery/evidence"
    paths = [old / "live" / name for name in ("request-1.json", "response-1.json", "predictions.json")]
    request, response, predictions = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    input_data = json.loads(request["user_input"].split("Input:\n", 1)[1])
    text = input_data["source_segment"]["text"]
    payload = response["proposal"]["tool_calls"][0]["runtime_call"]["arguments"]
    # Use the exact raw source string from the saved request, not a rewritten
    # conversation or labels. Only the immutable artifact identity is new here.
    from dataclasses import replace
    artifact = replace(artifact_for(dict(id="historical-diagnostic", messages=[dict(speaker="unused", text="unused")])),
                       content=text)
    rows = {}
    for name, profile in (("legacy", None), ("cross-message-v1", TaskDiscoveryProfile.CROSS_MESSAGE_V1)):
        service, provider, registry, _ = setup(artifact, payload=payload)
        if profile is not None:
            service = TaskObservationDiscoveryService(service.extractor, source_registry=registry,
                current_source=service.current_source, task_profile=profile)
        result = service.discover((artifact,), workspace_id=artifact.workspace_id)
        observations = result.valid_observations + result.partial_observations
        rows[name] = dict(descriptions=[o.description for o in observations],
            observations=[o.to_dict() for o in observations], failures=[f.reason for f in result.failures],
            prompt_sha256=result.receipts[0].prompt_sha256,
            config_sha256=result.receipts[0].config_sha256,
            extraction_fingerprint=result.receipts[0].extraction_fingerprint,
            request=provider.requests[0].to_dict(), fake_calls=len(provider.requests))
    report = dict(real_provider_requests=0, real_model_after="NOT_EVALUATED",
        frozen_files={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        second_original_message=text.splitlines()[1], input_message_count=len(text.splitlines()),
        original_window_speaker=input_data["source_segment"]["speaker"],
        original_prompt_task_contract="ONE task statement; supplied speaker; no cross-segment field carry",
        raw_claim_types=[c["claim_type"] for c in payload["claims"]],
        raw_task_parts=[c["task"]["description_parts"] for c in payload["claims"] if c.get("task")],
        omission_layer="MODEL_RAW_OUTPUT; omitted description never reaches projection or veto",
        causal_limits="Single-statement prompt and absent message map are observed constraints; no offline ablation proves their individual causal effect.",
        historical_score=json.loads((old/"m3j/replay-score.json").read_text(encoding="utf-8")),
        same_raw_response_before_after=rows,
        historical_projection_descriptions=[o["description"] for o in (
            predictions["cases"][0]["result"]["valid_observations"]+
            predictions["cases"][0]["result"]["partial_observations"])])
    output = Path(sys.argv[1])
    with output.open("x",encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"omission_layer":report["omission_layer"],"descriptions":{
        k:v["descriptions"] for k,v in rows.items()}, "real_model_after":"NOT_EVALUATED"},ensure_ascii=False))


if __name__ == "__main__":
    main()
