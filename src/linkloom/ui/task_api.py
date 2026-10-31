"""Thin HTTP adapter for the human-reviewed formal task service."""
from __future__ import annotations

import re
import sqlite3
from threading import Lock
from urllib.parse import parse_qs, unquote, urlsplit

from linkloom.tasks.candidate_source import ProductTaskCandidateSource
from linkloom.tasks.merge import MergeBlockedError
from linkloom.tasks.models import TaskStatus
from linkloom.tasks.query import TaskQueryError
from linkloom.tasks.review import TaskReviewService
from linkloom.tasks.review_models import CandidateUnavailableError, ReviewConflictError
from linkloom.tasks.store import CandidateAlreadyReviewedError, TaskNotFoundError, TaskStore


_PREFIX = "/api/product"
_ROUTES = (
    ("GET", r"/task-candidates", "candidates"),
    ("GET", r"/task-candidates/([^/]+)/evidence", "candidate_evidence"),
    ("GET", r"/tasks", "tasks"),
    ("GET", r"/tasks/([^/]+)", "task"),
    ("GET", r"/tasks/([^/]+)/events", "events"),
    ("POST", r"/task-candidates/([^/]+)/merge-preview", "merge_preview"),
    ("POST", r"/task-candidates/([^/]+)/review", "review"),
    ("POST", r"/tasks/([^/]+)/merges/([^/]+)/undo-preview", "undo_preview"),
    ("POST", r"/tasks/([^/]+)/merges/([^/]+)/undo", "undo"),
    ("POST", r"/tasks/([^/]+)/status", "status"),
)


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + " must be non-empty text")
    return value.strip()


def _fingerprint(value, name="expected_candidate_fingerprint"):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(name + " must be a SHA-256 digest")
    return value


def _revision(value):
    value = _text(value, "expected_review_revision")
    if re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("expected_review_revision must be a revision string")
    return value


def _fields(payload, allowed, required=()):
    if not isinstance(payload, dict) or set(payload) - set(allowed) or set(required) - set(payload):
        raise ValueError("The request has missing or unsupported fields")
    return payload


def _apply_fields(value):
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in {"title", "owner", "deadline"} for item in value):
        raise ValueError("apply_fields must select title, owner or deadline")
    if len(value) != len(set(value)):
        raise ValueError("apply_fields must not contain duplicates")
    return value


def _patch(value):
    _fields(value, {"title", "owner", "deadline", "deadline_text"})
    if ("deadline" in value) != ("deadline_text" in value):
        raise ValueError("deadline and deadline_text must be supplied together")
    if "title" in value:
        _text(value["title"], "title")
    for name in ("owner", "deadline", "deadline_text"):
        if name in value and value[name] is not None:
            _text(value[name], name)
    return value


def _audit(payload):
    return {name: _text(payload.get(name), name) for name in ("reviewer", "reason")}


def _page(query):
    result = {}
    for name, default in (("limit", 20), ("offset", 0)):
        value = query.get(name, str(default))
        if re.fullmatch(r"[0-9]+", value) is None:
            raise ValueError(name + " must be an integer")
        result[name] = int(value)
    if not 1 <= result["limit"] <= 100 or not 0 <= result["offset"] <= 2**63 - 1:
        raise ValueError("limit must be 1..100 and offset must be non-negative")
    return result


def _candidate_summary(snapshot):
    candidate = snapshot["candidate"]
    return {key: value for key, value in candidate.items() if key != "evidence"} | {
        "fingerprint": snapshot["fingerprint"], "evidence_count": len(candidate["evidence"])
    }


def _preview(value):
    result = dict(value)
    for key in ("candidate", "restored_candidate"):
        if key in result:
            result[key] = _candidate_summary(result[key])
    tail = result.get("task_event_tail")
    if tail:
        result["task_event_tail"] = {key: tail[key] for key in ("id", "sequence", "event_type", "created_at")}
    return result


def _receipt(value):
    """Serialize the already-completed service result without another read."""
    if "task" not in value:
        return {key: value[key] for key in (
            "id", "task_id", "candidate_id", "candidate_fingerprint", "review_status", "reviewer", "reason", "reviewed_at"
        )} | {"operation": value["action"], "task_event_ids": value["task_event_ids"]}
    audit = value["review_history"][-1]
    result = {key: value[key] for key in (
        "task", "review_status", "review_revision", "candidate_fingerprint", "field_sources"
    )}
    result.update(review_event_id=audit["id"], operation=audit["action"],
                  task_event_ids=audit["task_event_ids"], reviewer=audit["reviewer"],
                  reason=audit["reason"], reviewed_at=audit["reviewed_at"])
    if audit["action"] in {"merge", "undo_merge"}:
        operation = value["merge_history"][-1]
        result.update(merge_id=operation["id"], target_merge_id=operation["target_merge_id"])
    return result


class TaskProductAPI:
    """The Host selects the database; requests can only select a workspace."""

    def __init__(self, database_path):
        self.database_path = str(database_path) if database_path is not None else None
        self._lock = Lock()
        self._store = None
        self._service = None

    @staticmethod
    def matches(target):
        path = urlsplit(target).path
        return any(path == root or path.startswith(root + "/") for root in (
            _PREFIX + "/task-candidates", _PREFIX + "/tasks"
        ))

    @staticmethod
    def expects_json(method, target):
        path = urlsplit(target).path
        return method == "POST" and any(verb == "POST" and re.fullmatch(_PREFIX + pattern, path)
            for verb, pattern, _name in _ROUTES)

    @property
    def service(self):
        with self._lock:
            if self._service is None:
                if not self.database_path or self.database_path == ":memory:":
                    raise TaskQueryError("TASK_API_UNAVAILABLE", "Formal tasks require the Product's durable database.")
                self._store = TaskStore(self.database_path)
                self._service = TaskReviewService(self._store, ProductTaskCandidateSource())
            return self._service

    def close(self):
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = self._service = None

    def handle(self, method, target, payload=None):
        try:
            path = urlsplit(target).path
            route = None
            recognized = False
            for verb, pattern, name in _ROUTES:
                match = re.fullmatch(_PREFIX + pattern, path)
                if match:
                    recognized = True
                    if method == verb:
                        route = (name, tuple(_text(unquote(item, errors="strict"), "resource id") for item in match.groups()))
                        break
            if route is None:
                return self._error(405 if recognized else 404,
                    "METHOD_NOT_ALLOWED" if recognized else "RESOURCE_NOT_FOUND", "The task resource or method is unavailable.")
            parsed = parse_qs(urlsplit(target).query, keep_blank_values=True, strict_parsing=True)
            if any(len(values) != 1 for values in parsed.values()):
                raise ValueError("Query parameters must not be repeated")
            query = {key: values[0] for key, values in parsed.items()}
            workspace = _text(query.get("workspace_id"), "workspace_id")
            name, identifiers = route
            allowed = {"workspace_id"}
            if name in {"candidates", "tasks", "events", "candidate_evidence"}:
                allowed |= {"limit", "offset"}
            if name == "candidates": allowed |= {"state"}
            if name == "tasks": allowed |= {"owner", "status"}
            if name == "candidate_evidence": allowed |= {"candidate_fingerprint"}
            _fields(query, allowed)
            if method == "POST" and not isinstance(payload, dict):
                raise ValueError("The request body must be a JSON object")
            service = self.service
            with service.request_scope():
                service.require_workspace(workspace)
                return self._call(service, name, workspace, identifiers, query, payload)
        except TaskQueryError as error:
            status = 404 if error.code in {"WORKSPACE_NOT_FOUND", "RESOURCE_NOT_FOUND"} else (
                503 if error.code in {"TASK_BUSY", "TASK_API_UNAVAILABLE"} else 409)
            return self._error(status, error.code, str(error), error.details)
        except MergeBlockedError as error:
            operations = error.blocking_operations
            return self._error(409, "UNDO_BLOCKED", str(error), {
                "blocking_count": len(operations), "first_blocking_operation": operations[0] if operations else None
            })
        except TaskNotFoundError:
            return self._error(404, "RESOURCE_NOT_FOUND", "The task was not found in this workspace.")
        except CandidateAlreadyReviewedError:
            return self._error(409, "ALREADY_REVIEWED", "The candidate has already been processed.")
        except CandidateUnavailableError:
            return self._error(409, "SOURCE_INVALID", "The candidate or its original source failed verification.")
        except ReviewConflictError as error:
            return self._error(409, "REVIEW_CONFLICT", str(error))
        except ValueError as error:
            return self._error(400, "INVALID_REQUEST", str(error))
        except sqlite3.OperationalError as error:
            if getattr(error, "sqlite_errorcode", None) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                return self._error(503, "TASK_BUSY", "The task database is busy. Retry after refreshing.")
            return self._error(500, "INTERNAL_ERROR", "The task request could not be completed.")
        except Exception:
            return self._error(500, "INTERNAL_ERROR", "The task request could not be completed.")

    @staticmethod
    def _error(status, code, message, details=None):
        error = {"code": code, "message": message}
        if details:
            error["details"] = details
        return {"error": error}, status

    def _call(self, service, name, workspace, ids, query, payload):
        if name == "candidates":
            state = query.get("state", "pending")
            if state not in {"pending", "processed", "all"}:
                raise ValueError("state must be pending, processed or all")
            return service.list_candidates_page(workspace, state=state, **_page(query)), 200
        if name == "candidate_evidence":
            return service.candidate_evidence_page(workspace, ids[0],
                candidate_fingerprint=_fingerprint(query.get("candidate_fingerprint"), "candidate_fingerprint"), **_page(query)), 200
        if name == "tasks":
            options = _page(query)
            if "owner" in query: options["owner"] = query["owner"] or None
            if "status" in query: options["status"] = TaskStatus(query["status"]).value
            return service.list_tasks_page(workspace, **options), 200
        if name == "task":
            return service.task_summary(workspace, ids[0]), 200
        if name == "events":
            return service.task_events_page(workspace, ids[0], **_page(query)), 200
        if name == "merge_preview":
            _fields(payload, {"task_id", "expected_candidate_fingerprint", "apply_fields"},
                    {"task_id", "expected_candidate_fingerprint", "apply_fields"})
            value = service.preview_merge(workspace, _text(payload["task_id"], "task_id"), ids[0],
                expected_candidate_fingerprint=_fingerprint(payload["expected_candidate_fingerprint"]),
                apply_fields=_apply_fields(payload["apply_fields"]))
            return _preview(value), 200
        if name == "review":
            action = payload.get("action")
            common = {"action", "expected_candidate_fingerprint", "reviewer", "reason"}
            extra = {"patch", "expected_review_revision"} if action == "create" else (
                {"task_id", "apply_fields", "expected_review_revision", "expected_preview_token"} if action == "merge" else set())
            _fields(payload, common | extra, common)
            arguments = _audit(payload) | {"expected_candidate_fingerprint": _fingerprint(payload["expected_candidate_fingerprint"])}
            if action == "create":
                if "patch" in payload: arguments["patch"] = _patch(payload["patch"])
                if "expected_review_revision" in payload: arguments["expected_review_revision"] = _revision(payload["expected_review_revision"])
                value = service.confirm(workspace, ids[0], **arguments)
                receipt = _receipt(value)
                return receipt, 201 if receipt["operation"] == "confirm" else 200
            if action == "reject":
                return _receipt(service.reject(workspace, ids[0], **arguments)), 200
            if action == "merge":
                _fields(payload, common | extra, common | extra)
                arguments.update(expected_review_revision=_revision(payload["expected_review_revision"]),
                    expected_preview_token=_fingerprint(payload["expected_preview_token"], "expected_preview_token"),
                    apply_fields=_apply_fields(payload["apply_fields"]))
                return _receipt(service.merge(workspace, _text(payload["task_id"], "task_id"), ids[0], **arguments)), 200
            raise ValueError("action must be create, merge or reject")
        if name == "undo_preview":
            _fields(payload, set())
            return _preview(service.preview_undo_merge(workspace, ids[0], ids[1])), 200
        if name == "undo":
            fields = {"expected_review_revision", "expected_preview_token", "reviewer", "reason"}
            _fields(payload, fields, fields)
            value = service.undo_merge(workspace, ids[0], ids[1], **_audit(payload),
                expected_review_revision=_revision(payload["expected_review_revision"]),
                expected_preview_token=_fingerprint(payload["expected_preview_token"], "expected_preview_token"))
            return _receipt(value), 200
        if name == "status":
            _fields(payload, {"status", "actor", "reason"}, {"status", "actor", "reason"})
            status = TaskStatus(payload["status"]).value
            value = service.store.set_status(workspace, ids[0], status,
                actor=_text(payload["actor"], "actor"), reason=_text(payload["reason"], "reason"))
            return {"task": value.to_dict()}, 200
        raise AssertionError("Unregistered task route")