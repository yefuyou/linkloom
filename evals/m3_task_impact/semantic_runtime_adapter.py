"""Offline Dev execution contract, not a production semantic quality score.

Reads only PublicTools sources. No Gold, scorer, holdout, API or live Provider.
M1 extraction and typed question discovery are not implemented by this adapter;
their absence is exposed as BLOCKED rather than filled with reference answers.
"""
from datetime import UTC, datetime
import json

from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.runtime.graph import RuntimeEngine
from linkloom.runtime.models import RunRequest
from linkloom.scanner import scan_vault
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import build_task_observation
from linkloom.semantic_ingestion.task_relation_models import ObservationRef, canonical_hash
from linkloom.tools.contracts import ToolCall
from linkloom.tools.semantic_analysis import SemanticAnalysis, SemanticCallPolicy
from linkloom.tools.semantic_context import SemanticSourceSnapshot
from linkloom.tools.task_relations import TaskContextSnapshot, TaskRelationTools


class OfflineSemanticRuntimeAdapter:
    def __init__(self, root):
        self.root = root
        self.last = None

    def __call__(self, request, tools):
        root = self.root
        root.mkdir(parents=True, exist_ok=False)
        catalog = tools.list_sources()
        registry = SourceReferenceRegistry()
        artifacts, observations, handles = [], [], []
        for source in catalog:
            original = tools.read_source(source["doc_id"])
            artifact = RawArtifact(request["workspace_id"], original["artifact_id"], "timestamped_text",
                original["content"], ingestion_time=datetime(2026, 10, 18, tzinfo=UTC))
            if artifact.artifact_version_id != source["artifact_version_id"]:
                raise ValueError("public source version changed")
            artifacts.append(artifact)
            handles.append((artifact.artifact_version_id, source["doc_id"]))
            for segment in parse_source_text(artifact).segments:
                span = segment.evidence_span
                registry.register_evidence(request["workspace_id"], span.evidence_ref, content_hash=span.content_hash,
                    source_version=span.artifact_version_id if source["current"] else "historical-unavailable")
                observations.append(build_task_observation(None, segment, artifact))
        snapshot = SemanticSourceSnapshot(request["workspace_id"], request["question"], tuple(artifacts),
            tuple(observations), tuple(("observation_" + str(i), ObservationRef.from_observation(o)) for i, o in enumerate(observations)),
            (), tuple(handles), registry, frozenset(a.artifact_version_id for a in artifacts),
            frozenset(a.artifact_version_id for a in artifacts),
            frozenset(canonical_hash(ObservationRef.from_observation(o).to_dict()) for o in observations), True, False)
        semantic = SemanticAnalysis("dev-public-investigation", request["workspace_id"], lambda: snapshot, None,
            model_id="offline-not-configured", policy=SemanticCallPolicy("FAKE"))
        relation_tools = TaskRelationTools(request["workspace_id"], lambda: TaskContextSnapshot(
            request["workspace_id"], tuple(observations), tuple(artifacts), registry), semantic_analysis=semantic)
        selected = {}
        def action(tool, args):
            return ModelAction.tool(ToolCall(tool, tool, args))
        def inspect(r): return action("inspect_task_context", {"workspace_id": request["workspace_id"]})
        def query(r):
            selected["context_call_id"] = r.observation.call_id
            return action("query_task_relations", {"workspace_id": request["workspace_id"]})
        def analyze(r):
            selected["relations_call_id"] = r.observation.call_id
            return action("analyze_task_semantics", {"workspace_id": request["workspace_id"], "investigation_id": "dev-public-investigation"})
        def final(r): return ModelAction.final(json.dumps({**selected, "semantic_call_id": r.observation.call_id}))
        model = FakeModelAdapter([inspect, query, analyze, final])
        vault = root / "vault"
        vault.mkdir()
        (vault / "anchor.md").write_text("# Synthetic Dev anchor\n", encoding="utf8")
        scan = scan_vault(vault, root / "scan")
        engine = RuntimeEngine(vault, scan.index_path, root / "checkpoints", trace_dir=root / "traces", model=model,
            task_relation_tools=relation_tools, memory_enabled=False, decision_memory_enabled=False)
        status = engine.start_multi_agent(RunRequest(request_id=request["case_id"], workflow="task_relations",
            query=request["question"], max_steps=4, max_provider_requests=4))
        state = engine.checkpointer.get_latest(status.thread_id)
        result = json.loads((root / "checkpoints" / state.result_ref).read_text(encoding="utf8"))
        self.last = dict(status=status.to_dict(), checkpoint=state.to_dict(), result=result,
                         outer_fake_calls=model.call_count, new_live_provider_requests=0, evaluation_status="NOT_EVALUATED")
        return dict(tasks=[], relations=[], revisions=[], answer_status="ABSTAIN",
            abstentions=["M1_EXTRACTION_AND_TYPED_RELATION_QUESTION_DISCOVERY_NOT_CONNECTED", "SEMANTIC_NOT_EVALUATED"], answer_evidence=[])
