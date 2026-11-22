"""Durable run directory of one product orchestration run, and the object that drives it.

Layout under work/investigations/<id>/: orchestration.json (what the Host authorized and the sources it
copied), langgraph.sqlite (the LangGraph checkpoint: the only orchestration state), vault/ and scan/ (read-only
source copies), supervisor/ and tasks/ (receipts of every paid call; see graph.py and worker.py).
"""
from __future__ import annotations

from pathlib import Path
from threading import Event

from linkloom.orchestration.files import read_json, write_json
from linkloom.orchestration.graph import Orchestrator, open_checkpointer
from linkloom.orchestration.limits import OrchestrationLimits
from linkloom.orchestration.worker import WorkerDeps
from linkloom.ui.projection import WorkspacePresentationContext

SCHEMA = 'orchestration-run/v1'
RECORD, CHECKPOINT, CANCEL_MARKER = 'orchestration.json', 'langgraph.sqlite', 'cancelled.json'


def write_record(root, *, query, workspace_id, identity, workspace, source_names, source_versions, index,
                 confirmed_scope, entity_leads, limits):
    """Written once, before any paid call, so a restarted server can read and resume this run."""
    root = Path(root)
    write_json(root / RECORD, dict(
        schema=SCHEMA, query=query, workspace_id=workspace_id, identity=identity,
        workspace=dict(display_name=workspace.display_name, document_count=workspace.document_count),
        source_names=source_names, source_versions=source_versions,
        index=Path(index).resolve().relative_to(root.resolve()).as_posix(),
        confirmed_scope=confirmed_scope, entity_leads=list(entity_leads), limits=limits.to_dict()))


def load_record(root):
    record = read_json(Path(root) / RECORD)
    if not isinstance(record, dict) or record.get('schema') != SCHEMA:
        raise ValueError('ORCHESTRATION_RECORD_UNREADABLE')
    return record


def is_cancelled(root):
    return (Path(root) / CANCEL_MARKER).is_file()


class OrchestrationRun:
    """An Orchestrator bound to one run directory. Owns the SQLite connection of the LangGraph checkpoint."""

    def __init__(self, root, run_id, record, *, model_factory, validate):
        self.root, self.run_id, self.record = Path(root), run_id, record
        self.cancelled = Event()
        self.workspace = WorkspacePresentationContext(display_name=str(record['workspace']['display_name']),
                                                      document_count=record['workspace']['document_count'])
        self.saver = open_checkpointer(self.root / CHECKPOINT)

        def guarded():
            if self.cancelled.is_set() or is_cancelled(self.root):
                raise ValueError('PARALLEL_CANCELLED')
            validate()

        self.guard = guarded
        deps = WorkerDeps(root=self.root, run_id=run_id, vault=self.root / 'vault', index=self.root / record['index'],
            workspace=self.workspace, workspace_id=record['workspace_id'], source_names=record['source_names'],
            source_versions=record['source_versions'], model_factory=model_factory, validate=guarded,
            limits=OrchestrationLimits.from_dict(record['limits']))
        self.orchestrator = Orchestrator(deps, self.saver)

    def start(self):
        return self.orchestrator.start(self.record['query'], confirmed_scope=self.record['confirmed_scope'],
                                       entity_leads=self.record['entity_leads'])

    def resume(self, approval=None):
        return self.orchestrator.resume(approval)

    def state(self):
        return self.orchestrator.state()

    def cancel(self):
        write_json(self.root / CANCEL_MARKER, dict(reason='PARALLEL_CANCELLED'))
        self.cancelled.set()

    def close(self):
        try:
            self.saver.conn.close()
        except Exception:
            pass
