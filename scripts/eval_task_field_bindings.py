"""Bounded ES2002 product-profile comparison; default commands are offline.

No model judge is used. Prompts contain only the selected canonical message,
not reference answers or sample labels. Reference negatives need human review.
"""
from __future__ import annotations
import argparse
import csv
from datetime import UTC, datetime, timedelta
import hashlib
import io
import json
import math
import os
from pathlib import Path
import random
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "8b3944d9a228c31614937c98b95f9bb4d28de6b2"
MEETINGS = tuple("ES2002" + part for part in "abcd")
SEED = 20261101
MODEL = "gemini-3.8-flash"
MAX_USD = 1.0
MAX_OUTPUT = 4096
MANIFEST_VERSION = "task-field-sample/v1"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def replace_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".pending")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def load_development(data, gold_path):
    # Only literal filenames are opened. Never enumerate other meeting series.
    meetings = {}
    for mid in MEETINGS:
        meeting = load_json(Path(data) / "transcripts" / (mid + ".json"))
        if meeting["meeting_id"] != mid:
            raise ValueError("Development filename/meeting identity mismatch")
        meetings[mid] = meeting
    gold = [json.loads(line) for line in Path(gold_path).read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    if len(gold) != 11 or len({row["gold_id"] for row in gold}) != 11:
        raise ValueError("Exactly eleven unique development gold tasks required")
    if any(row["meeting_id"] not in MEETINGS for row in gold):
        raise ValueError("Only ES2002 development references permitted")
    return meetings, gold


def utterance_inventory(meetings):
    inventory = []
    for mid in MEETINGS:
        meeting = meetings[mid]
        for ordinal, utterance in enumerate(meeting["utterances"]):
            start, end = utterance["char_start"], utterance["char_end"]
            prefix = f'{utterance["speaker"]} [{utterance["start"]:.3f}-{utterance["end"]:.3f}]: '
            if (meeting["text"][start:end] != prefix + utterance["text"] + "\n"
                    or not utterance["text"].strip() or "\n" in utterance["text"]):
                raise ValueError("AMI utterance differs from unchanged original")
            inventory.append(dict(sample_id=f"{mid}-{ordinal:04d}", meeting_id=mid,
                ordinal=ordinal, segment_id=utterance["segment_id"], speaker=utterance["speaker"],
                text=utterance["text"], start=utterance["start"], char_start=start, char_end=end, gold_ids=[]))
    return inventory


def build_manifest(meetings, gold):
    inventory = utterance_inventory(meetings)
    gold_map = {}
    for row in gold:
        mid, quote = row["meeting_id"], row["quote"]
        source = meetings[mid]["text"]
        if not quote or source.count(quote) != 1:
            raise ValueError("Gold quote must occur exactly once in its source")
        start, end = source.index(quote), source.index(quote) + len(quote)
        matches = [item for item in inventory if item["meeting_id"] == mid
            and max(start, item["char_start"]) < min(end, item["char_end"])]
        if not matches:
            raise ValueError("Gold task has no canonical utterance")
        gold_map[row["gold_id"]] = [item["sample_id"] for item in matches]
        for item in matches:
            item["gold_ids"].append(row["gold_id"])
    positive = [item for item in inventory if item["gold_ids"]]
    eligible = [item for item in inventory if not item["gold_ids"]]
    if len(eligible) < 200:
        raise ValueError("Not enough reference-negative utterances")
    chosen = {item["sample_id"] for item in random.Random(SEED).sample(eligible, 200)}
    samples = [{**item, "sample_kind": "gold_utterance" if item["gold_ids"] else "reference_negative"}
               for item in inventory if item["gold_ids"] or item["sample_id"] in chosen]
    return dict(version=MANIFEST_VERSION, baseline_commit=BASELINE, seed=SEED,
        allowed_meetings=list(MEETINGS), positive_utterances=len(positive), negative_utterances=200,
        sample_count=len(samples), max_requests=2 * len(samples), max_usd=MAX_USD,
        gold_tasks=gold, gold_to_samples=gold_map, samples=samples,
        generation=dict(model=MODEL, max_output_tokens=MAX_OUTPUT, thinking_level="low", temperature=None),
        source_hashes={mid: digest(meetings[mid]) for mid in MEETINGS}, gold_sha256=digest(gold),
        negative_definition="Outside gold quotes; human non-task review required before live requests",
        model_input_policy="Only selected canonical message/speaker/time, no gold/labels/other messages")


def freeze_baseline(directory):
    directory = Path(directory)
    archive = subprocess.run(["git", "archive", "--format=zip", BASELINE, "src/linkloom"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout
    hashes = {}
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        for member in package.infolist():
            name = member.filename
            if member.is_dir():
                continue
            if (not name.startswith("src/linkloom/") or Path(name).is_absolute()
                    or ".." in Path(name).parts or (member.external_attr >> 16) & 0o170000 == 0o120000):
                raise ValueError("Unsafe baseline archive member")
            value = package.read(member)
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(value)
            hashes[name] = hashlib.sha256(value).hexdigest()
    write_new(directory / "snapshot.json", dict(commit=BASELINE, files=hashes))


def verify_snapshot(directory):
    directory = Path(directory)
    manifest = load_json(directory / "snapshot.json")
    if manifest["commit"] != BASELINE or any(file_hash(directory / p) != h for p, h in manifest["files"].items()):
        raise ValueError("Frozen baseline source changed")
    return digest(manifest)


def prepare(data, gold_path, out):
    out = Path(out)
    meetings, gold = load_development(data, gold_path)
    manifest = build_manifest(meetings, gold)
    out.mkdir(parents=True, exist_ok=False)
    freeze_baseline(out / "baseline")
    write_new(out / "manifest.json", manifest)
    for mid in MEETINGS:
        write_new(out / "sources" / (mid + ".json"), meetings[mid])
    fields = ["sample_id", "meeting_id", "ordinal", "speaker", "text", "non_task", "reason"]
    with (out / "negative-review.csv").open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in manifest["samples"]:
            if item["sample_kind"] == "reference_negative":
                writer.writerow({**{key: item[key] for key in fields[:5]}, "non_task": "", "reason": ""})
    lines = ["# ES2002 固定负样本（调用前人工核对）", "",
        "固定种子20261101；未标gold不自动等于没有待办。请在negative-review.csv明确填non_task=true/false。",
        "漏标先报告，不按模型结果重抽。", ""]
    for item in manifest["samples"]:
        if item["sample_kind"] == "reference_negative":
            lines += [f'## {item["sample_id"]} · ordinal {item["ordinal"]} · {item["speaker"]}', "", item["text"], ""]
    (out / "negative-review.md").write_text("\n".join(lines), encoding="utf-8")
    return dict(manifest=str(out / "manifest.json"), sample_count=manifest["sample_count"],
        positive_utterances=manifest["positive_utterances"], negative_utterances=200,
        max_requests=manifest["max_requests"], max_usd=MAX_USD, network="not used")


def verify_inputs(out):
    out = Path(out)
    manifest = load_json(out / "manifest.json")
    if (manifest["version"] != MANIFEST_VERSION or manifest["baseline_commit"] != BASELINE
            or manifest["allowed_meetings"] != list(MEETINGS) or manifest["seed"] != SEED
            or manifest["negative_utterances"] != 200 or manifest["max_usd"] != MAX_USD):
        raise ValueError("Frozen development protocol mismatch")
    meetings = {mid: load_json(out / "sources" / (mid + ".json")) for mid in MEETINGS}
    if build_manifest(meetings, manifest["gold_tasks"]) != manifest:
        raise ValueError("Frozen sample/gold/source protocol changed")
    snapshot_hash = verify_snapshot(out / "baseline")
    with (out / "negative-review.csv").open(encoding="utf-8-sig", newline="") as stream:
        reviews = list(csv.DictReader(stream))
    negatives = [row for row in manifest["samples"] if row["sample_kind"] == "reference_negative"]
    if len(reviews) != 200 or len({row["sample_id"] for row in reviews}) != 200:
        raise ValueError("Negative review must cover exactly the 200 frozen utterances")
    by_id = {row["sample_id"]: row for row in reviews}
    for item in negatives:
        row = by_id.get(item["sample_id"])
        if row is None or any(str(item[key]) != row[key] for key in ("meeting_id", "ordinal", "speaker", "text")):
            raise ValueError("Negative review changed an original utterance")
        if row["non_task"].strip().lower() != "true":
            raise ValueError("All negatives need human non_task=true before requests")
    return manifest, meetings, dict(manifest_sha256=digest(manifest),
        negative_review_sha256=file_hash(out / "negative-review.csv"), baseline_snapshot_sha256=snapshot_hash)

class DurableBudget:
    """One sequential run: durable reserve before wire, never replay unknowns."""
    def __init__(self, path, protocol, max_requests, pricing):
        self.path = Path(path)
        self.protocol, self.max_requests, self.pricing = protocol, max_requests, pricing
        rates = [pricing[name] for name in ("input_per_million", "cached_input_per_million", "output_per_million")]
        if (type(max_requests) is not int or max_requests < 1
                or any(type(rate) not in (int, float) or not math.isfinite(rate) or rate < 0 for rate in rates)
                or rates[0] <= 0 or rates[2] <= 0 or rates[1] > rates[0]):
            raise ValueError("Invalid request/price limits")
        if self.path.exists():
            self.state = load_json(self.path)
            if (self.state["protocol"] != protocol or self.state["max_requests"] != max_requests
                    or self.state["max_usd"] != MAX_USD or self.state["pricing"] != pricing):
                raise ValueError("Budget/resume protocol changed")
            if self.state["failure"] or self.state["active"] is not None:
                raise ValueError("Unknown/failed outcome retained; no automatic replay")
        else:
            self.state = dict(version="task-field-budget/v1", protocol=protocol,
                max_requests=max_requests, max_usd=MAX_USD, pricing=pricing,
                requests=0, actual_wire_requests=0, charged_upper_bound_usd=0.0,
                known_usage_cost_usd=0.0, account_invoice_usd=None,
                active=None, failure=None, entries=[])
            write_new(self.path, self.state)
        self.verify_complete_prefix()

    def verify_complete_prefix(self):
        entries = self.state["entries"]
        if (self.state["requests"] != len(entries)
                or self.state["actual_wire_requests"] != len(entries)
                or any(row["sequence"] != i + 1 or row["status"] != "COMPLETE"
                       or not row["wire"] for i, row in enumerate(entries))):
            raise ValueError("Incomplete/corrupt prefix cannot be replayed automatically")
        if (len({(row["variant"], row["sample_id"]) for row in entries}) != len(entries)
                or self.state["requests"] > self.max_requests
                or self.state["charged_upper_bound_usd"] > MAX_USD
                or any(row["variant"] not in {"old", "new"}
                       or not math.isfinite(row["cost_usd_from_usage"])
                       or not 0 <= row["cost_usd_from_usage"] <= row["reserved_usd"] for row in entries)):
            raise ValueError("Invalid/duplicate budget prefix")
        total = sum(row["cost_usd_from_usage"] for row in entries)
        if (not math.isclose(total, self.state["known_usage_cost_usd"], abs_tol=1e-12)
                or not math.isclose(total, self.state["charged_upper_bound_usd"], abs_tol=1e-12)):
            raise ValueError("Budget prefix does not match saved usage")

    def save(self):
        replace_json(self.path, self.state)

    def reserve(self, variant, sample_id, model, body, max_output):
        if self.state["failure"] or self.state["active"] is not None or model != MODEL:
            raise RuntimeError("Run stopped or request already active")
        if variant not in {"old", "new"} or any((row["variant"], row["sample_id"]) == (variant, sample_id)
                for row in self.state["entries"]):
            raise ValueError("Invalid/duplicate sample request")
        reserved = ((len(canonical(body).encode("utf-8")) + 1024) * self.pricing["input_per_million"]
                    + max_output * self.pricing["output_per_million"]) / 1_000_000
        if (self.state["requests"] + 1 > self.max_requests
                or self.state["charged_upper_bound_usd"] + reserved > MAX_USD):
            raise RuntimeError("Request/cost ceiling reached before HTTP")
        sequence = self.state["requests"] + 1
        row = dict(sequence=sequence, variant=variant, sample_id=sample_id,
            request_sha256=digest(body), reserved_usd=reserved, status="RESERVED", wire=False)
        self.state["entries"].append(row)
        self.state["requests"] = sequence
        self.state["charged_upper_bound_usd"] += reserved
        self.state["active"] = sequence
        self.save()
        return sequence

    def wire(self, request):
        row = self.active()
        if (request.url.host != "generativelanguage.googleapis.com" or request.method != "POST"
                or not request.url.path.endswith(":generateContent") or row["wire"]
                or self.state["actual_wire_requests"] >= self.state["requests"]):
            raise RuntimeError("Unexpected/unreserved/repeated SDK request blocked")
        row["wire"] = True
        self.state["actual_wire_requests"] += 1
        self.save()

    def active(self):
        if self.state["active"] is None:
            raise RuntimeError("No durable request reservation")
        return self.state["entries"][self.state["active"] - 1]

    def settle(self, usage):
        row = self.active()
        if row["status"] != "RESERVED":
            raise ValueError("Usage may settle a reservation only once")
        prompt = usage.get("prompt_token_count")
        output = usage.get("candidates_token_count")
        thinking = usage.get("thoughts_token_count", 0)
        cached = usage.get("cached_content_token_count", 0)
        if (any(type(n) is not int or n < 0 for n in (prompt, output, thinking, cached))
                or cached > prompt or not row["wire"]):
            raise ValueError("Invalid usage/unreserved wire; keep reservation")
        cost = ((prompt - cached) * self.pricing["input_per_million"]
                + cached * self.pricing["cached_input_per_million"]
                + (output + thinking) * self.pricing["output_per_million"]) / 1_000_000
        if cost > row["reserved_usd"] or not math.isfinite(cost):
            raise ValueError("Usage exceeds conservative reservation")
        row.update(usage=usage, cost_usd_from_usage=cost, status="USAGE_KNOWN")
        self.state["charged_upper_bound_usd"] += cost - row["reserved_usd"]
        self.state["known_usage_cost_usd"] += cost
        self.save()

    def finish(self, prediction_path):
        row = self.active()
        if row["status"] != "USAGE_KNOWN":
            raise ValueError("Cannot finish an unmetered prediction")
        row.update(status="COMPLETE", prediction_sha256=file_hash(prediction_path))
        self.state["active"] = None
        self.save()

    def fail(self, error):
        self.state["failure"] = type(error).__name__
        if self.state["active"] is not None:
            self.active()["status"] = "STOPPED"
        self.save()


def sanitize_response(raw):
    return dict(model=raw.get("model_version"), usage_metadata=raw.get("usage_metadata"),
        candidates=[dict(finish_reason=candidate.get("finish_reason"), content=dict(parts=[
            {key: value for key, value in part.items() if key != "thought_signature"}
            for part in candidate.get("content", {}).get("parts", []) if not part.get("thought")]))
            for candidate in raw.get("candidates", [])])


def canonical_content(meeting):
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    # Parser anchors for AMI elapsed time, not observed dates or task due dates.
    return "\n".join(f'[{(epoch + timedelta(seconds=u["start"])).isoformat(timespec="milliseconds")}] '
        f'{u["speaker"]}: {u["text"]}' for u in meeting["utterances"])


def source_signature():
    paths = sorted((ROOT / "src" / "linkloom").rglob("*.py"))
    paths += [Path(__file__), ROOT / "scripts" / "eval_tasks.py"]
    return {str(path.relative_to(ROOT)).replace("\\", "/"): file_hash(path) for path in paths}


def execute_worker(out, variant, pricing):
    from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
    from linkloom.semantic_ingestion.extraction import (ProviderNeutralSemanticExtractor,
        ProviderRequestAuthorization, SemanticExtractionContext)
    from linkloom.semantic_ingestion.pipeline import SemanticIngestionPipeline
    from linkloom.semantic_ingestion.validation import CandidateValidator
    from linkloom.ui.product import DEFAULT_PRODUCT_RELATION_RESOLVER
    import linkloom

    out = Path(out).resolve()
    expected = (out / "baseline" / "src" if variant == "old" else ROOT / "src").resolve()
    if not Path(linkloom.__file__).resolve().is_relative_to(expected):
        raise ValueError("Worker imported wrong product source version")
    manifest, meetings, protocol = verify_inputs(out)
    run_signature = dict(protocol=protocol, pricing=pricing, source_files=source_signature())
    if not (out / "run-protocol.json").exists() or load_json(out / "run-protocol.json") != run_signature:
        raise ValueError("Worker requires the frozen explicit-live run protocol")
    protocol["current_source_sha256"] = digest(source_signature())
    budget = DurableBudget(out / "budget.json", protocol, manifest["max_requests"], pricing)
    completed = {(row["variant"], row["sample_id"]): row for row in budget.state["entries"]}
    for key, row in completed.items():
        path = out / "predictions" / key[0] / (key[1] + ".json")
        if not path.exists() or file_hash(path) != row["prediction_sha256"]:
            raise ValueError("Completed prediction changed; cannot resume")
    pending = [item for item in manifest["samples"] if (variant, item["sample_id"]) not in completed]
    if not pending:
        return dict(variant=variant, already_complete=True, requests=budget.state["requests"])
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    key = os.environ.get("GEMINI_API_KEY")
    if not key or not key.strip():
        raise ValueError("GEMINI_API_KEY unavailable; no request made")

    class Transport:
        def generate_content(self, *, model, contents, config):
            item = current["sample"]
            budget.reserve(variant, item["sample_id"], model,
                dict(contents=contents, config=config), config["max_output_tokens"])
            response = sdk.models.generate_content(model=model, contents=contents, config=config)
            raw = response if isinstance(response, dict) else response.model_dump(mode="json", exclude_none=True)
            write_new(out / "responses" / variant / (item["sample_id"] + ".json"), sanitize_response(raw))
            budget.settle(raw.get("usage_metadata", {}))
            return response

    current = {}
    sdk = build_gemini_sdk_client(key, http_options_kwargs=dict(timeout=120000,
        retry_options=dict(attempts=1), client_args=dict(follow_redirects=False,
        event_hooks=dict(request=[budget.wire]))))
    provider = GeminiProviderAdapter(Transport(), model_id=MODEL)
    guard = "es2002-task-field-sample/v1"
    extractor = ProviderNeutralSemanticExtractor(provider, provider_id="gemini", model_id=MODEL,
        request_guard=lambda request: ProviderRequestAuthorization.approve(request,
            guard_id=guard, reservation_id=f'sample-{budget.state["requests"] + 1}',
            estimated_cost_upper_bound_usd=MAX_USD - budget.state["charged_upper_bound_usd"]),
        request_guard_id=guard, max_attempts=1, max_output_tokens=MAX_OUTPUT)
    pipeline = SemanticIngestionPipeline(extractor, CandidateValidator())
    artifacts = {mid: RawArtifact(workspace_id="es2002-field-sample", artifact_id=mid,
        source_type="timestamped_text", content=canonical_content(meetings[mid]),
        ingestion_time=datetime(2000, 1, 1, tzinfo=UTC)) for mid in MEETINGS}
    parsed = {mid: parse_timestamped_text(artifact).segments for mid, artifact in artifacts.items()}
    valid_samples = {item["sample_id"] for item in manifest["samples"]}
    if any(sample_id not in valid_samples for _, sample_id in completed):
        raise ValueError("Saved prediction references a sample outside the frozen manifest")
    try:
        for item in pending:
            current["sample"] = item
            artifact = artifacts[item["meeting_id"]]
            segment = parsed[item["meeting_id"]][item["ordinal"]]
            if segment.ordinal != item["ordinal"] or segment.body_text != item["text"] or segment.speaker != item["speaker"]:
                raise ValueError("Canonical conversion changed sample")
            options = {}
            if variant == "new":
                from linkloom.semantic_ingestion.extraction import ProductTaskFieldContext
                options["product_task_fields"] = ProductTaskFieldContext(artifact)
            context = SemanticExtractionContext(expected_workspace_id=artifact.workspace_id,
                relation_resolver=DEFAULT_PRODUCT_RELATION_RESOLVER, **options)
            trace = pipeline.process_segment_with_trace(segment, artifact, context)
            if trace.extraction.failure_code is not None:
                write_new(out / "failures" / variant / (item["sample_id"] + ".json"), trace.extraction.to_dict())
                raise RuntimeError("Product extraction failed; not scored as empty")
            candidates = []
            for result in trace.candidate_results:
                candidate = result.candidate
                if candidate is not None and candidate.task_observation is not None:
                    candidates.append(dict(prediction_id=f'{item["sample_id"]}:{len(candidates) + 1}',
                        observation=candidate.task_observation.to_dict(), outcome=result.outcome.value,
                        reason_codes=[reason.value for reason in result.reason_codes]))
            path = out / "predictions" / variant / (item["sample_id"] + ".json")
            write_new(path, dict(sample_id=item["sample_id"], variant=variant,
                extraction=trace.extraction.to_dict(), candidates=candidates,
                validation_results=[dict(outcome=r.outcome.value,
                    candidate_id=r.candidate.candidate_id if r.candidate else None,
                    reason_codes=[reason.value for reason in r.reason_codes]) for r in trace.candidate_results]))
            budget.finish(path)
            print(json.dumps(dict(event="sample_complete", variant=variant, sample_id=item["sample_id"],
                requests=budget.state["requests"], candidates=len(candidates),
                known_usage_cost_usd=budget.state["known_usage_cost_usd"])), flush=True)
    except Exception as error:
        budget.fail(error)
        raise
    finally:
        sdk.close()
    return dict(variant=variant, requests=budget.state["requests"],
        actual_wire_requests=budget.state["actual_wire_requests"],
        known_usage_cost_usd=budget.state["known_usage_cost_usd"])


def preview_worker(out, variant):
    """Capture the exact product request at the guard; provider cannot execute."""
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from linkloom.semantic_ingestion.timestamped_text import parse_timestamped_text
    from linkloom.semantic_ingestion.extraction import (ProviderNeutralSemanticExtractor,
        SemanticExtractionContext)
    from linkloom.ui.product import DEFAULT_PRODUCT_RELATION_RESOLVER
    import linkloom
    out = Path(out).resolve()
    expected = (out / "baseline" / "src" if variant == "old" else ROOT / "src").resolve()
    if not Path(linkloom.__file__).resolve().is_relative_to(expected):
        raise ValueError("Preview imported wrong product source version")
    manifest, meetings, protocol = verify_inputs(out)
    captured = []
    def guard(request):
        captured.append(request.to_dict())
        raise PermissionError("Offline request preview: provider blocked")
    class NeverProvider:
        def complete(self, request):
            raise AssertionError("Offline preview must never call a provider")
    extractor = ProviderNeutralSemanticExtractor(NeverProvider(), provider_id="gemini", model_id=MODEL,
        request_guard=guard, request_guard_id="es2002-task-field-sample/v1",
        max_attempts=1, max_output_tokens=MAX_OUTPUT)
    artifacts = {mid: RawArtifact(workspace_id="es2002-field-sample", artifact_id=mid,
        source_type="timestamped_text", content=canonical_content(meetings[mid]),
        ingestion_time=datetime(2000, 1, 1, tzinfo=UTC)) for mid in MEETINGS}
    parsed = {mid: parse_timestamped_text(artifact).segments for mid, artifact in artifacts.items()}
    for item in manifest["samples"]:
        artifact = artifacts[item["meeting_id"]]
        segment = parsed[item["meeting_id"]][item["ordinal"]]
        if segment.body_text != item["text"] or segment.speaker != item["speaker"]:
            raise ValueError("Canonical preview conversion changed original sample")
        options = {}
        if variant == "new":
            from linkloom.semantic_ingestion.extraction import ProductTaskFieldContext
            options["product_task_fields"] = ProductTaskFieldContext(artifact)
        context = SemanticExtractionContext(artifact.workspace_id, DEFAULT_PRODUCT_RELATION_RESOLVER, **options)
        before = len(captured)
        extractor.extract(segment, context)
        if len(captured) != before + 1:
            raise ValueError("Expected exactly one denied request per sample")
    write_new(out / "request-preview" / (variant + ".json"),
        [dict(sample_id=item["sample_id"], variant=variant, request=request)
         for item, request in zip(manifest["samples"], captured)])
    prompt_bytes = sum(len(row["user_input"].encode("utf-8")) for row in captured)
    # An estimate only; actual input token counts and paid cost come from usage.
    value = dict(variant=variant, samples=len(captured), max_output_tokens=MAX_OUTPUT,
        prompt_utf8_bytes=prompt_bytes, mean_prompt_utf8_bytes=prompt_bytes / len(captured),
        rough_input_cost_utf8_div4=prompt_bytes / 4 * .75 / 1e6,
        minimum_output_example_30_tokens_cost=len(captured) * 30 * 3.75 / 1e6,
        network="not used; guard denied every request", protocol=protocol)
    write_new(out / (variant + "-preview.json"), value)
    return value


def preview(out):
    out = Path(out).resolve()
    verify_inputs(out)
    for variant in ("old", "new"):
        source = out / "baseline" / "src" if variant == "old" else ROOT / "src"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(source), str(ROOT)]),
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        command = [sys.executable, str(Path(__file__).resolve()), "_preview-worker", "--out", str(out),
            "--variant", variant]
        result = subprocess.run(command, cwd=ROOT, env=env)
        if result.returncode:
            raise RuntimeError("Offline request preview failed; no network requests authorized")
    return dict(old=load_json(out / "old-preview.json"), new=load_json(out / "new-preview.json"))


def run(out, variant, pricing, resume=False):
    out = Path(out).resolve()
    manifest, _, protocol = verify_inputs(out)
    signature = dict(protocol=protocol, pricing=pricing, source_files=source_signature())
    path = out / "run-protocol.json"
    if path.exists():
        if not resume or load_json(path) != signature:
            raise ValueError("Existing run needs --resume and identical sources/protocol/prices")
    else:
        if resume:
            raise ValueError("No existing run to resume")
        write_new(path, signature)
    for selected in (["old", "new"] if variant == "both" else [variant]):
        source = out / "baseline" / "src" if selected == "old" else ROOT / "src"
        env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(source), str(ROOT)]),
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        command = [sys.executable, str(Path(__file__).resolve()), "_worker", "--out", str(out),
            "--variant", selected, "--pricing", canonical(pricing), "--live"]
        result = subprocess.run(command, cwd=ROOT, env=env)
        if result.returncode:
            raise RuntimeError(f"{selected} worker stopped; budget retained, no automatic replay")
    return dict(status="PREDICTIONS_COMPLETE", samples=manifest["sample_count"],
        judgment="Human gold-task mapping required; no model judge has run")


def verify_completed_run(out, manifest, protocol=None):
    out = Path(out)
    if protocol is None:
        _, _, protocol = verify_inputs(out)
    saved_budget = load_json(out / "budget.json")
    run_protocol = load_json(out / "run-protocol.json")
    if (run_protocol["protocol"] != protocol or run_protocol["source_files"] != source_signature()):
        raise ValueError("Run source/protocol changed before report")
    ledger_protocol = dict(protocol, current_source_sha256=digest(source_signature()))
    budget = DurableBudget(out / "budget.json", ledger_protocol, manifest["max_requests"], run_protocol["pricing"]).state
    if budget != saved_budget:
        raise ValueError("Report ledger differs from saved budget")
    completed = {(row["variant"], row["sample_id"]): row for row in budget["entries"]}
    expected = {(variant, item["sample_id"]) for variant in ("old", "new") for item in manifest["samples"]}
    if set(completed) != expected:
        raise ValueError("Saved run does not cover exactly the frozen sample/version pairs")
    if budget["failure"] or budget["active"] is not None or budget["requests"] != manifest["max_requests"]:
        raise ValueError("Incomplete/failed run cannot be a complete comparison")
    for (variant, sample_id), entry in completed.items():
        path = out / "predictions" / variant / (sample_id + ".json")
        if file_hash(path) != entry["prediction_sha256"]:
            raise ValueError("Saved prediction was changed before report")
    return budget, completed


def judgment_template(out):
    out = Path(out)
    manifest, _, _ = verify_inputs(out)
    verify_completed_run(out, manifest)
    path = out / "gold-review.csv"
    fields = ["variant", "gold_id", "prediction_id", "extracted", "owner_correct", "deadline_correct", "reason"]
    with path.open("x", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for gold in manifest["gold_tasks"]:
            for variant in ("old", "new"):
                writer.writerow(dict(variant=variant, gold_id=gold["gold_id"], prediction_id="",
                    extracted="", owner_correct="", deadline_correct="", reason=""))
    return dict(path=str(path), network="not used")


def boolean(value):
    if value not in ("true", "false"):
        raise ValueError("Human judgments require explicit true/false")
    return value == "true"


def report(out, judgments):
    out = Path(out)
    manifest, _, protocol = verify_inputs(out)
    budget, completed = verify_completed_run(out, manifest, protocol)
    predictions = {}
    negative = {variant: dict(utterances_with_false_positives=0, false_positive_candidates=0, utterances=200)
                for variant in ("old", "new")}
    for variant in ("old", "new"):
        for item in manifest["samples"]:
            path = out / "predictions" / variant / (item["sample_id"] + ".json")
            if file_hash(path) != completed[(variant, item["sample_id"])]["prediction_sha256"]:
                raise ValueError("Saved prediction was changed before report")
            value = load_json(path)
            if value["variant"] != variant or value["sample_id"] != item["sample_id"]:
                raise ValueError("Prediction identity changed")
            predictions[(variant, item["sample_id"])] = value
            if item["sample_kind"] == "reference_negative":
                count = len(value["candidates"])
                negative[variant]["utterances_with_false_positives"] += bool(count)
                negative[variant]["false_positive_candidates"] += count
    with Path(judgments).open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    keys = {(variant, gold["gold_id"]) for gold in manifest["gold_tasks"] for variant in ("old", "new")}
    if len(rows) != len(keys) or {(row["variant"], row["gold_id"]) for row in rows} != keys:
        raise ValueError("Judgments must cover eleven tasks in both versions exactly once")
    used, by_gold = set(), {}
    gold_by_id = {row["gold_id"]: row for row in manifest["gold_tasks"]}
    for row in rows:
        variant, gid = row["variant"], row["gold_id"]
        extracted, owner, deadline = (boolean(row[name]) for name in ("extracted", "owner_correct", "deadline_correct"))
        possible = {prediction["prediction_id"] for sid in manifest["gold_to_samples"][gid]
                    for prediction in predictions[(variant, sid)]["candidates"]}
        if extracted:
            marker = (variant, row["prediction_id"])
            if row["prediction_id"] not in possible or marker in used:
                raise ValueError("Matched prediction absent, outside gold evidence or already used")
            used.add(marker)
        elif row["prediction_id"] or owner or deadline:
            raise ValueError("Unextracted task cannot have correct fields")
        if gold_by_id[gid]["deadline"] == "未说明" and deadline:
            raise ValueError("Unstated reference deadlines cannot score correct")
        by_gold.setdefault(gid, {})[variant] = dict(extracted=extracted, owner_correct=owner,
            deadline_correct=deadline, deadline_scored=gold_by_id[gid]["deadline"] != "未说明",
            prediction_id=row["prediction_id"] or None, reason=row["reason"])
    summary = dict(status="COMPLETE_DEVELOPMENT_SAMPLE", protocol=protocol, seed=SEED,
        generation=manifest["generation"],
        positive_gold_tasks=11, positive_utterances=manifest["positive_utterances"],
        negatives=negative, per_gold=by_gold, requests=budget["requests"],
        actual_wire_requests=budget["actual_wire_requests"],
        known_usage_cost_usd=budget["known_usage_cost_usd"],
        charged_upper_bound_usd=budget["charged_upper_bound_usd"], max_usd=MAX_USD,
        account_invoice_usd=None, pricing=budget["pricing"], human_judgments_sha256=file_hash(judgments),
        limitations=["ES2002 development sample only, not generalization evidence",
            "Reference negatives reviewed before execution; gold unchanged",
            "One message per request; cross-message context deliberately not supplied",
            "Owner/deadline scored separately; missing-year dates remain partial",
            "API usage priced at recorded rates, not account invoice; no model judge"])
    write_new(out / "comparison.json", summary)
    lines = ["# L5-2 ES2002 抽样对比", "", "开发集诊断，不代表保留集或泛化性能。", "",
        "| 金标准待办 | 旧抽出 | 新抽出 | 旧负责人正确 | 新负责人正确 | 旧截止正确 | 新截止正确 |",
        "| --- | --- | --- | --- | --- | --- | --- |"]
    def label(value):
        return "是" if value else "否"
    for gold in manifest["gold_tasks"]:
        old, new = by_gold[gold["gold_id"]]["old"], by_gold[gold["gold_id"]]["new"]
        fields = [gold["gold_id"], label(old["extracted"]), label(new["extracted"]),
            label(old["owner_correct"]), label(new["owner_correct"]),
            label(old["deadline_correct"]) if old["deadline_scored"] else "未注明，不计分",
            label(new["deadline_correct"]) if new["deadline_scored"] else "未注明，不计分"]
        lines.append("| " + " | ".join(fields) + " |")
    lines += ["", "| 200条负发言 | 误报发言数 | 误报候选数 |", "| --- | ---: | ---: |"]
    for variant in ("old", "new"):
        lines.append(f'| {variant} | {negative[variant]["utterances_with_false_positives"]} | {negative[variant]["false_positive_candidates"]} |')
    lines += ["", f'实际请求 {budget["requests"]}，实际wire {budget["actual_wire_requests"]}；API用量计价 USD {budget["known_usage_cost_usd"]:.9f}，上限 USD 1.00。不是账户实扣账单。',
        "", "固定种子抽样，调用前核对负样本，未修改gold、未读取其他系列、未调用模型裁判。", ""]
    (out / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    prep = sub.add_parser("prepare")
    prep.add_argument("--data", type=Path, required=True)
    prep.add_argument("--gold", type=Path, required=True)
    prep.add_argument("--out", type=Path, required=True)
    live = sub.add_parser("run")
    live.add_argument("--out", type=Path, required=True)
    live.add_argument("--variant", choices=("old", "new", "both"), default="both")
    live.add_argument("--live", action="store_true")
    live.add_argument("--resume", action="store_true")
    live.add_argument("--price-checked", required=True)
    live.add_argument("--input-per-million", type=float, default=.75)
    live.add_argument("--cached-input-per-million", type=float, default=.075)
    live.add_argument("--output-per-million", type=float, default=3.75)
    worker = sub.add_parser("_worker", help=argparse.SUPPRESS)
    worker.add_argument("--out", type=Path, required=True)
    worker.add_argument("--variant", choices=("old", "new"), required=True)
    worker.add_argument("--pricing", required=True)
    worker.add_argument("--live", action="store_true")
    offline_preview = sub.add_parser("preview")
    offline_preview.add_argument("--out", type=Path, required=True)
    preview_child = sub.add_parser("_preview-worker", help=argparse.SUPPRESS)
    preview_child.add_argument("--out", type=Path, required=True)
    preview_child.add_argument("--variant", choices=("old", "new"), required=True)
    template = sub.add_parser("judgment-template")
    template.add_argument("--out", type=Path, required=True)
    reporting = sub.add_parser("report")
    reporting.add_argument("--out", type=Path, required=True)
    reporting.add_argument("--judgments", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command is None:
        value = dict(network="not used", command="prepare / review / run --live / judgment-template / report",
            meetings=list(MEETINGS), seed=SEED, max_usd=MAX_USD, baseline=BASELINE)
    elif args.command == "prepare":
        value = prepare(args.data, args.gold, args.out)
    elif args.command == "run":
        if not args.live:
            parser.error("No model calls made: --live required")
        pricing = dict(input_per_million=args.input_per_million,
            cached_input_per_million=args.cached_input_per_million,
            output_per_million=args.output_per_million, checked=args.price_checked,
            source="https://ai.google.dev/gemini-api/docs/pricing", basis="API usage, not account invoice")
        if (any(not math.isfinite(pricing[key]) or pricing[key] < 0 for key in
                ("input_per_million", "cached_input_per_million", "output_per_million"))
                or pricing["input_per_million"] <= 0 or pricing["output_per_million"] <= 0
                or pricing["cached_input_per_million"] > pricing["input_per_million"]):
            parser.error("Invalid conservative price rates")
        value = run(args.out, args.variant, pricing, resume=args.resume)
    elif args.command == "_worker":
        if not args.live:
            parser.error("No model calls made: --live required")
        value = execute_worker(args.out, args.variant, json.loads(args.pricing))
    elif args.command == "preview":
        value = preview(args.out)
    elif args.command == "_preview-worker":
        value = preview_worker(args.out, args.variant)
    elif args.command == "judgment-template":
        value = judgment_template(args.out)
    else:
        value = report(args.out, args.judgments)
    print(json.dumps(value, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
