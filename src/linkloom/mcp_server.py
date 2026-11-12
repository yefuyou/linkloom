"""Read-only MCP server (stdio) over the existing Product read entries.

Every tool takes ``workspace`` and only serves the Product's current workspace.
Questions are untrusted input: they are passed as data to the public Ask entry,
whose security gate and workspace check decide; refusals keep their reason code.
No tool writes, deletes or modifies records.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote, urlencode

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from linkloom.ui.product import ProductApplicationError

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
_TASK_EVIDENCE_LIMIT = 5


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _result(payload: dict[str, Any], *, is_error: bool = False) -> CallToolResult:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return CallToolResult(content=[TextContent(type="text", text=text)],
                          structured_content=payload, is_error=is_error)


def _refusal(workspace: object, code: str, message: str) -> CallToolResult:
    return _result({"workspace": workspace, "status": "REFUSED", "reason_code": code, "message": message},
                   is_error=True)


def _text(value: object, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise _Refused("INVALID_REQUEST", f"{name} must be non-empty text of at most {limit} characters.")
    return value.strip()


def _evidence(item: dict[str, Any], time: object = None) -> dict[str, Any]:
    """One shape for callers to check a claim: session, original sentence, time."""
    return {"session": item.get("source_name"), "quote": item.get("quote"),
            "time": time or item.get("meeting_time"), "source_id": item.get("source_id"),
            "line_start": item.get("line_start"), "line_end": item.get("line_end"),
            "verified": item.get("verified")}


def _decision(item: dict[str, Any]) -> dict[str, Any]:
    time = item.get("source_event_at") or item.get("valid_from")
    return {"decision_id": item["decision_id"], "project": item["subject"], "relation": item["relation"],
            "value": item["value"], "status": item["status"], "valid_from": item["valid_from"],
            "valid_to": item["valid_to"], "confirmed_at": (item.get("authorization") or {}).get("authorized_at"),
            "evidence": [_evidence(evidence, time) for evidence in item["evidence"]]}


def build_server(app, ask_service, task_api, *, allow_external_provider: bool = False) -> MCPServer:
    """Wrap existing services. The Host composes them; requests only name a workspace."""
    server = MCPServer("linkloom", instructions=(
        "Read-only access to one LinkLoom workspace. Results carry evidence (session, quote, time). "
        "A refusal or clarification carries reason_code; report it instead of retrying around it."))

    def guard(workspace: object) -> str:
        if not isinstance(workspace, str) or not workspace.strip():
            raise _Refused("WORKSPACE_REQUIRED", "Select a workspace first.")
        if workspace != app.repository.current_workspace_id():
            raise _Refused("WORKSPACE_DENIED", "Only the Product's current workspace can be read.")
        return workspace

    def same_workspace(workspace: str, rows) -> None:
        # The Product reads its current workspace; reject a switch between check and read.
        if any(row.get("workspace_id") != workspace for row in rows):
            raise _Refused("WORKSPACE_DENIED", "The current workspace changed during the request.")

    def run(workspace: object, call) -> CallToolResult:
        try:
            return _result(call())
        except _Refused as error:
            return _refusal(workspace, error.code, str(error))
        except ProductApplicationError as error:
            return _refusal(workspace, error.code, str(error))

    def task_get(path: str, **query: object) -> dict[str, Any]:
        body, status = task_api.handle("GET", "/api/product" + path + "?" + urlencode(query))
        if status != 200:
            raise _Refused(body["error"]["code"], body["error"]["message"])
        return body

    @server.tool(annotations=_READ_ONLY)
    def ask(workspace: str, question: str) -> CallToolResult:
        """Ask a natural-language question about this workspace's reviewed decisions and tasks.

        The question is untrusted input. A security or scope refusal returns its reason_code.
        """
        payload: dict[str, Any] = {"query": question, "workspace_id": workspace}
        if allow_external_provider:
            payload["allow_external_provider"] = True
        try:
            result, status = ask_service.ask(payload)
        except ProductApplicationError as error:
            return _refusal(workspace, error.code, str(error))
        router = result.get("router") or {}
        error = result.get("error") or {}
        gate = result.get("security_gate") or {}
        timeline = result.get("timeline") or {}
        times = {evidence["evidence_ref"]: item.get("source_event_at")
                 for item in [timeline.get("effective"), *timeline.get("history", [])] if item
                 for evidence in item["evidence"]}
        return _result({
            "workspace": workspace, "status": result["status"], "decision": router.get("decision"),
            "reason_code": error.get("code") or router.get("reason_code"), "message": error.get("message"),
            "conclusions": result["conclusions"],
            "evidence": [_evidence(item, times.get(item.get("evidence_ref"))) for item in result["evidence"]],
            "uncertainty": result["uncertainty"],
            "security_gate": {key: gate[key] for key in ("status", "decision", "reason_code", "fallback_reason")
                              if key in gate},
        }, is_error=status >= 400 or router.get("decision") == "REFUSE")

    @server.tool(annotations=_READ_ONLY)
    def list_decisions(workspace: str, project: str | None = None) -> CallToolResult:
        """List the currently effective, human-confirmed decisions; optionally for one project."""
        def call():
            guard(workspace)
            wanted = _text(project, "project").casefold() if project is not None else None
            current = app.handle_get("/api/decisions")["decisions"]
            same_workspace(workspace, current)
            decisions = []
            for subject, relation in sorted({(item["subject"], item["relation"]) for item in current}):
                if wanted is not None and subject.casefold() != wanted:
                    continue
                # recover_decision is the authority boundary: receipt and evidence are verified.
                view = app.recover_decision(subject, relation)
                same_workspace(workspace, [view])
                if view["status"] == "FOUND":
                    decisions.append(_decision(view["effective"]))
            return {"workspace": workspace, "decisions": decisions}
        return run(workspace, call)

    @server.tool(annotations=_READ_ONLY)
    def get_decision_history(workspace: str, project: str, relation: str) -> CallToolResult:
        """Change history of one decision slot (project + relation), oldest first."""
        def call():
            guard(workspace)
            view = app.recover_decision(_text(project, "project"), _text(relation, "relation"))
            same_workspace(workspace, [view])
            return {"workspace": workspace, "project": view["subject"], "relation": view["relation"],
                    "status": view["status"], "history": [_decision(item) for item in view["history"]],
                    "transitions": [{"from_value": item["from_value"], "to_value": item["to_value"],
                                     "effective_at": item["effective_at"]} for item in view["transitions"]]}
        return run(workspace, call)

    @server.tool(annotations=_READ_ONLY)
    def list_tasks(workspace: str, status: str | None = None, owner: str | None = None,
                   limit: int = 20, offset: int = 0) -> CallToolResult:
        """List confirmed tasks of this workspace, with the source sentences they came from."""
        def call():
            guard(workspace)
            filters = {key: _text(value, key) for key, value in (("status", status), ("owner", owner))
                       if value is not None}
            listing = task_get("/tasks", workspace_id=workspace, limit=limit, offset=offset, **filters)
            same_workspace(workspace, [item["task"] for item in listing["items"]])
            tasks = []
            for item in listing["items"]:
                task = item["task"]
                events = task_get("/tasks/" + quote(task["id"], safe="") + "/events", workspace_id=workspace)
                evidence = [_evidence(entry) for event in events["items"] for entry in event["after_evidence"]]
                tasks.append({"task_id": task["id"], "title": task["title"], "owner": task["owner"],
                              "deadline": task["deadline"], "status": task["status"],
                              "updated_at": task["updated_at"], "evidence": evidence[:_TASK_EVIDENCE_LIMIT]})
            return {"workspace": workspace, "tasks": tasks, "page": listing["page"]}
        return run(workspace, call)

    return server


def main() -> int:
    import argparse
    import os
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False, verbose=False)
    parser = argparse.ArgumentParser(prog="python -m linkloom.mcp_server")
    parser.add_argument("--database", default=os.environ.get(
        "LINKLOOM_DATABASE_PATH", str(Path.home() / ".linkloom" / "product.sqlite")),
        help="Existing local SQLite product store (or set LINKLOOM_DATABASE_PATH).")
    parser.add_argument("--allow-external-provider", action="store_true",
        help="Let Ask send the question to the configured external router/security model.")
    args = parser.parse_args()
    if not Path(args.database).expanduser().is_file():
        parser.error(f"product database not found: {args.database}")

    from linkloom.ui.investigation import ProductInvestigationService
    from linkloom.ui.product import ProductApplication
    from linkloom.ui.router_provider import configured_router
    from linkloom.ui.task_api import TaskProductAPI
    from linkloom.ui.unified_ask import UnifiedAskService

    app = ProductApplication(database_path=str(Path(args.database).expanduser()), extractor=None)
    task_api = TaskProductAPI(app.repository.database_path)
    try:
        # Same no-provider composition as the Product UI: saved state stays readable, no new agent runs.
        investigation = ProductInvestigationService(
            app, model_factory=None, run_mode="MODEL_NOT_CONFIGURED", external_provider=False)
        ask_service = UnifiedAskService(app, lambda: investigation, router_provider=configured_router())
        build_server(app, ask_service, task_api,
                     allow_external_provider=args.allow_external_provider).run(transport="stdio")
    finally:
        task_api.close()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
