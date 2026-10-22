"""Explicit offline task investigation demo wiring for the local Product UI.

The fixture is intentionally small and public.  It uses deterministic stage
adapters and a deterministic runtime FakeModel; it never creates provider
requests, writes task relation records, or presents a business approval action.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from time import monotonic, sleep
from uuid import uuid4

from linkloom.agents.model_adapter import (
    FakeModelAdapter,
    ModelAction,
    ModelResponse,
    ModelUsage,
)
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.tools.contracts import ToolCall
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.product import ProductApplication
from linkloom.ui.task_investigation import TaskInvestigationModels


PUBLIC_TASK_TEXTS = (
    "[2029-06-01T09:00:00Z] 林枫: 项目：青竹；负责人：林枫；完成日志迁移；截止日期：2029-06-20。",
    "[2029-06-04T09:00:00Z] 林枫: 延续上次的完成日志迁移，截止日期调整为2029-06-24；项目：青竹；负责人：林枫。",
)
DEMO_WORKSPACE_NAME = "Public offline task investigation"
DEMO_SOURCE_URI_PREFIX = "public-offline-task://"


def _existing_demo_workspace(database: Path) -> str | None:
    """Reject a non-demo SQLite store before ProductApplication can touch it."""
    if str(database) == ":memory:" or not database.exists() or database.stat().st_size == 0:
        return None
    try:
        connection = sqlite3.connect(
            f"file:{database.resolve().as_posix()}?mode=ro", uri=True,
        )
    except sqlite3.Error as exc:
        raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED") from exc
    try:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not tables:
            return None
        if "ll_workspace" not in tables:
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        workspaces = connection.execute(
            "SELECT workspace_id, name FROM ll_workspace ORDER BY workspace_id"
        ).fetchall()
        if any(name != DEMO_WORKSPACE_NAME for _, name in workspaces):
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        if len(workspaces) > 1:
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        if not workspaces:
            return None
        if "ll_source_version" not in tables:
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        source_uris = connection.execute(
            "SELECT source_uri FROM ll_source_version"
        ).fetchall()
        if any(not str(uri).startswith(DEMO_SOURCE_URI_PREFIX) for (uri,) in source_uris):
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        return str(workspaces[0][0])
    finally:
        connection.close()


def _claim(text: str, *parts: str, owner: str | None = None,
           project: str | None = None, deadline_text: str | None = None) -> dict:
    return {
        "claim_type": "PLAN",
        "subject": None,
        "relation": None,
        "value": None,
        "temporal_status": "NOT_STATED",
        "valid_from": None,
        "valid_to": None,
        "confidence": {key: 0.9 for key in ("claim_type", "entity", "relation", "temporal", "overall")},
        "task": {
            "text": text,
            "description_parts": list(parts),
            "owner": owner,
            "project": project,
            "vendor": None,
            "deadline_text": deadline_text,
        },
    }


class _OfflineStage:
    """Scripted public inputs for F/G; this class does no semantic inference."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        self.requests: list[dict] = []

    def complete(self, request):
        self.requests.append(request.to_dict())
        if self.stage == "discovery":
            source_index = next(
                index for index, text in enumerate(PUBLIC_TASK_TEXTS)
                if text.split(": ", 1)[1] in request.user_input
            )
            text = PUBLIC_TASK_TEXTS[source_index].split(": ", 1)[1]
            arguments = {
                "claims": [_claim(
                    text,
                    "完成日志迁移",
                    owner="林枫",
                    project="青竹",
                    deadline_text="2029-06-24" if source_index == 1 else "2029-06-20",
                )]
            }
        elif self.stage == "planning":
            payload = json.loads(request.user_input.split("INPUT_JSON\n", 1)[1])
            handles = [endpoint["handle"] for endpoint in payload["endpoints"]]
            steps = [
                dict(
                    id="identity",
                    kind="relation",
                    question="两次会议是否延续同一交付任务？",
                    handles=handles[:2],
                    source_handles=[],
                    depends_on=[],
                    predicate="SAME_TASK",
                    field="",
                    target_kind="",
                    missing_entities=[],
                ),
                dict(
                    id="revision",
                    kind="relation",
                    question="后一次会议是否修订前一计划？",
                    handles=list(reversed(handles[:2])),
                    source_handles=[],
                    depends_on=["identity"],
                    predicate="REVISES_PLAN",
                    field="",
                    target_kind="",
                    missing_entities=[],
                ),
            ]
            arguments = {
                "steps": steps,
                "uncertainties": ["需要核对原文及现有关系记录。"],
                "refusal_reasons": [],
            }
        else:
            payload = json.loads(request.user_input.split("AUTHORIZED_SNAPSHOT_JSON\n", 1)[1])
            arguments = {
                "proposals": [
                    {
                        "predicate": query["predicate"],
                        "subject": query["subject"],
                        "object": query["object"],
                        "proposed_verdict": "SUPPORTED",
                        "supporting_evidence": [
                            {"span_id": item["span_id"], "quote": item["source"]["quote"]}
                            for item in payload["allowed_evidence"]
                        ],
                        "counter_evidence": [],
                        "missing_evidence": ["产品不创建正式关系记录"],
                        "reason_codes": ["OFFLINE_PUBLIC_FIXTURE"],
                        "changed_fields": [],
                    }
                    for query in payload["queries"]
                ]
            }
        return ModelResponse(
            action=ModelAction.tool(
                ToolCall("offline-task-stage", request.available_tools[0].tool_id, arguments)
            ),
            usage=ModelUsage(input_tokens=20, output_tokens=10, total_tokens=30),
        )


def _runtime_model(_query: str):
    """Return the deterministic outer runtime model used only by the demo."""

    def call(request, tool_id: str):
        arguments = {
            "workspace_id": next(
                tool.input_schema["properties"]["workspace_id"]["enum"][0]
                for tool in request.available_tools
                if tool.tool_id == tool_id
            )
        }
        if tool_id == "analyze_task_semantics":
            arguments["investigation_id"] = "product-investigation"
        return ModelAction.tool(ToolCall(tool_id, tool_id, arguments))

    def final(request):
        results = [entry.tool_result for entry in request.tool_history if entry.tool_result]
        if request.observation:
            results.append(request.observation)
        by_tool = {result.tool_id: result.call_id for result in results}
        keys = {
            "context_call_id": "inspect_task_context",
            "relations_call_id": "query_task_relations",
        }
        if "analyze_task_semantics" in by_tool:
            keys["semantic_call_id"] = "analyze_task_semantics"
        return ModelAction.final(json.dumps({key: by_tool[tool] for key, tool in keys.items()}))

    def third(request):
        if any(tool.tool_id == "analyze_task_semantics" for tool in request.available_tools):
            return call(request, "analyze_task_semantics")
        return final(request)

    return FakeModelAdapter([
        lambda request: call(request, "inspect_task_context"),
        lambda request: call(request, "query_task_relations"),
        third,
        final,
    ])


def build_offline_task_demo(database_path: str | Path):
    """Create an isolated ProductApplication and explicit offline task service."""

    database = Path(database_path).expanduser()
    existing_workspace_id = _existing_demo_workspace(database)
    database.parent.mkdir(parents=True, exist_ok=True)
    app = ProductApplication(database_path=database, extractor=None)
    if existing_workspace_id is None:
        workspace = app.repository.create_workspace(DEMO_WORKSPACE_NAME)
        workspace_id = workspace["workspace_id"]
    else:
        workspace = app.repository.get_workspace(existing_workspace_id)
        if workspace is None:
            app.close()
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        workspace_id = existing_workspace_id
    app.repository.select_workspace(workspace_id)
    current_sources = app.repository.list_sources(workspace_id)
    expected_uris = {f"{DEMO_SOURCE_URI_PREFIX}meeting-{index + 1}"
                     for index in range(len(PUBLIC_TASK_TEXTS))}
    if current_sources:
        current_uris = {str(source["source_uri"]) for source in current_sources}
        if current_uris != expected_uris or len(current_sources) != len(PUBLIC_TASK_TEXTS):
            app.close()
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        for index, content in enumerate(PUBLIC_TASK_TEXTS):
            source = next(source for source in current_sources
                          if source["source_uri"] == f"{DEMO_SOURCE_URI_PREFIX}meeting-{index + 1}")
            stored = app.repository.get_source(workspace_id, source["source_id"], include_content=True)
            if stored is None or stored.get("content") != content:
                app.close()
                raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
    else:
        if existing_workspace_id is not None:
            app.close()
            raise ValueError("OFFLINE_DEMO_DATABASE_NOT_DEDICATED")
        for index, content in enumerate(PUBLIC_TASK_TEXTS):
            source_id = "src_" + uuid4().hex
            artifact = RawArtifact(
                workspace_id,
                source_id,
                "timestamped_text",
                content,
                source_uri=f"{DEMO_SOURCE_URI_PREFIX}meeting-{index + 1}",
            )
            app.repository.create_source(
                workspace_id,
                source_id,
                f"offline-meeting-{index + 1}.txt",
                artifact,
                tuple(segment.evidence_span for segment in parse_source_text(artifact).segments),
            )
            app.repository.set_ingestion_result(
                workspace_id,
                source_id,
                artifact.artifact_version_id,
                status="COMPLETE",
                candidate_count=0,
                review_required_count=0,
            )
    stages = [_OfflineStage(stage) for stage in ("discovery", "planning", "semantics")]
    service = ProductInvestigationService(
        app,
        model_factory=_runtime_model,
        run_mode="OFFLINE_TASK_DEMO",
        external_provider=False,
        task_models=TaskInvestigationModels(*stages, execution_kind="FAKE"),
    )
    return app, service


__all__ = ["PUBLIC_TASK_TEXTS", "build_offline_task_demo"]
