"""Single-budget, two-stage ES2002 development validation (offline by default).

The sample is copied unchanged from the completed v1 development run. All new
A/B and ES2002 requests share one durable USD 0.60 ledger. No model judge, retry,
held-out data, response reuse, or automatic field-stage repair is permitted.
"""
from __future__ import annotations
import argparse
import ast
from contextlib import contextmanager
import csv
from datetime import UTC, datetime
import inspect
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import zipfile

# Direct CLI execution puts scripts/ first; library imports use the repo root.
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
from scripts.eval_task_field_bindings import (ROOT, MEETINGS, MODEL, canonical, digest,
    file_hash, write_new, replace_json, load_json, canonical_content, verify_inputs as verify_v1_inputs)

MAX_USD = .60
MAX_REQUESTS = 256
STAGE1_MAX_OUTPUT = 4096
STAGE2_MAX_OUTPUT = 1536
PRICE = dict(input_per_million=.75, cached_input_per_million=.075,
    output_per_million=3.75, checked="2026-11-02", source="https://ai.google.dev/gemini-api/docs/pricing",
    basis="API input/cached/output including thinking rates; not account invoice")
STAGE_AGENTS = {"semantic-extractor-v1": "stage1", "product-task-fields-stage2-v2": "stage2"}
AB_TEXT = {"A": "联调报告由李明负责，10/12 前完成。", "B": "联调报告改由王芳负责，延期到 10/16。"}


def source_signature():
    files = sorted((ROOT / "src" / "linkloom").rglob("*.py"))
    files += [Path(__file__), ROOT / "scripts" / "eval_task_field_bindings.py"]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): file_hash(path) for path in files}


def source_function(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return ast.get_source_segment(source, node)
    raise ValueError("Missing expected master source contract")


def freeze_master_reference(commit, target):
    target = Path(target)
    archive = subprocess.run(["git", "archive", "--format=zip", commit, "src/linkloom"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    files = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        for member in package.infolist():
            if member.is_dir():
                continue
            name = member.filename
            if not name.startswith("src/linkloom/") or ".." in Path(name).parts or Path(name).is_absolute():
                raise ValueError("Unsafe master archive entry")
            data = package.read(member)
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            files[name] = __import__("hashlib").sha256(data).hexdigest()
    env = {**os.environ, "PYTHONPATH": str((target / "src").resolve()), "PYTHONUTF8": "1"}
    code = "import json; from linkloom.semantic_ingestion.extraction import _EXTRACTION_SCHEMA_SHA256; print(json.dumps(dict(schema_sha256=_EXTRACTION_SCHEMA_SHA256)))"
    value = json.loads(subprocess.run([sys.executable, "-c", code], cwd=target,
        env=env, check=True, text=True, encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout)
    extraction = (target / "src/linkloom/semantic_ingestion/extraction.py").read_text(encoding="utf-8")
    value.update(commit=commit, files=files,
        prompt_source_sha256=digest(source_function(extraction, "_prompt")),
        schema_source_sha256=digest(source_function(extraction, "_extraction_schema")))
    write_new(target / "reference.json", value)
    return value


def prepare(fixed_run, out, master_commit):
    fixed_run, out = Path(fixed_run).resolve(), Path(out).resolve()
    original, _, _ = verify_v1_inputs(fixed_run)
    if (original["sample_count"] != 210 or original["positive_utterances"] != 10
            or original["negative_utterances"] != 200 or len(original["gold_tasks"]) != 11):
        raise ValueError("Only the unchanged approved 210-message sample is permitted")
    out.mkdir(parents=True, exist_ok=False)
    copied = {}
    for relative in ["manifest.json", "negative-review.csv", *["sources/" + mid + ".json" for mid in MEETINGS]]:
        path = out / "fixed" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (fixed_run / relative).read_bytes()
        path.write_bytes(data)
        copied[relative] = file_hash(path)
    reference = freeze_master_reference(master_commit, out / "master-reference")
    protocol = dict(version="two-stage-validation/v1", fixed_files=copied,
        fixed_sample_sha256=digest(original), master_commit=master_commit,
        master_reference_sha256=digest(reference), max_usd=MAX_USD, max_requests=MAX_REQUESTS,
        stage1=dict(max_output_tokens=STAGE1_MAX_OUTPUT, thinking_level="low", temperature=None),
        stage2=dict(max_output_tokens=STAGE2_MAX_OUTPUT, calls="one per actual core-valid task"),
        model=MODEL, ab_originals=AB_TEXT, namespaces=["ab", "es2002"],
        execution="sequential hosts only, fresh replies, no automatic retries or replays")
    write_new(out / "sample-protocol.json", protocol)
    return dict(status="PREPARED_OFFLINE", out=str(out), sample_count=210,
        core_calls=212, dynamic_field_calls=True, max_requests=MAX_REQUESTS, max_usd=MAX_USD, network="not used")


def verify_fixed(out):
    out = Path(out)
    protocol = load_json(out / "sample-protocol.json")
    if (protocol["max_usd"] != MAX_USD or protocol["max_requests"] != MAX_REQUESTS
            or protocol["ab_originals"] != AB_TEXT or protocol["model"] != MODEL):
        raise ValueError("Approved v2 protocol changed")
    for relative, expected in protocol["fixed_files"].items():
        if file_hash(out / "fixed" / relative) != expected:
            raise ValueError("Fixed development sample/review/source changed")
    fixed = load_json(out / "fixed/manifest.json")
    if (digest(fixed) != protocol["fixed_sample_sha256"] or fixed["allowed_meetings"] != list(MEETINGS)
            or fixed["sample_count"] != 210 or fixed["seed"] != 20261101):
        raise ValueError("Fixed sample identity changed")
    reference = load_json(out / "master-reference/reference.json")
    if digest(reference) != protocol["master_reference_sha256"]:
        raise ValueError("Master reference protocol changed")
    for relative, expected in reference["files"].items():
        if file_hash(out / "master-reference" / relative) != expected:
            raise ValueError("Frozen master reference source changed")
    return fixed, protocol, reference


def freeze_run(out):
    out = Path(out)
    _, protocol, reference = verify_fixed(out)
    from linkloom.semantic_ingestion import extraction
    source = inspect.getsource(extraction)
    if (digest(source_function(source, "_prompt")) != reference["prompt_source_sha256"]
            or digest(source_function(source, "_extraction_schema")) != reference["schema_source_sha256"]
            or extraction._EXTRACTION_SCHEMA_SHA256 != reference["schema_sha256"]):
        raise ValueError("Stage1 master prompt/schema contract changed")
    frozen = dict(sample_protocol_sha256=digest(protocol), source_files=source_signature(), pricing=PRICE)
    path = out / "run-protocol.json"
    if path.exists():
        if load_json(path) != frozen:
            raise ValueError("Run signature changed; cannot reuse the validation ledger")
    else:
        write_new(path, frozen)
    return frozen


class ExclusiveRunLock:
    """OS-owned file lock: a second AB/eval process cannot overwrite the ledger."""
    def __init__(self, path):
        self.path = Path(path)
        self.handle = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError):
            self.handle.close()
            raise RuntimeError("Another AB/evaluation process owns the shared ledger") from None

    def close(self):
        if self.handle.closed:
            return
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()

def resource_unavailable(stage, message):
    if stage == "stage2":
        from linkloom.semantic_ingestion.product_task_stages import AnnotationBudgetUnavailable
        raise AnnotationBudgetUnavailable(message)
    raise PermissionError(message)


class SharedBudget:
    """All stages and both namespaces share one process-exclusive ledger."""
    def __init__(self, out):
        self.out = Path(out).resolve()
        self.lock = ExclusiveRunLock(self.out / "budget.lock")
        self.mutex = threading.RLock()
        try:
            fixed, _, _ = verify_fixed(self.out)
            self.frozen = freeze_run(self.out)
            self.allowed = {"ab": {"A", "B"}, "es2002": {row["sample_id"] for row in fixed["samples"]}}
            self.path = self.out / "budget.json"
            if self.path.exists():
                self.state = load_json(self.path)
                if (self.state["run_protocol_sha256"] != digest(self.frozen)
                        or self.state["max_usd"] != MAX_USD or self.state["max_requests"] != MAX_REQUESTS):
                    raise ValueError("Shared budget source/protocol/limits changed")
            else:
                self.state = dict(version="two-stage-shared-budget/v1", run_protocol_sha256=digest(self.frozen),
                    max_usd=MAX_USD, max_requests=MAX_REQUESTS, requests=0, actual_wire_requests=0,
                    charged_upper_bound_usd=0.0, known_usage_cost_usd=0.0, account_invoice_usd=None,
                    active=None, blocked=None, pricing=PRICE, entries=[])
                write_new(self.path, self.state)
            self.verify()
            if self.state["active"] is not None and self.state["blocked"] is None:
                self.state["blocked"] = "UNRESOLVED_PRIOR_REQUEST"
                self.save()
        except Exception:
            self.lock.close()
            raise

    def close(self):
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def save(self):
        replace_json(self.path, self.state)

    def verify(self):
        entries = self.state["entries"]
        if (self.state["requests"] != len(entries) or len(entries) > MAX_REQUESTS
                or self.state["actual_wire_requests"] != sum(bool(row["wire"]) for row in entries)
                or len({row["logical_key"] for row in entries}) != len(entries)):
            raise ValueError("Shared budget count/identity mismatch")
        charged, known = 0.0, 0.0
        for index, row in enumerate(entries, 1):
            if (row["sequence"] != index or row["namespace"] not in self.allowed
                    or row["sample_id"] not in self.allowed[row["namespace"]]
                    or row["stage"] not in {"stage1", "stage2"}):
                raise ValueError("Shared budget has an unapproved request identity")
            paid = row.get("cost_usd_from_usage")
            if paid is not None:
                if (not row["wire"] or not math.isfinite(paid) or not 0 <= paid <= row["reserved_usd"]):
                    raise ValueError("Shared budget has invalid settled usage")
                usage = row.get("usage", {})
                prompt, visible, thought, cached = (usage.get("prompt_token_count"),
                    usage.get("candidates_token_count"), usage.get("thoughts_token_count", 0),
                    usage.get("cached_content_token_count", 0))
                if (any(type(value) is not int or value < 0 for value in (prompt, visible, thought, cached))
                        or cached > prompt):
                    raise ValueError("Saved usage is invalid")
                expected = ((prompt - cached) * PRICE["input_per_million"]
                    + cached * PRICE["cached_input_per_million"]
                    + (visible + thought) * PRICE["output_per_million"]) / 1e6
                if not math.isclose(paid, expected, abs_tol=1e-12):
                    raise ValueError("Saved usage does not match its recorded cost")
                charged += paid
                known += paid
            else:
                charged += row["reserved_usd"]
            if row["status"] == "COMPLETE":
                for kind in ("request", "response", "model_response"):
                    if file_hash(self.out / row[kind + "_path"]) != row[kind + "_sha256"]:
                        raise ValueError("Saved request/response pairing changed")
        if (not math.isclose(charged, self.state["charged_upper_bound_usd"], abs_tol=1e-10)
                or not math.isclose(known, self.state["known_usage_cost_usd"], abs_tol=1e-10)
                or charged > MAX_USD + 1e-10):
            raise ValueError("Shared budget usage/reservation total mismatch")

    def ensure_scope(self, namespace, sample_id):
        if namespace not in self.allowed or sample_id not in self.allowed[namespace]:
            raise ValueError("Request outside approved A/B and fixed development sample")

    def reserve(self, metadata, request, body, max_output):
        with self.mutex:
            self.ensure_scope(metadata["namespace"], metadata["sample_id"])
            if self.state["blocked"] or self.state["active"] is not None:
                resource_unavailable(metadata["stage"], "Shared budget stopped; no external request may be replayed")
            stage = metadata["stage"]
            task_key = "core" if stage == "stage1" else request.task_id
            logical_key = digest([metadata["namespace"], metadata["sample_id"], stage, task_key])
            if any(row["logical_key"] == logical_key for row in self.state["entries"]):
                raise RuntimeError("Duplicate stage/sample/task request blocked; do not replay")
            expected_output = STAGE1_MAX_OUTPUT if stage == "stage1" else STAGE2_MAX_OUTPUT
            if max_output != expected_output:
                raise ValueError("Stage output cap changed from the approved protocol")
            reserve = ((len(canonical(body).encode("utf-8")) + 1024) * PRICE["input_per_million"]
                + max_output * PRICE["output_per_million"]) / 1e6
            if self.state["requests"] >= MAX_REQUESTS or self.state["charged_upper_bound_usd"] + reserve > MAX_USD:
                self.state["blocked"] = "REQUEST_OR_COST_CEILING"
                self.save()
                resource_unavailable(stage, "Shared USD0.60/request ceiling reached before HTTP")
            sequence = self.state["requests"] + 1
            request_path = Path("requests") / f"{sequence:04d}.json"
            write_new(self.out / request_path, dict(metadata=metadata, request=request.to_dict(), wire_body=body))
            entry = dict(sequence=sequence, **metadata, task_key=task_key, logical_key=logical_key,
                run_id=request.run_id, task_id=request.task_id, turn_id=request.turn_id,
                request_path=request_path.as_posix(), request_sha256=file_hash(self.out / request_path),
                reserved_usd=reserve, status="RESERVED", wire=False)
            self.state["entries"].append(entry)
            self.state["requests"] = sequence
            self.state["active"] = sequence
            self.state["charged_upper_bound_usd"] += reserve
            self.save()
            return sequence

    def active(self):
        if self.state["active"] is None:
            raise RuntimeError("No reserved shared-budget request")
        return self.state["entries"][self.state["active"] - 1]

    def wire(self, request):
        with self.mutex:
            row = self.active()
            if (self.state["blocked"] or row["wire"]
                    or self.state["actual_wire_requests"] >= self.state["requests"]
                    or request.method != "POST" or request.url.host != "generativelanguage.googleapis.com"
                    or not request.url.path.endswith(":generateContent")):
                raise RuntimeError("Unexpected/unreserved/repeated provider wire blocked")
            row["wire"] = True
            self.state["actual_wire_requests"] += 1
            self.save()

    def response(self, raw):
        with self.mutex:
            row = self.active()
            response_path = Path("responses") / f'{row["sequence"]:04d}.json'
            from scripts.eval_task_field_bindings import sanitize_response
            write_new(self.out / response_path, sanitize_response(raw))
            row.update(response_path=response_path.as_posix(), response_sha256=file_hash(self.out / response_path))
            usage = raw.get("usage_metadata", {})
            prompt, visible, thought, cached = (usage.get("prompt_token_count"),
                usage.get("candidates_token_count"), usage.get("thoughts_token_count", 0),
                usage.get("cached_content_token_count", 0))
            if (any(type(value) is not int or value < 0 for value in (prompt, visible, thought, cached))
                    or cached > prompt or not row["wire"] or row["status"] != "RESERVED"):
                self.save()
                raise ValueError("Provider usage missing/invalid; reservation retained")
            cost = ((prompt - cached) * PRICE["input_per_million"] + cached * PRICE["cached_input_per_million"]
                + (visible + thought) * PRICE["output_per_million"]) / 1e6
            if cost > row["reserved_usd"]:
                self.save()
                raise ValueError("Provider cost exceeded conservative reservation")
            row.update(usage=usage, cost_usd_from_usage=cost, status="USAGE_KNOWN")
            self.state["known_usage_cost_usd"] += cost
            self.state["charged_upper_bound_usd"] += cost - row["reserved_usd"]
            self.save()

    def finish(self, response):
        with self.mutex:
            row = self.active()
            if row["status"] != "USAGE_KNOWN":
                raise ValueError("Unknown response cannot complete or release its reservation")
            path = Path("model-responses") / f'{row["sequence"]:04d}.json'
            write_new(self.out / path, response.to_dict())
            row.update(model_response_path=path.as_posix(), model_response_sha256=file_hash(self.out / path), status="COMPLETE")
            self.state["active"] = None
            self.save()

    def fail_active(self, error):
        with self.mutex:
            if self.state["active"] is not None:
                row = self.active()
                row.update(status="UNRESOLVED", failure_type=type(error).__name__)
                self.state["blocked"] = "UNRESOLVED_PROVIDER_OUTCOME"
                self.save()

    def save_result(self, namespace, sample_id, result):
        with self.mutex:
            self.ensure_scope(namespace, sample_id)
            related = [row for row in self.state["entries"] if (row["namespace"], row["sample_id"]) == (namespace, sample_id)]
            if not related or not any(row["stage"] == "stage1" for row in related):
                raise ValueError("Result has no matching core request receipt")
            path = Path("results") / namespace / (sample_id + ".json")
            value = dict(namespace=namespace, sample_id=sample_id, result=result,
                ledger_sequences=[row["sequence"] for row in related])
            write_new(self.out / path, value)
            result_hash = file_hash(self.out / path)
            for row in related:
                row.update(result_path=path.as_posix(), result_sha256=result_hash)
            self.save()
            return value

    def prior_result(self, namespace, sample_id):
        with self.mutex:
            self.ensure_scope(namespace, sample_id)
            rows = [row for row in self.state["entries"] if (row["namespace"], row["sample_id"]) == (namespace, sample_id)]
            if not rows:
                return None
            if any("result_path" not in row for row in rows):
                raise RuntimeError("Prior stage has no complete local result; no automatic rerun")
            paths = {row["result_path"] for row in rows}
            hashes = {row["result_sha256"] for row in rows}
            if len(paths) != 1 or len(hashes) != 1:
                raise ValueError("Stage/result pairing is inconsistent")
            path = self.out / paths.pop()
            if file_hash(path) != hashes.pop():
                raise ValueError("Saved result changed")
            return load_json(path)

class BudgetedAdapter:
    """One actual Gemini adapter, with trusted per-message scope for both stages."""
    def __init__(self, provider, budget):
        self.provider, self.budget = provider, budget
        self.local = threading.local()

    @contextmanager
    def scope(self, namespace, sample_id, *, stage1_prompt_sha256, source_text_sha256):
        self.budget.ensure_scope(namespace, sample_id)
        if getattr(self.local, "scope", None) is not None:
            raise RuntimeError("Nested request scopes are not allowed")
        self.local.scope = dict(namespace=namespace, sample_id=sample_id,
            stage1_prompt_sha256=stage1_prompt_sha256, source_text_sha256=source_text_sha256)
        try:
            yield
        finally:
            self.local.scope = None

    def complete(self, request):
        scope = getattr(self.local, "scope", None)
        if scope is None:
            raise RuntimeError("Provider request has no approved sample/message scope")
        if request.model_id != MODEL or request.agent_id not in STAGE_AGENTS:
            raise ValueError("Unapproved model/stage agent")
        stage = STAGE_AGENTS[request.agent_id]
        tool = "emit_semantic_candidates" if stage == "stage1" else "emit_task_field_annotations"
        if len(request.available_tools) != 1 or request.available_tools[0].tool_id != tool:
            raise ValueError("Stage tool contract changed")
        if stage == "stage1":
            reference = load_json(self.budget.out / "master-reference/reference.json")
            if (digest(request.available_tools[0].input_schema) != reference["schema_sha256"]
                    or __import__("hashlib").sha256(request.user_input.encode("utf-8")).hexdigest() != scope["stage1_prompt_sha256"]):
                raise ValueError("Stage1 request differs from original master prompt/schema")
        import re
        match = re.search(r"_c([0-9]+)$", request.task_id) if stage == "stage2" else None
        if stage == "stage2" and match is None:
            raise ValueError("Field-stage request lost its original core claim index")
        metadata = {**scope, "stage": stage, "original_claim_index": int(match.group(1)) if match else None}
        # The underlying transport uses this immutable request metadata when it
        # reserves the exact mapped SDK body, before any real HTTP wire.
        with self.budget.mutex:
            self.local.request, self.local.metadata = request, metadata
            try:
                response = self.provider.complete(request)
                if self.budget.state["active"] is not None:
                    if self.budget.active()["status"] == "USAGE_KNOWN":
                        self.budget.finish(response)
                    else:
                        self.budget.fail_active(RuntimeError("Provider did not return a metered response"))
                return response
            except Exception as error:
                self.budget.fail_active(error)
                raise
            finally:
                self.local.request = self.local.metadata = None


class LiveSession:
    """Create one SDK client with retries disabled and the shared wire hook."""
    def __init__(self, budget):
        from dotenv import load_dotenv
        from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
        load_dotenv(ROOT / ".env", override=False)
        key = os.environ.get("GEMINI_API_KEY")
        if not key or not key.strip():
            raise ValueError("GEMINI_API_KEY unavailable; no model request made")
        self.budget = budget
        self.sdk = build_gemini_sdk_client(key, http_options_kwargs=dict(timeout=120000,
            retry_options=dict(attempts=1), client_args=dict(follow_redirects=False,
            event_hooks=dict(request=[budget.wire]))))
        session = self
        class Transport:
            def generate_content(self, *, model, contents, config):
                adapter = session.adapter
                request, metadata = getattr(adapter.local, "request", None), getattr(adapter.local, "metadata", None)
                if request is None or metadata is None or model != MODEL:
                    raise RuntimeError("Mapped SDK request lacks its trusted stage reservation")
                budget.reserve(metadata, request, dict(contents=contents, config=config), config["max_output_tokens"])
                try:
                    response = session.sdk.models.generate_content(model=model, contents=contents, config=config)
                    raw = response if isinstance(response, dict) else response.model_dump(mode="json", exclude_none=True)
                    budget.response(raw)
                    return response
                except Exception as error:
                    budget.fail_active(error)
                    raise
        provider = GeminiProviderAdapter(Transport(), model_id=MODEL)
        self.adapter = BudgetedAdapter(provider, budget)

    def close(self):
        self.sdk.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def scoped_extractor(budget, adapter, namespace, sample_for_segment, *, save_extraction=False):
    """Subclass preserves ProductApplication's normal Provider extractor dispatch.

    For A/B use sample_for_segment=lambda s: exact_ab_sample(s.body_text).
    For ES2002 use the selected canonical sample ID. Never parse a gold answer.
    """
    from linkloom.semantic_ingestion.extraction import (ProviderNeutralSemanticExtractor,
        ProviderRequestAuthorization, _prompt)
    guard_id = "two-stage-shared-validation/v1"
    def authorize(request):
        if budget.state["blocked"]:
            resource_unavailable(STAGE_AGENTS.get(request.agent_id, "stage1"), "Shared validation budget is stopped")
        return ProviderRequestAuthorization.approve(request, guard_id=guard_id,
            reservation_id=f'shared-{budget.state["requests"] + 1}',
            estimated_cost_upper_bound_usd=MAX_USD - budget.state["charged_upper_bound_usd"])
    class ScopedExtractor(ProviderNeutralSemanticExtractor):
        def extract(self, segment, context):
            sample_id = sample_for_segment(segment)
            if budget.prior_result(namespace, sample_id) is not None:
                raise RuntimeError("Saved sample already completed; do not re-extract")
            # _prompt is checked against frozen master source by freeze_run;
            # the second stage cannot substitute a task-only first prompt.
            expected_prompt = _prompt(segment, context, self.prompt_version)
            with adapter.scope(namespace, sample_id,
                    stage1_prompt_sha256=__import__("hashlib").sha256(expected_prompt.encode("utf-8")).hexdigest(),
                    source_text_sha256=digest(segment.body_text)):
                result = super().extract(segment, context)
            if save_extraction:
                budget.save_result(namespace, sample_id, dict(extraction=result.to_dict()))
            return result
    return ScopedExtractor(adapter, provider_id="gemini", model_id=MODEL,
        request_guard=authorize, request_guard_id=guard_id, max_attempts=1,
        max_output_tokens=STAGE1_MAX_OUTPUT, temperature=None, thinking_level="low")


def exact_ab_sample(text):
    matches = [name for name, original in AB_TEXT.items() if text == original]
    if len(matches) != 1:
        raise ValueError("A/B sentence changed or unexpected source; no request permitted")
    return matches[0]


def run_evaluation(out, live=False):
    if not live:
        raise ValueError("No model calls made: --live required")
    out = Path(out).resolve()
    fixed, _, _ = verify_fixed(out)
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
    from linkloom.semantic_ingestion.extraction import SemanticExtractionContext, ProductTaskFieldContext
    from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
    from linkloom.semantic_ingestion.validation import CandidateValidator
    from linkloom.ui.product import DEFAULT_PRODUCT_RELATION_RESOLVER
    meetings = {mid: load_json(out / "fixed/sources" / (mid + ".json")) for mid in MEETINGS}
    artifacts = {mid: RawArtifact(workspace_id="es2002-two-stage", artifact_id=mid,
        source_type="timestamped_text", content=canonical_content(meeting),
        ingestion_time=datetime(2000, 1, 1, tzinfo=UTC)) for mid, meeting in meetings.items()}
    parsed = {mid: parse_timestamped_text(artifact).segments for mid, artifact in artifacts.items()}
    with SharedBudget(out) as budget:
        with LiveSession(budget) as session:
            current = {}
            extractor = scoped_extractor(budget, session.adapter, "es2002", lambda segment: current["sample_id"])
            pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
            for item in fixed["samples"]:
                sample_id = item["sample_id"]
                if budget.prior_result("es2002", sample_id) is not None:
                    continue
                if budget.state["blocked"]:
                    raise RuntimeError("Shared budget stopped; preserve already saved task results")
                current["sample_id"] = sample_id
                artifact = artifacts[item["meeting_id"]]
                segment = parsed[item["meeting_id"]][item["ordinal"]]
                if segment.body_text != item["text"] or segment.speaker != item["speaker"]:
                    raise ValueError("Canonical fixed sample changed")
                context = SemanticExtractionContext(artifact.workspace_id, DEFAULT_PRODUCT_RELATION_RESOLVER,
                    product_task_fields=ProductTaskFieldContext(artifact, version="product-task-fields/v2"))
                trace = pipeline.process_segment_with_trace(segment, artifact, context)
                candidates = []
                for result in trace.candidate_results:
                    candidate = result.candidate
                    if candidate is not None and candidate.task_observation is not None:
                        candidates.append(dict(prediction_id=f"{sample_id}:{len(candidates) + 1}",
                            observation=candidate.task_observation.to_dict(), outcome=result.outcome.value,
                            reason_codes=[reason.value for reason in result.reason_codes]))
                output = dict(extraction=trace.extraction.to_dict(), candidates=candidates,
                    validation_results=[dict(outcome=result.outcome.value,
                        candidate_id=result.candidate.candidate_id if result.candidate else None,
                        reason_codes=[reason.value for reason in result.reason_codes]) for result in trace.candidate_results])
                budget.save_result("es2002", sample_id, output)
                if trace.extraction.failure_code is not None:
                    raise RuntimeError("Core stage failed; task recall is not an empty-success result")
                print(json.dumps(dict(event="sample_complete", sample_id=sample_id, candidates=len(candidates),
                    requests=budget.state["requests"], known_usage_cost_usd=budget.state["known_usage_cost_usd"],
                    charged_upper_bound_usd=budget.state["charged_upper_bound_usd"], blocked=budget.state["blocked"])), flush=True)
        return budget.state


def preview(out):
    """Freeze code and show sample/prompt identities without constructing an SDK."""
    out = Path(out).resolve()
    fixed, protocol, reference = verify_fixed(out)
    frozen = freeze_run(out)
    return dict(status="FROZEN_OFFLINE", network="not used", samples=fixed["sample_count"],
        positive_utterances=fixed["positive_utterances"], gold_tasks=len(fixed["gold_tasks"]),
        negative_utterances=fixed["negative_utterances"], seed=fixed["seed"],
        total_core_calls_including_ab=212, field_calls="one per core-valid task",
        max_usd=MAX_USD, max_requests=MAX_REQUESTS,
        stage1_master_commit=protocol["master_commit"],
        stage1_schema_sha256=reference["schema_sha256"], run_protocol_sha256=digest(frozen))


def completed_results(out):
    out = Path(out).resolve()
    fixed, _, _ = verify_fixed(out)
    with SharedBudget(out) as budget:
        if budget.state["active"] is not None or budget.state["blocked"]:
            raise ValueError("Blocked/incomplete budget cannot be reported as a complete run")
        values = {}
        for item in fixed["samples"]:
            saved = budget.prior_result("es2002", item["sample_id"])
            if saved is None:
                raise ValueError("Missing fixed sample; cannot complete comparison")
            if saved["result"]["extraction"].get("failure_code") is not None:
                raise ValueError("Core extraction failure cannot count as an empty success")
            values[item["sample_id"]] = saved["result"]
        for name in AB_TEXT:
            if budget.prior_result("ab", name) is None:
                raise ValueError("Missing A/B result in the same shared budget")
        entries = budget.state["entries"]
        core = [row for row in entries if row["stage"] == "stage1"]
        if len(core) != 212 or any(row["status"] != "COMPLETE" for row in entries):
            raise ValueError("Expected exactly 212 complete stage1 calls and complete field calls")
        return fixed, dict(budget.state), values


def judgment_template(out):
    out = Path(out).resolve()
    fixed, _, _ = completed_results(out)
    path = out / "gold-review.csv"
    fields = ["gold_id", "prediction_id", "extracted", "owner_correct", "deadline_correct", "reason"]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for gold in fixed["gold_tasks"]:
            writer.writerow(dict.fromkeys(fields, "") | dict(gold_id=gold["gold_id"]))
    return dict(path=str(path), network="not used", judgment="human only; unchanged prior scoring policy")


def report(out, judgments):
    """Check one-to-one human matches; calculate denominators, never model-judge."""
    from scripts.eval_task_field_bindings import boolean
    out = Path(out).resolve()
    fixed, budget, results = completed_results(out)
    with Path(judgments).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    gold_by_id = {row["gold_id"]: row for row in fixed["gold_tasks"]}
    if len(rows) != 11 or {row["gold_id"] for row in rows} != set(gold_by_id):
        raise ValueError("Human judgments must cover all eleven tasks exactly once")
    used, by_gold = set(), {}
    for row in rows:
        gid = row["gold_id"]
        extracted, owner, deadline = (boolean(row[name]) for name in ("extracted", "owner_correct", "deadline_correct"))
        possible = {candidate["prediction_id"] for sid in fixed["gold_to_samples"][gid]
            for candidate in results[sid]["candidates"]}
        if extracted:
            if row["prediction_id"] not in possible or row["prediction_id"] in used:
                raise ValueError("Matched task absent/outside gold evidence/already matched")
            used.add(row["prediction_id"])
        elif row["prediction_id"] or owner or deadline:
            raise ValueError("An unextracted task cannot have correct fields")
        if gold_by_id[gid]["deadline"] == "未说明" and deadline:
            raise ValueError("Unstated gold deadline cannot score correct")
        if not row["reason"].strip():
            raise ValueError("Each human judgment needs a reason")
        by_gold[gid] = dict(extracted=extracted, owner_correct=owner, deadline_correct=deadline,
            deadline_scored=gold_by_id[gid]["deadline"] != "未说明",
            prediction_id=row["prediction_id"] or None, reason=row["reason"])
    negatives = [row for row in fixed["samples"] if row["sample_kind"] == "reference_negative"]
    false_counts = [len(results[row["sample_id"]]["candidates"]) for row in negatives]
    counts = dict(extracted=sum(row["extracted"] for row in by_gold.values()),
        owner_correct=sum(row["owner_correct"] for row in by_gold.values()),
        deadline_correct=sum(row["deadline_correct"] for row in by_gold.values()),
        gold_tasks=11, negative_utterances=200,
        false_positive_utterances=sum(bool(count) for count in false_counts),
        false_positive_candidates=sum(false_counts))
    gates = dict(extraction=counts["extracted"] >= 8, owner=counts["owner_correct"] >= 3,
        negative_utterances=counts["false_positive_utterances"] <= 2,
        negative_candidates=counts["false_positive_candidates"] <= 2)
    stages = {namespace: {stage: sum(row["namespace"] == namespace and row["stage"] == stage
        for row in budget["entries"]) for stage in ("stage1", "stage2")} for namespace in ("ab", "es2002")}
    value = dict(status="COMPLETE_DEVELOPMENT_SAMPLE", target_passed=all(gates.values()),
        targets=gates, counts=counts, per_gold=by_gold, seed=fixed["seed"], stages=stages,
        requests=budget["requests"], actual_wire_requests=budget["actual_wire_requests"],
        known_usage_cost_usd=budget["known_usage_cost_usd"],
        charged_upper_bound_usd=budget["charged_upper_bound_usd"], max_usd=MAX_USD,
        account_invoice_usd=None, pricing=budget["pricing"], human_judgments_sha256=file_hash(judgments),
        limitations=["ES2002 development sample only; no held-out data", "No repeated paid trial or model judge",
            "Sample, seed and scoring denominators unchanged", "API usage priced at recorded rates, not account invoice"])
    write_new(out / "comparison.json", value)
    lines = ["# 两阶段 ES2002 开发集抽样", "", "固定10条发言包含11条金标准待办，加原200条负样本；未使用保留集。", "",
        "| 金标准待办 | 抽出 | 负责人正确 | 截止正确 |", "| --- | --- | --- | --- |"]
    yn = lambda value: "是" if value else "否"
    for gold in fixed["gold_tasks"]:
        row = by_gold[gold["gold_id"]]
        lines.append("| " + " | ".join([gold["gold_id"], yn(row["extracted"]), yn(row["owner_correct"]),
            yn(row["deadline_correct"]) if row["deadline_scored"] else "未注明，不计分"]) + " |")
    lines += ["", f"抽出 {counts['extracted']}/11；负责人正确 {counts['owner_correct']}/11；负样本误报发言 {counts['false_positive_utterances']}/200、误报候选 {counts['false_positive_candidates']}。",
        "", f"门槛结果：{'通过' if all(gates.values()) else '未通过，未付费重试'}。",
        "", f"共 {budget['actual_wire_requests']} 次实际请求（含 A/B 所有阶段），用量计价 USD {budget['known_usage_cost_usd']:.9f}，上限 USD 0.60；不是账户实扣。", ""]
    (out / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    prep = sub.add_parser("prepare")
    prep.add_argument("--fixed-run", required=True, type=Path)
    prep.add_argument("--out", required=True, type=Path)
    prep.add_argument("--master-commit", required=True)
    check = sub.add_parser("preview")
    check.add_argument("--out", required=True, type=Path)
    live = sub.add_parser("run")
    live.add_argument("--out", required=True, type=Path)
    live.add_argument("--live", action="store_true")
    template = sub.add_parser("judgment-template")
    template.add_argument("--out", required=True, type=Path)
    reporting = sub.add_parser("report")
    reporting.add_argument("--out", required=True, type=Path)
    reporting.add_argument("--judgments", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command is None:
        value = dict(network="not used", max_usd=MAX_USD, max_requests=MAX_REQUESTS,
            command="prepare / preview / run --live / judgment-template / report")
    elif args.command == "prepare":
        value = prepare(args.fixed_run, args.out, args.master_commit)
    elif args.command == "preview":
        value = preview(args.out)
    elif args.command == "run":
        if not args.live:
            parser.error("No model calls made: --live required")
        value = run_evaluation(args.out, live=True)
    elif args.command == "judgment-template":
        value = judgment_template(args.out)
    else:
        value = report(args.out, args.judgments)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return value


if __name__ == "__main__":
    main()
