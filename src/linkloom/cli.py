"""Command-line entry point for linkloom's read-only tools."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from linkloom.loader import LoaderError
from linkloom.scanner import ScannerError, scan_vault
from linkloom.services.ask import AskService
from linkloom.services.connect import ConnectService
import uuid
from linkloom.runtime import (
    RuntimeEngine,
    RunRequest,
    SQLiteCheckpointer,
    RuntimeModelError,
    RunStatus,
)
from linkloom.observability.reader import TraceReader, TraceReadError
from linkloom.observability.redaction import safe_ref
from linkloom.observability.summary import summarize_trace, write_summary
from linkloom.runtime.graph import DEFAULT_TRACE_DIR
from linkloom.memory import MemoryStore, MemoryLifecycle, MemoryScope, MemoryPolicyViolation


_SAFE_TRACE_LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


def _safe_trace_label(value: str | None) -> str:
    if not value or not _SAFE_TRACE_LABEL.fullmatch(value):
        return f"sha256:{safe_ref(value or '').get('sha256')}"
    return value


def print_run_status(
    status: RunStatus,
    *,
    checkpointer: SQLiteCheckpointer | None = None,
    checkpoint_dir: Path | None = None,
    trace_dir: Path | None = None,
) -> None:
    print(f"Run ID: {status.run_id}")
    print(f"Thread ID: {status.thread_id}")
    print(f"Status: {status.status}")
    print(f"Current Step: {status.current_step}")
    print(f"Checkpoint ID: {status.checkpoint_id}")
    interrupt_id = status.interrupt.get("interrupt_id") if status.interrupt else None
    print(f"Interrupt ID: {interrupt_id}")
    print(f"Result Ref: {status.result_ref}")
    if checkpointer is not None:
        state = checkpointer.get_latest(status.thread_id)
        print(f"Source SHA-256: {state.source.index_sha256 if state else None}")
    else:
        print("Source SHA-256: None")
    print("Source Write: 0")
    if checkpoint_dir is not None:
        print(f"Checkpoint Dir: {checkpoint_dir}")
    if trace_dir is not None:
        print(f"Trace Dir: {trace_dir}")
    if status.error:
        print(f"Error Code: {status.error.get('code')}")
        print(f"Error Message: {status.error.get('message')}")
    else:
        print("Error: None")


def print_agent_status(
    status: RunStatus,
    *,
    checkpointer: SQLiteCheckpointer,
    checkpoint_dir: Path,
    trace_dir: Path,
) -> None:
    """Print a safe, role-oriented P4 result without raw prompt/content."""
    print_run_status(
        status,
        checkpointer=checkpointer,
        checkpoint_dir=checkpoint_dir,
        trace_dir=trace_dir,
    )
    print("Coordination Pattern: manager-as-tools")
    print("Writer Capability: DENY")
    print("Gold Access: DENY")
    print("Network: DENY")
    if not status.result_ref:
        return

    result_path = checkpoint_dir / status.result_ref
    print(f"Result: {result_path}")
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("Agent Result: unavailable")
        return

    agent_tasks = result.get("agent_tasks", [])
    agent_ids = [
        task.get("agent_id")
        for task in agent_tasks
        if isinstance(task, dict) and isinstance(task.get("agent_id"), str)
    ]
    labels = {
        "coordinator": "Coordinator",
        "retrieval_agent": "Retrieval Agent",
        "curator_agent": "Curator Agent",
        "reviewer_agent": "Reviewer Agent",
    }
    print("Agents: " + " -> ".join(labels.get(agent_id, agent_id) for agent_id in agent_ids))
    print("Evidence Refs: " + ", ".join(str(ref) for ref in result.get("evidence", [])))
    review = result.get("review") or {}
    print(f"Reviewer Decision: {review.get('decision', 'None')}")
    usage = result.get("usage") or {}
    print(f"Steps: {usage.get('steps', 0)}")
    print(f"Tool Calls: {usage.get('tool_calls', 0)}")
    print(f"Handoffs: {len(result.get('handoffs', []))}")
    print(f"Fallback Used: {bool(result.get('fallback_used', False))}")
    memory_refs = result.get("memory_refs", [])
    if memory_refs:
        memory_ids = [
            ref.get("memory_id")
            for ref in memory_refs
            if isinstance(ref, dict) and isinstance(ref.get("memory_id"), str)
        ]
        print(f"Memory Refs: {len(memory_refs)} ({', '.join(memory_ids)})")


def print_agent_trace(root: Path, run_id: str) -> None:
    """Show safe P4 event metadata; quotes and prompts never enter this view."""
    reader = TraceReader(root)
    events = reader.read_events(run_id)
    for event in events:
        node = _safe_trace_label(event.node_name or "system")
        task_id = event.attributes.get("task_id") if isinstance(event.attributes, dict) else None
        handoff_id = event.attributes.get("handoff_id") if isinstance(event.attributes, dict) else None
        ref = task_id or handoff_id or ""
        suffix = f" ref={_safe_trace_label(ref)}" if ref else ""
        print(f"{event.seq:02d} {event.event_type:24} {event.status:8} {event.actor:7} {node}{suffix}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="linkloom")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser(
        "scan", help="Create a read-only index for a Markdown vault."
    )
    scan_parser.add_argument("input_root", type=Path, help="Markdown vault root.")
    scan_parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Directory for generated artifacts. It must be outside the input root.",
    )

    ask_parser = subparsers.add_parser(
        "ask", help="Query notes and return cited, grounded evidence."
    )
    ask_parser.add_argument("input_root", type=Path, help="Markdown vault root.")
    ask_parser.add_argument("--query", type=str, required=True, help="Question to search for evidence.")
    ask_parser.add_argument("--index", type=Path, required=True, help="Path to vault_index.json.")
    ask_parser.add_argument("--output", type=Path, required=True, help="Directory for generated artifacts.")

    connect_parser = subparsers.add_parser(
        "connect", help="Discover explainable relation candidates between notes."
    )
    connect_parser.add_argument("input_root", type=Path, help="Markdown vault root.")
    connect_parser.add_argument("--query", type=str, required=True, help="Query to guide relation discovery.")
    connect_parser.add_argument("--index", type=Path, required=True, help="Path to vault_index.json.")
    connect_parser.add_argument("--output", type=Path, required=True, help="Directory for generated artifacts.")

    run_parser = subparsers.add_parser(
        "run", help="Runtime command group."
    )
    run_subparsers = run_parser.add_subparsers(dest="run_command", required=True)

    run_ask_parser = run_subparsers.add_parser("ask", help="Run ask workflow with runtime engine.")
    run_ask_parser.add_argument("vault", type=Path, help="Markdown vault root.")
    run_ask_parser.add_argument("--index", type=Path, required=True, help="Path to index file.")
    run_ask_parser.add_argument("--query", type=str, required=True, help="Query.")
    run_ask_parser.add_argument("--checkpoint", type=Path, required=True, help="Path to checkpoint directory.")
    run_ask_parser.add_argument("--pause-after", type=str, choices=["retrieve_context"], default=None, help="Pause after a step.")
    run_ask_parser.add_argument("--max-steps", type=int, default=12, help="Max steps limit.")
    run_ask_parser.add_argument("--max-provider-requests", type=int, default=0, help="Max provider requests limit.")

    run_ask_parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE_DIR, help="Path to trace directory.")
    run_ask_parser.add_argument("--memory-root", type=Path, default=None, help="Path to memory storage.")

    run_connect_parser = run_subparsers.add_parser("connect", help="Run connect workflow with runtime engine.")
    run_connect_parser.add_argument("vault", type=Path, help="Markdown vault root.")
    run_connect_parser.add_argument("--index", type=Path, required=True, help="Path to index file.")
    run_connect_parser.add_argument("--query", type=str, required=True, help="Query.")
    run_connect_parser.add_argument("--checkpoint", type=Path, required=True, help="Path to checkpoint directory.")
    run_connect_parser.add_argument("--pause-after", type=str, choices=["retrieve_context"], default=None, help="Pause after a step.")
    run_connect_parser.add_argument("--max-steps", type=int, default=12, help="Max steps limit.")
    run_connect_parser.add_argument("--max-provider-requests", type=int, default=0, help="Max provider requests limit.")
    run_connect_parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE_DIR, help="Path to trace directory.")
    run_connect_parser.add_argument("--memory-root", type=Path, default=None, help="Path to memory storage.")

    run_inspect_parser = run_subparsers.add_parser("inspect", help="Inspect thread status.")
    run_inspect_parser.add_argument("--thread-id", type=str, required=True, help="Thread ID.")
    run_inspect_parser.add_argument("--checkpoint", type=Path, required=True, help="Path to checkpoint directory.")

    run_resume_parser = run_subparsers.add_parser("resume", help="Resume thread execution.")
    run_resume_parser.add_argument("--thread-id", type=str, required=True, help="Thread ID.")
    run_resume_parser.add_argument("--interrupt-id", type=str, required=True, help="Interrupt ID.")
    run_resume_parser.add_argument(
        "--input",
        type=str,
        choices=["resume", "reject", "继续", "拒绝"],
        required=True,
        help="Response: resume/reject or 继续/拒绝.",
    )
    run_resume_parser.add_argument("--vault", type=Path, required=True, help="Markdown vault root.")
    run_resume_parser.add_argument("--index", type=Path, required=True, help="Path to index file.")
    run_resume_parser.add_argument("--checkpoint", type=Path, required=True, help="Path to checkpoint directory.")
    run_resume_parser.add_argument("--trace", type=Path, default=DEFAULT_TRACE_DIR, help="Path to trace directory.")
    run_resume_parser.add_argument("--memory-root", type=Path, default=None, help="Path to memory storage.")

    trace_parser = subparsers.add_parser("trace", help="Trace read-only command group.")
    trace_subparsers = trace_parser.add_subparsers(dest="trace_command", required=True)

    trace_show_parser = trace_subparsers.add_parser("show", help="Show trace events.")
    trace_show_parser.add_argument("--run-id", type=str, required=True, help="Run ID to show.")
    trace_show_parser.add_argument("--root", type=Path, required=True, help="Root trace directory.")

    trace_summary_parser = trace_subparsers.add_parser("summary", help="Generate trace summary.")
    trace_summary_parser.add_argument("--run-id", type=str, required=True, help="Run ID to summarize.")
    trace_summary_parser.add_argument("--root", type=Path, required=True, help="Root trace directory.")

    agent_parser = subparsers.add_parser("agent", help="Run the read-only P4 multi-agent workflow.")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    for workflow in ("ask", "connect"):
        agent_run_parser = agent_subparsers.add_parser(workflow, help=f"Run the P4 {workflow} workflow.")
        agent_run_parser.add_argument("vault", type=Path, help="Markdown vault root.")
        agent_run_parser.add_argument("--index", type=Path, required=True, help="Path to index file.")
        agent_run_parser.add_argument("--query", type=str, required=True, help="Query.")
        agent_run_parser.add_argument(
            "--checkpoint", type=Path,
            default=Path(".artifacts") / "p4" / "runtime",
            help="Path to P4 checkpoint directory.",
        )
        agent_run_parser.add_argument(
            "--trace", type=Path,
            default=Path(".artifacts") / "p4" / "traces",
            help="Path to P4 trace directory.",
        )
        agent_run_parser.add_argument("--max-steps", type=int, default=12, help="Maximum total agent steps.")
        agent_run_parser.add_argument("--max-provider-requests", type=int, default=0)
        agent_run_parser.add_argument("--memory-root", type=Path, default=None, help="Path to memory storage.")

    agent_trace_parser = agent_subparsers.add_parser("trace", help="Show P4 agent trace metadata.")
    agent_trace_parser.add_argument("--run-id", type=str, required=True, help="Run ID to show.")
    agent_trace_parser.add_argument("--root", type=Path, required=True, help="Root trace directory.")

    memory_parser = subparsers.add_parser("memory", help="Manage memory storage.")
    memory_subparsers = memory_parser.add_subparsers(dest="memory_command", required=True)

    mem_propose_parser = memory_subparsers.add_parser("propose", help="Propose a memory candidate.")
    mem_propose_parser.add_argument("--store", type=Path, required=True, help="Path to memory JSONL store.")
    mem_propose_parser.add_argument("--scope", type=str, choices=["global", "project", "workflow", "thread"], required=True, help="Scope of the memory.")
    mem_propose_parser.add_argument("--key", type=str, required=True, help="Memory key.")
    mem_propose_parser.add_argument("--value-json", type=str, required=True, help="JSON string for the memory value.")
    mem_propose_parser.add_argument("--source-ref", type=str, action="append", default=[], help="Source references.")

    mem_candidates_parser = memory_subparsers.add_parser("candidates", help="List memory candidates.")
    mem_candidates_parser.add_argument("--store", type=Path, required=True, help="Path to memory JSONL store.")

    mem_confirm_parser = memory_subparsers.add_parser("confirm", help="Confirm a memory candidate.")
    mem_confirm_parser.add_argument("--store", type=Path, required=True, help="Path to memory JSONL store.")
    mem_confirm_parser.add_argument("--candidate-id", type=str, required=True, help="Candidate ID to confirm.")
    mem_confirm_parser.add_argument("--actor", type=str, required=True, help="Actor confirming the memory.")
    mem_confirm_parser.add_argument("--edited-json", type=str, default=None, help="Edited JSON string for the memory value.")

    mem_list_parser = memory_subparsers.add_parser("list", help="List active memory items.")
    mem_list_parser.add_argument("--store", type=Path, required=True, help="Path to memory JSONL store.")

    mem_revoke_parser = memory_subparsers.add_parser("revoke", help="Revoke an active memory item.")
    mem_revoke_parser.add_argument("--store", type=Path, required=True, help="Path to memory JSONL store.")
    mem_revoke_parser.add_argument("--item-id", type=str, required=True, help="Item ID to revoke.")
    mem_revoke_parser.add_argument("--actor", type=str, required=True, help="Actor revoking the memory.")

    eval_parser = subparsers.add_parser("eval", help="Run formal evaluation.")
    eval_parser.add_argument("--dataset", type=str, required=True, help="Dataset name.")
    eval_parser.add_argument("--provider", type=str, required=True, help="Provider name.")
    eval_parser.add_argument("--scenario", type=str, required=True, choices=["perfect", "noisy", "malformed", "real"], help="Scenario.")
    eval_parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    eval_parser.add_argument("--repo-root", type=Path, default=Path.cwd(), help="Repository root.")

    writeback_parser = subparsers.add_parser("writeback", help="Writeback operations.")
    writeback_subparsers = writeback_parser.add_subparsers(dest="writeback_command", required=True)
    
    wb_demo_parser = writeback_subparsers.add_parser("demo", help="Run writeback e2e demo.")
    wb_demo_parser.add_argument("--output", type=Path, required=True, help="Output directory.")
    wb_demo_parser.add_argument("--scenario", type=str, default="success", help="Scenario to run.")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        if args.command == "scan":
            result = scan_vault(args.input_root, args.output)
            print(f"Indexed notes: {result.note_count}")
            print(f"Warnings: {result.warning_count}")
            print(f"Index: {result.index_path}")
            print(f"Summary: {result.summary_path}")
            return 0

        if args.command == "ask":
            result = AskService.run(
                vault_root=args.input_root,
                index_path=args.index,
                query=args.query,
                output_dir=args.output,
            )
            print(f"Status: {result.status}")
            print(f"Query: {result.query}")
            print(f"Answer: {result.answer['text']}")
            print(f"Evidence count: {len(result.evidence)}")
            print(f"Index SHA-256: {result.source.get('index_sha256', '')}")
            print(f"Result: {args.output / 'result.json'}")
            print(f"Report: {args.output / 'report.md'}")
            return 0

        if args.command == "connect":
            result = ConnectService.run(
                vault_root=args.input_root,
                index_path=args.index,
                query=args.query,
                output_dir=args.output,
            )
            print(f"Status: {result.status}")
            print(f"Query: {result.query}")
            print(f"Candidate pairs: {len(result.candidates)}")
            print(f"Evidence count: {len(result.evidence)}")
            print(f"Index SHA-256: {result.source.get('index_sha256', '')}")
            print(f"Result: {args.output / 'result.json'}")
            print(f"Report: {args.output / 'report.md'}")
            return 0

        if args.command == "run":
            if args.run_command in ("ask", "connect"):
                request = RunRequest(
                    request_id=f"req_cli_{uuid.uuid4().hex[:12]}",
                    workflow=args.run_command,
                    query=args.query,
                    vault_root=str(args.vault.resolve()),
                    index_path=None,
                    dry_run=True,
                    max_steps=args.max_steps,
                    max_provider_requests=args.max_provider_requests,
                )
                engine = RuntimeEngine(
                    vault_root=args.vault,
                    index_path=args.index,
                    checkpoint_dir=args.checkpoint,
                    pause_after=args.pause_after,
                    trace_dir=args.trace,
                    memory_root=args.memory_root,
                )
                status = engine.start(request)
                print_run_status(
                    status,
                    checkpointer=engine.checkpointer,
                    checkpoint_dir=args.checkpoint,
                    trace_dir=args.trace,
                )
                return 0 if status.status in {"paused", "completed", "rejected"} else 1

            elif args.run_command == "inspect":
                checkpointer = SQLiteCheckpointer(args.checkpoint)
                latest_state = checkpointer.get_latest(args.thread_id)
                if latest_state is None:
                    from linkloom.runtime.errors import ThreadNotFoundError
                    raise ThreadNotFoundError(f"Thread '{args.thread_id}' not found.")
                history = checkpointer.list_checkpoints(args.thread_id)
                checkpoint_id = history[-1]["checkpoint_id"] if history else None
                print(f"Run ID: {latest_state.run_id}")
                print(f"Thread ID: {latest_state.thread_id}")
                print(f"Status: {latest_state.status}")
                print(f"Current Step: {latest_state.current_step}")
                print(f"Checkpoint ID: {checkpoint_id}")
                interrupt_id = latest_state.pending_interrupt.interrupt_id if latest_state.pending_interrupt else None
                print(f"Interrupt ID: {interrupt_id}")
                print(f"Result Ref: {latest_state.result_ref}")
                print(f"Source SHA-256: {latest_state.source.index_sha256}")
                print("Source Write: 0")
                print(f"Checkpoint Dir: {args.checkpoint}")
                if latest_state.error:
                    print(f"Error Code: {latest_state.error.code}")
                    print(f"Error Message: {latest_state.error.message}")
                else:
                    print("Error: None")
                return 0

            elif args.run_command == "resume":
                engine = RuntimeEngine(
                    vault_root=args.vault,
                    index_path=args.index,
                    checkpoint_dir=args.checkpoint,
                    trace_dir=args.trace,
                    memory_root=args.memory_root,
                )
                response = {"继续": "resume", "拒绝": "reject"}.get(args.input, args.input)
                status = engine.resume(
                    thread_id=args.thread_id,
                    interrupt_id=args.interrupt_id,
                    response=response,
                )
                print_run_status(
                    status,
                    checkpointer=engine.checkpointer,
                    checkpoint_dir=args.checkpoint,
                    trace_dir=args.trace,
                )
                return 0 if status.status in {"completed", "rejected"} else 1

        if args.command == "agent":
            if args.agent_command in ("ask", "connect"):
                request = RunRequest(
                    request_id=f"req_cli_p4_{uuid.uuid4().hex[:12]}",
                    workflow=args.agent_command,
                    query=args.query,
                    vault_root=str(args.vault.resolve()),
                    index_path=None,
                    dry_run=True,
                    max_steps=args.max_steps,
                    max_provider_requests=args.max_provider_requests,
                )
                engine = RuntimeEngine(
                    vault_root=args.vault,
                    index_path=args.index,
                    checkpoint_dir=args.checkpoint,
                    trace_dir=args.trace,
                    memory_root=args.memory_root,
                )
                status = engine.start_multi_agent(request)
                print_agent_status(
                    status,
                    checkpointer=engine.checkpointer,
                    checkpoint_dir=args.checkpoint,
                    trace_dir=args.trace,
                )
                return 0 if status.status == "completed" else 1

            if args.agent_command == "trace":
                print_agent_trace(args.root, args.run_id)
                return 0

        if args.command == "trace":
            reader = TraceReader(args.root)
            if args.trace_command == "show":
                events = reader.read_events(args.run_id)
                for ev in events:
                    node = _safe_trace_label(ev.node_name or "system")
                    print(f"{ev.seq:02d} {ev.event_id} {ev.event_type:20} {ev.status:8} {node}")
                return 0

            elif args.trace_command == "summary":
                manifest = reader.read_manifest(args.run_id)
                events = reader.read_events(args.run_id)
                summary_text = summarize_trace(events, manifest)
                print(summary_text)
                out_path = args.root / args.run_id / "trace_summary.md"
                write_summary(out_path, events, manifest)
                return 0

        if args.command == "memory":
            try:
                store = MemoryStore(args.store)
                lifecycle = MemoryLifecycle(store)
                if args.memory_command == "propose":
                    value = json.loads(args.value_json)
                    scope = MemoryScope(args.scope)
                    candidate = lifecycle.propose_candidate(
                        scope=scope,
                        key=args.key,
                        value=value,
                        source_refs=args.source_ref,
                    )
                    print(f"Candidate ID: {candidate.id}")
                    print(f"Scope: {candidate.scope.value}")
                    print(f"Key: {candidate.key}")
                    print(f"Status: {candidate.status.value}")
                    print(f"Sources: {len(candidate.source_refs)}")
                    return 0
                elif args.memory_command == "candidates":
                    candidates = lifecycle.list_candidates()
                    for cand in candidates:
                        print(f"Candidate ID: {cand.id} | Key: {cand.key} | Status: {cand.status.value}")
                    return 0
                elif args.memory_command == "confirm":
                    edited_value = json.loads(args.edited_json) if args.edited_json else None
                    item = lifecycle.confirm_candidate(
                        candidate_id=args.candidate_id,
                        actor=args.actor,
                        edited_value=edited_value,
                    )
                    print(f"Item ID: {item.id}")
                    print(f"Candidate ID: {item.candidate_id}")
                    print(f"Scope: {item.scope.value}")
                    print(f"Key: {item.key}")
                    print(f"Status: {item.status.value}")
                    return 0
                elif args.memory_command == "list":
                    items = lifecycle.list_active()
                    for item in items:
                        print(f"Item ID: {item.id} | Key: {item.key} | Status: {item.status.value}")
                    return 0
                elif args.memory_command == "revoke":
                    item = lifecycle.revoke_item(
                        item_id=args.item_id,
                        actor=args.actor,
                    )
                    print(f"Item ID: {item.id} | Status: {item.status.value}")
                    return 0
            except MemoryPolicyViolation as error:
                print(f"Memory Policy Error: {error}", file=sys.stderr)
                return 1
            except json.JSONDecodeError:
                print("JSON Error: Malformed JSON input.", file=sys.stderr)
                return 1

        if args.command == "eval":
            if args.dataset != "relation_vault_v1":
                print("Error: unsupported dataset")
                return 1
            if args.provider not in {"mock", "gemini_cli"}:
                print("Error: unsupported provider")
                return 1

            try:
                from linkloom.evaluation.runner import FormalEvaluationRunner
                runner = FormalEvaluationRunner(repo_root=args.repo_root, output_root=args.output)
                result = runner.run(provider=args.provider, scenario=args.scenario)
                manifest = result["manifest"]
                metrics = result["metrics"]
                safety = result["safety"]
                run_dir = Path(result["run_dir"])
                artifact_count = len([path for path in run_dir.iterdir() if path.is_file()]) if run_dir.exists() else 0
                print(f"Run ID: {manifest.get('run_id')}")
                print(f"Status: {manifest.get('status')}")
                print(f"Output Directory: {result['run_dir']}")
                print(f"Artifact Count: {artifact_count}")
                print(f"Safety Status: {'PASSED' if safety.get('passed') else 'FAILED'}")
                print("Selected metric summaries:")
                print(f"Topic F1: {metrics.get('topic', {}).get('f1', 0.0):.3f}")
                print(f"Relation Link F1: {metrics.get('relation', {}).get('link_f1', 0.0):.3f}")
                print(f"Baseline Eligible: {manifest.get('baseline_eligible', False)}")
                if manifest.get("status") in ("invalid", "failed") or not safety.get("passed"):
                    return 1
                return 0
            except Exception as error:
                print(f"Error: Evaluation failed safely: {error.__class__.__name__}", file=sys.stderr)
                return 1

        if args.command == "writeback":
            if args.writeback_command == "demo":
                from linkloom.mutations.e2e import E2EWritebackRunner
                from linkloom.evaluation.writeback_eval import run_evaluation
                if args.scenario == "all":
                    metrics = run_evaluation(args.output)
                    print(json.dumps(metrics, indent=2))
                    print("Writeback Metrics: writeback_metrics.json")
                    print("Failure Matrix: failure_matrix.json")
                    return 0 if metrics["passed"] else 1
                else:
                    runner = E2EWritebackRunner(args.output)
                    import tempfile
                    with tempfile.TemporaryDirectory(prefix="linkloom_demo_") as temp_dir_str:
                        temp_dir = Path(temp_dir_str)
                        target_root = temp_dir / "target"
                        backup_root = temp_dir / "backup"
                        manifest = runner.run_e2e(
                            target_root, backup_root, "APPROVE_DEFAULT", scenario=args.scenario
                        )
                        
                        artifact_dir = args.output / ".artifacts" / "p7"
                        artifact_dir.mkdir(parents=True, exist_ok=True)
                    
                    manifest_path = artifact_dir / "run_manifest.json"
                    manifest["cli_artifact_ref"] = manifest_path.relative_to(args.output).as_posix()
                    manifest_path.write_text(json.dumps(manifest, indent=2))
                    
                    if manifest.get("status") == "applied":
                        print("Status: completed")
                    else:
                        print(f"Status: failed ({manifest.get('error_code')})")
                        
                    print(f"Writeback Run ID: {manifest.get('writeback_run_id', 'N/A')}")
                    print(f"Trace Artifact: {manifest.get('trace_artifact_ref', 'N/A')}")
                    print(f"Eval Artifact: {manifest.get('eval_artifact_ref', 'N/A')}")
                    print(f"Manifest Path: {manifest_path}")
                    
                    passed_checks = False
                    if args.scenario == "success":
                        sc = manifest.get("scenario_checks", {})
                        passed_checks = manifest.get("status") == "applied" and sc.get("apply_calls") == 1 and sc.get("writer_calls") == 1 and sc.get("before_sha256") != sc.get("after_sha256")
                        
                    return 0 if (manifest.get("status") == "applied" and passed_checks) else 1

        return 2
    except (ScannerError, LoaderError) as error:
        print(f"Error: {getattr(error, 'code', 'SOURCE_ERROR')}: {error}", file=sys.stderr)
        return getattr(error, "exit_code", 2)
    except RuntimeModelError as error:
        print(f"Error: {error.code}: {error}", file=sys.stderr)
        return 1
    except (TraceReadError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Error: could not write artifacts: {error}", file=sys.stderr)
        return 1
    return 0

