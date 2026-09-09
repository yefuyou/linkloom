"""Coordinator Agent for P4."""

from datetime import datetime, timezone
import uuid
from typing import Any, Callable, Dict, List

from linkloom.agents.base import AgentTask, AgentResult, ReviewDecision, HandoffRequest
from linkloom.agents.retrieval_agent import RetrievalAgent
from linkloom.agents.curator_agent import CuratorAgent
from linkloom.agents.reviewer_agent import ReviewerAgent
from linkloom.agents.registry import AgentRegistry
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer
from linkloom.tools.runtime import ToolRuntime, create_retrieval_tool_runtime


class Coordinator:
    def __init__(
        self,
        registry: AgentRegistry,
        retrieval_agent: RetrievalAgent,
        curator_agent: CuratorAgent,
        reviewer_agent: ReviewerAgent,
        tool_funcs: Dict[str, Callable],
        tool_runtime: ToolRuntime | None = None,
    ):
        self.registry = registry
        self.retrieval_agent = retrieval_agent
        self.curator_agent = curator_agent
        self.reviewer_agent = reviewer_agent
        self.tool_funcs = tool_funcs
        # Direct Coordinator callers remain compatible, while the adapter can
        # inject the runtime composed from its trusted VaultReader callbacks.
        self.tool_runtime = tool_runtime or create_retrieval_tool_runtime(
            tool_funcs["search_notes"],
            tool_funcs["read_verified_note"],
        )
        
        self.max_agent_tasks = 6
        self.max_total_steps = 12
        self.max_handoffs = 4
        self.max_tool_calls = 20
        
        self._seen_inputs = set()

    def run(
        self,
        run_id: str,
        workflow: str,
        query: str,
        source_context: Dict[str, Any],
        initial_refs: List[str],
        event_sink: Any = None,
        *,
        retrieval_task_id: str | None = None,
    ) -> Dict[str, Any]:
        
        status = "running"
        agent_tasks = []
        handoffs = []
        evidence = []
        errors = []
        fallback_used = False
        final_result = None
        review_decision = None
        
        total_steps = 0
        total_tool_calls = 0
        
        def emit(event_type: str, actor: str, status_str: str, **kwargs):
            if event_sink and hasattr(event_sink, "emit"):
                event_sink.emit(event_type=event_type, actor=actor, status=status_str, **kwargs)
        
        def create_task(agent_id: str, input_refs: List[str], wf: str, max_steps: int = 3, parent_task_id: str = None) -> AgentTask:
            input_sig = tuple(sorted(input_refs))
            if input_sig in self._seen_inputs:
                raise ValueError("Duplicate task/input refs detected.")
            self._seen_inputs.add(input_sig)
            
            if len(agent_tasks) >= self.max_agent_tasks:
                raise ValueError("Exceeded max_agent_tasks")
                
            identity = self.registry.get_agent(agent_id)
            task = AgentTask(
                task_id=(retrieval_task_id if agent_id == "retrieval_agent" and retrieval_task_id is not None
                         else f"task_{uuid.uuid4().hex[:8]}"),
                run_id=run_id,
                parent_task_id=parent_task_id,
                parent_agent_id="coordinator",
                agent_id=agent_id,
                workflow=wf,
                input_refs=input_refs,
                allowed_tool_ids=identity.capabilities,
                max_steps=max_steps,
                deadline_ms=5000,
                status="queued",
                attempt=0,
                created_at=datetime.now(timezone.utc).isoformat()
            )
            return task
            
        def enforce_limits():
            if total_steps >= self.max_total_steps:
                raise ValueError("Exceeded max_total_steps")
            if len(agent_tasks) >= self.max_agent_tasks:
                raise ValueError("Exceeded max_agent_tasks")
            if len(handoffs) > self.max_handoffs:
                raise ValueError("Exceeded max_handoffs")
            if total_tool_calls > self.max_tool_calls:
                raise ValueError("Exceeded max_tool_calls")
                
        def enforce_policy(agent_id: str, caps: List[str]) -> ToolPolicyEnforcer:
            policy = ToolCallPolicy(
                policy_version="p4-readonly-tools-v1",
                agent_id=agent_id,
                allowed_tool_ids=caps,
                denied_tool_ids=["write_file", "rename_file", "read_gold", "raw_filesystem"],
                max_calls=max(0, self.max_tool_calls - total_tool_calls),
                network="deny",
                vault_write="deny",
                gold_access="deny"
            )
            return ToolPolicyEnforcer(policy)
            
        emit("run.started", "coordinator", "started")
        
        try:
            enforce_limits()
            # 1. Retrieval
            retrieval_task = create_task("retrieval_agent", initial_refs, workflow)
            retrieval_task_dict = retrieval_task.to_dict()
            agent_tasks.append(retrieval_task_dict)
            emit("agent.task.created", "coordinator", "ok", attributes={"task_id": retrieval_task.task_id})
            emit("agent.task.started", "retrieval_agent", "started", attributes={"task_id": retrieval_task.task_id})
            
            ret_policy = enforce_policy("retrieval_agent", self.registry.get_agent("retrieval_agent").capabilities)
            ret_result = self.retrieval_agent.execute(
                task=retrieval_task,
                query=query,
                source_context=source_context,
                policy_enforcer=ret_policy,
                tool_runtime=self.tool_runtime,
                event_sink=event_sink,
            )
            
            total_steps += ret_result.usage["steps"]
            total_tool_calls += ret_result.usage["tool_calls"]
            enforce_limits()
            
            if ret_result.status == "failed":
                retrieval_task_dict["status"] = "failed"
                emit("agent.task.failed", "retrieval_agent", "failed", attributes={"task_id": retrieval_task.task_id}, error=ret_result.error)
                fallback_used = True
                evidence = []
                emit("agent.fallback.used", "coordinator", "ok", attributes={"task_id": retrieval_task.task_id})
                if "fallback" not in errors:
                    errors.append("fallback_used")
            else:
                retrieval_task_dict["status"] = "completed"
                emit("agent.task.completed", "retrieval_agent", "ok", attributes={"task_id": retrieval_task.task_id})
                evidence = ret_result.output_refs
            
            # 2. Connect specific: Curator
            candidate_refs = []
            curator_task = None
            if workflow == "connect":
                if len(handoffs) >= self.max_handoffs:
                    raise ValueError("Exceeded max_handoffs")
                    
                curator_task = create_task("curator_agent", initial_refs + evidence, workflow, parent_task_id=retrieval_task.task_id)
                curator_task_dict = curator_task.to_dict()
                agent_tasks.append(curator_task_dict)
                emit("agent.task.created", "coordinator", "ok", attributes={"task_id": curator_task.task_id})
                
                handoff = HandoffRequest(
                    handoff_id=f"handoff_{uuid.uuid4().hex[:8]}",
                    from_agent_id="coordinator",
                    to_agent_id="curator_agent",
                    reason_code="connect_candidate_generation",
                    task_id=curator_task.task_id,
                    input_refs=initial_refs + evidence,
                    requested_output_type="candidate_bundle",
                    allowed_tool_ids=self.registry.get_agent("curator_agent").capabilities,
                    max_steps=3,
                    status="requested",
                    created_at=datetime.now(timezone.utc).isoformat()
                )
                handoff_dict = handoff.to_dict()
                handoffs.append(handoff_dict)
                emit("handoff.requested", "coordinator", "ok", attributes={"handoff_id": handoff.handoff_id})
                
                handoff_dict["status"] = "accepted"
                emit("handoff.accepted", "curator_agent", "ok", attributes={"handoff_id": handoff.handoff_id})
                emit("agent.task.started", "curator_agent", "started", attributes={"task_id": curator_task.task_id})
                
                cur_policy = enforce_policy("curator_agent", self.registry.get_agent("curator_agent").capabilities)
                cur_result = self.curator_agent.execute(
                    task=curator_task,
                    query=query,
                    policy_enforcer=cur_policy,
                    reader_func=self.tool_funcs["read_verified_note"],
                    curator_func=self.tool_funcs["build_pair_signals"]
                )
                total_steps += cur_result.usage["steps"]
                total_tool_calls += cur_result.usage["tool_calls"]
                
                if cur_result.status == "failed":
                    curator_task_dict["status"] = "failed"
                    handoff_dict["status"] = "rejected"
                    emit("agent.task.failed", "curator_agent", "failed", attributes={"task_id": curator_task.task_id}, error=cur_result.error)
                    fallback_used = True
                    candidate_refs = []
                    review_incomplete = True
                    emit("agent.fallback.used", "coordinator", "ok", attributes={"task_id": curator_task.task_id})
                    if "fallback" not in errors:
                        errors.append("fallback_used")
                else:
                    curator_task_dict["status"] = "completed"
                    handoff_dict["status"] = "completed"
                    emit("agent.task.completed", "curator_agent", "ok", attributes={"task_id": curator_task.task_id})
                    candidate_refs = cur_result.output_refs
                    review_incomplete = False
            else:
                candidate_refs = evidence
                review_incomplete = False
                
            # 3. Reviewer
            parent_id = curator_task.task_id if workflow == "connect" else retrieval_task.task_id
            reviewer_task = create_task("reviewer_agent", candidate_refs + evidence, workflow, parent_task_id=parent_id)
            reviewer_task_dict = reviewer_task.to_dict()
            agent_tasks.append(reviewer_task_dict)
            emit("agent.task.created", "coordinator", "ok", attributes={"task_id": reviewer_task.task_id})
            emit("agent.task.started", "reviewer_agent", "started", attributes={"task_id": reviewer_task.task_id})
            
            rev_policy = enforce_policy("reviewer_agent", self.registry.get_agent("reviewer_agent").capabilities)
            review_dec = self.reviewer_agent.execute(
                review_id=f"review_{uuid.uuid4().hex[:8]}",
                subject_refs=candidate_refs,
                subject_payloads=[{"ref": ref} for ref in candidate_refs],
                evidence_refs=evidence,
                policy_enforcer=rev_policy,
                validate_ev_func=self.tool_funcs["validate_evidence"],
                validate_sch_func=self.tool_funcs["validate_schema"]
            )
            reviewer_task_dict["status"] = "completed"
            review_decision = review_dec.to_dict()
            total_tool_calls += max(0, rev_policy.call_count)
            total_steps += 1
            emit("agent.task.completed", "reviewer_agent", "ok", attributes={"task_id": reviewer_task.task_id})
            enforce_limits()
            
            if fallback_used:
                if review_decision["decision"] not in ("evidence_insufficient", "schema_invalid"):
                    review_decision["decision"] = "needs_human"
                    review_decision["reason"] = "review_incomplete due to fallback"
            
            status = "completed"
            final_result = {
                "evidence_refs": evidence,
                "candidate_refs": candidate_refs,
                "review_decision": review_decision["decision"]
            }
            emit("run.completed", "coordinator", "ok")
            
        except Exception as e:
            status = "failed"
            errors.append(str(e))
            emit("run.failed", "coordinator", "failed", error={"message": str(e), "code": "COORDINATOR_ERROR"})
        finally:
            self._seen_inputs.clear()
            
        return {
            "run_id": run_id,
            "workflow": workflow,
            "status": status,
            "result": final_result,
            "agent_tasks": agent_tasks,
            "handoffs": handoffs,
            "review": review_decision,
            "evidence": evidence,
            "policy": "p4-readonly-tools-v1",
            "usage": {
                "steps": total_steps,
                "tool_calls": total_tool_calls
            },
            # The runtime-managed retrieval ledger is an audit-safe projection
            # of the ToolRuntime lifecycle.  Curator/reviewer legacy direct
            # callbacks are intentionally not represented here yet.
            "tool_ledger": [
                record.to_dict() for record in self.tool_runtime.ledger.to_list()
            ],
            "fallback_used": fallback_used,
            "errors": errors
        }
