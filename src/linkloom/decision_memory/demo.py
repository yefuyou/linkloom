"""Deterministic current/history/evolution proof for Temporal Decision Memory."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from linkloom.decision_memory.materialization import DecisionMaterializer
from linkloom.decision_memory.models import ActionRecord, DecisionRecord, DecisionStatus
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.decision_memory.store import TemporalDecisionStore


def build_demo_report() -> dict[str, Any]:
    source_registry = SourceReferenceRegistry(
        episodes={
            "borealis": {
                "episode:comparison-review",
                "episode:final-decision",
            }
        },
        evidence={
            "borealis": {
                "requirements/vendor-selection-v1.md#L4-L10",
                "decisions/supplier-decision.md#L8-L15",
                "actions/rollout.md#L3-L8",
            }
        },
    )
    with TemporalDecisionStore(":memory:", source_registry=source_registry) as store:
        materializer = DecisionMaterializer(store)
        first = DecisionRecord(
            decision_id="supplier-a",
            workspace_id="borealis",
            subject_key="supplier-selection",
            value="Supplier A",
            status=DecisionStatus.CURRENT,
            valid_from=datetime(2026, 5, 12, tzinfo=UTC),
            valid_to=None,
            supersedes_id=None,
            source_episode_id="episode:comparison-review",
            source_evidence_refs=("requirements/vendor-selection-v1.md#L4-L10",),
            provenance_run_id="run:fixture-a",
        )
        materializer.materialize(
            materializer.propose(
                first,
                team_decision_contract_pass=True,
                grounding_pass=True,
            ),
            approved_by="fixture:temporal-demo",
        )

        second = DecisionRecord(
            decision_id="supplier-b",
            workspace_id="borealis",
            subject_key="supplier-selection",
            value="Supplier B",
            status=DecisionStatus.CURRENT,
            valid_from=datetime(2026, 6, 1, tzinfo=UTC),
            valid_to=None,
            supersedes_id="supplier-a",
            source_episode_id="episode:final-decision",
            source_evidence_refs=("decisions/supplier-decision.md#L8-L15",),
            provenance_run_id="run:fixture-b",
        )
        rollout = ActionRecord(
            action_id="supplier-b-rollout",
            workspace_id="borealis",
            description="Prepare Supplier B rollout.",
            owner="Rina",
            deadline="2026-06-15",
            status="OPEN",
            source_decision_id="supplier-b",
            source_evidence_refs=("actions/rollout.md#L3-L8",),
        )
        materializer.materialize(
            materializer.propose(
                second,
                actions=(rollout,),
                team_decision_contract_pass=True,
                grounding_pass=True,
            ),
            approved_by="fixture:temporal-demo",
        )

        current = store.get_current("borealis", "supplier-selection")
        historical = store.get_as_of(
            "borealis",
            "supplier-selection",
            datetime(2026, 5, 20, tzinfo=UTC),
        )
        evolution = store.get_evolution("borealis", "supplier-selection")
        if current is None or historical is None or len(evolution) != 1:
            raise RuntimeError("temporal demo did not produce the required query states")
        return {
            "workspace_id": "borealis",
            "subject_key": "supplier-selection",
            "current_question": {
                "question": "我们现在最终决定了什么？",
                "answer": _json_ready(asdict(current)),
            },
            "historical_question": {
                "question": "之前决定是什么？",
                "as_of": "2026-05-20T00:00:00+00:00",
                "answer": _json_ready(asdict(historical)),
            },
            "evolution_question": {
                "question": "什么时候从 A 改成了 B，证据是什么？",
                "answer": _json_ready(asdict(evolution[0])),
            },
            "relations": {
                "decision_to_evidence": list(
                    store.get_decision_evidence("borealis", "supplier-b")
                ),
                "decision_to_previous_decision": "supplier-a",
                "decision_to_action_to_owner": [
                    _json_ready(asdict(action))
                    for action in store.get_actions("borealis", "supplier-b")
                ],
            },
            "write_policy": {
                "team_decision_contract": "PASS",
                "grounding": "PASS",
                "materialization": "explicit fixture approval",
            },
        }


def write_demo_report(output_path: str | Path) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(build_demo_report(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, DecisionStatus):
        return value.value
    return value
