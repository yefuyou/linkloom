"""Public synthetic contract harness, never a semantic model or quality proxy."""
from datetime import UTC, datetime
import hashlib
import json

from linkloom.semantic_ingestion.artifacts import RawArtifact
from tests.unit.test_task_observation_discovery import claim, setup


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def artifact_for(case):
    content = "\n".join(f"[2031-03-01T09:{i:02d}:00Z] {m['speaker']}: {m['text']}"
                        for i, m in enumerate(case["messages"]))
    return RawArtifact("m3n-public", case["id"], "timestamped_text", content,
                       ingestion_time=datetime(2031, 3, 1, tzinfo=UTC))


def evaluate(dataset, *, task_profile=None):
    rows = []
    totals = dict(expected=0, accepted=0, matched=0, false_rejections=0,
                  false_acceptances=0, erroneous_groups=0, fields_correct=0,
                  fields_total=0, evidence_valid=0, evidence_total=0, calls=0)
    for case in dataset["cases"]:
        artifact = artifact_for(case)
        # Gold-shaped ORACLE proposals exercise transport/projection only. Hostile
        # cropped negative proposals exercise the existing refusal boundary.
        proposals = [claim(p["text"], *p["description_parts"], **{
            k: p[k] for k in ("owner", "deadline_text", "project", "vendor")})
            for p in case["expected"]]
        proposals += [claim(part, part) for part in case["rejected_descriptions"]]
        service, provider, registry, _ = setup(artifact, proposals)
        if task_profile is not None:
            from linkloom.semantic_ingestion.task_observation_discovery import TaskObservationDiscoveryService
            service = TaskObservationDiscoveryService(service.extractor, source_registry=registry,
                current_source=service.current_source, task_profile=task_profile)
        result = service.discover((artifact,), workspace_id=artifact.workspace_id)
        found = result.valid_observations + result.partial_observations
        matched, field_correct, field_total, used = 0, 0, 0, set()
        matches = {}
        for index, expected in enumerate(case["expected"]):
            candidates = [o for o in found if o.raw_text == expected["text"]
                          and [e.quote for e in o.field_evidence if e.field == "description"]
                          == expected["description_parts"] and o.observation_id not in used]
            if not candidates:
                continue
            observation = candidates[0]
            used.add(observation.observation_id)
            matches[observation.observation_id] = index
            matched += 1
            deadline = expected["deadline_text"]
            expected_fields = {k: expected[k] for k in ("owner", "deadline_text", "project", "vendor")}
            expected_fields["deadline"] = deadline if deadline and len(deadline) == 10 and deadline[4] == "-" else None
            for name, value in expected_fields.items():
                field_total += 1
                field_correct += getattr(observation, name) == value
        erroneous_groups = sum(len({matches[oid] for oid in g.observation_ids if oid in matches}) > 1
                               for g in result.groups)
        valid = sum(o.verify(artifact) and o.authority == "SUPPORTING_ONLY"
                    and o.relation_status == "UNCONFIRMED" for o in found)
        row = dict(id=case["id"], category=case["category"], expected=len(case["expected"]),
            accepted=len(found), matched=matched, false_rejections=len(case["expected"])-matched,
            false_acceptances=len(found)-matched, erroneous_groups=erroneous_groups,
            fields_correct=field_correct, fields_total=field_total,
            evidence_valid=valid, evidence_total=len(found), calls=len(provider.requests),
            request_sha256=[hashlib.sha256(json.dumps(r.to_dict(), sort_keys=True,
                ensure_ascii=False, separators=(",", ":")).encode()).hexdigest() for r in provider.requests],
            prompt_sha256=[r.receipt.prompt_sha256 for r in result.extractions],
            observations=[o.to_dict() for o in found], failures=[f.reason for f in result.failures])
        for key in totals:
            totals[key] += row[key]
        rows.append(row)
    totals.update(recall=totals["matched"]/totals["expected"] if totals["expected"] else None,
        precision=totals["matched"]/totals["accepted"] if totals["accepted"] else None,
        field_accuracy=totals["fields_correct"]/totals["fields_total"] if totals["fields_total"] else None,
        evidence_validity=totals["evidence_valid"]/totals["evidence_total"] if totals["evidence_total"] else None)
    return dict(totals=totals, cases=rows)
