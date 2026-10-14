"""One frozen synthetic Temporal Decision run through the product HTTP route."""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import sleep
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
from linkloom.ui.investigation import ProductInvestigationService
from tests.integration.test_product_application_flow import (
    _client, _start_product_server, _stop_product_server, _wait_source,
)
from tests.smoke.test_p14_cn2_typed_gemini38_smoke import (
    AppendOnlyProviderLedger, LedgeredGeminiClient, SmokeBlocked,
)


REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / "work" / "temporal_product_real_20261014_release_gate"
MODEL = "gemini-3.8-flash"
BATCH = "TD-HTTP-G38-20261014-RELEASE-GATE"
ORDER = ("T02", "T03", "T04", "T05")
TOTAL_REQUEST_CAP = 40
AUTHORIZATION_ENV = "LINKLOOM_TEMPORAL_RELEASE_GATE_AUTHORIZATION"
REQUIRED_AUTHORIZATION = "AUTHORIZE TD-HTTP-G38-20261014-RELEASE-GATE"
HISTORICAL_T01_LEDGER = REPO / "work" / "temporal_product_real_20261014" / "TD-HTTP-G38-20261014-T01" / "provider_ledger.jsonl"
HISTORICAL_T03_LEDGER = ROOT / f"{BATCH}-T03" / "provider_ledger.jsonl"


def _select_next_stage() -> None:
    """Select the separately authorized batch without changing old run identities."""
    global ROOT, BATCH, ORDER, TOTAL_REQUEST_CAP, AUTHORIZATION_ENV, REQUIRED_AUTHORIZATION
    ROOT = REPO / "work" / "temporal_product_real_20261014_next_stage"
    BATCH = "TD-HTTP-G38-20261014-NEXT-STAGE"
    ORDER = ("T04", "T05", "T03-NEW")
    TOTAL_REQUEST_CAP = 36
    AUTHORIZATION_ENV = "LINKLOOM_TEMPORAL_NEXT_STAGE_AUTHORIZATION"
    REQUIRED_AUTHORIZATION = "AUTHORIZE TD-HTTP-G38-20261014-NEXT-STAGE"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_score(run_dir: Path) -> dict:
    try:
        payload = (run_dir / "score.json").read_bytes()
        digest = (run_dir / "score.sha256").read_text(encoding="ascii").strip()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ValueError("score digest mismatch")
        score = json.loads(payload)
        if not isinstance(score, dict) or score.get("label") not in {
            "ANSWERED_CORRECT", "FAILED", "NOT_EVALUATED",
        }:
            raise ValueError("invalid score")
        return score
    except (OSError, ValueError, TypeError):
        raise SmokeBlocked("SCORE_INTEGRITY_FAILED_STOP") from None


def _fingerprint(paths: list[str]) -> str:
    lines = [f"{path} {_sha(REPO / path)}" for path in paths]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _freeze() -> tuple[dict, dict]:
    protocol = json.loads((ROOT / "PROTOCOL.json").read_text(encoding="utf-8"))
    if protocol.get("batch_id") != BATCH or protocol.get("model_id") != MODEL:
        raise SmokeBlocked("FROZEN_BATCH_IDENTITY_MISMATCH")
    if (protocol.get("max_runtime_runs") != len(ORDER)
            or protocol.get("max_generation_requests_total") != TOTAL_REQUEST_CAP
            or protocol.get("max_generation_requests_per_run") != 18
            or protocol.get("max_runtime_steps_per_run") != 18
            or protocol.get("case_order") != list(ORDER)
            or protocol.get("request_timeout_seconds") != 130
            or protocol.get("outer_process_tree_timeout_seconds") != 1900
            or protocol.get("sdk_automatic_retries") != 0
            or protocol.get("concurrency") != 1
            or protocol.get("final_protocol") != "claim-proposal/v1"):
        raise SmokeBlocked("FROZEN_EXECUTION_LIMIT_MISMATCH")
    if (protocol.get("authorization_environment_variable") != AUTHORIZATION_ENV
            or protocol.get("required_authorization") != REQUIRED_AUTHORIZATION):
        raise SmokeBlocked("FROZEN_AUTHORIZATION_MISMATCH")
    if _sha(HISTORICAL_T01_LEDGER) != protocol.get("historical_t01_ledger_sha256"):
        raise SmokeBlocked("HISTORICAL_T01_LEDGER_CHANGED")
    if protocol.get("historical_t03_ledger_sha256") is not None and (
        _sha(HISTORICAL_T03_LEDGER) != protocol["historical_t03_ledger_sha256"]
    ):
        raise SmokeBlocked("HISTORICAL_T03_LEDGER_CHANGED")
    for name in ("CASES.json", "GOLD.json"):
        if _sha(ROOT / name) != protocol["sha256"][name]:
            raise SmokeBlocked("FROZEN_INPUT_FINGERPRINT_MISMATCH")
    if _fingerprint(protocol["production_code_paths"]) != protocol["production_code_fingerprint_sha256"]:
        raise SmokeBlocked("IMPLEMENTATION_FINGERPRINT_MISMATCH")
    for name in protocol["execution_paths"]:
        if _sha(REPO / name) != protocol["sha256"][name]:
            raise SmokeBlocked("EXECUTION_FINGERPRINT_MISMATCH")
    cases = json.loads((ROOT / "CASES.json").read_text(encoding="utf-8"))
    all_cases = list(cases["cases"]) + [cases["holdout_case"]]
    if (cases.get("data_class") != "synthetic"
            or [case.get("id") for case in all_cases] != list(ORDER)
            or any(case.get("run_id") != f"{BATCH}-{case['id']}" for case in all_cases)):
        raise SmokeBlocked("FROZEN_CASE_IDENTITY_MISMATCH")
    return protocol, cases


class _BatchLock:
    def __enter__(self):
        self._file = open(ROOT / "batch.lock", "a+b")
        self._file.seek(0, os.SEEK_END)
        if self._file.tell() == 0:
            self._file.write(b"0")
            self._file.flush()
            os.fsync(self._file.fileno())
        self._file.seek(0)
        try:
            msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self._file.close()
            raise SmokeBlocked("CONCURRENT_BATCH_RUN_BLOCKED") from None
        return self

    def __exit__(self, *_):
        self._file.seek(0)
        msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
        self._file.close()


def _remaining(case_id: str, cases: dict) -> int:
    """Read all prior ledgers; any incomplete or uncertain record stops the batch."""
    position = ORDER.index(case_id)
    prior_requests = 0
    all_cases = list(cases["cases"]) + [cases["holdout_case"]]
    for index, case in enumerate(all_cases):
        run_dir = ROOT / case["run_id"]
        if index >= position:
            if run_dir.exists():
                raise SmokeBlocked("RUN_ID_ALREADY_USED_OR_CASE_OUT_OF_ORDER")
            continue
        if not run_dir.is_dir():
            raise SmokeBlocked("PRIOR_CASE_NOT_RUN")
        ledger_path = run_dir / "provider_ledger.jsonl"
        if not ledger_path.is_file():
            raise SmokeBlocked("PRIOR_LEDGER_MISSING_STOP")
        try:
            events = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
            reserved = [event for event in events if event["event"] == "request_reserved"]
            finished = [event for event in events if event["event"] == "request_finished"]
            end = [event for event in events if event["event"] == "run_finished"]
            if (not events or events[0]["event"] != "batch_reserved"
                    or events[0]["batch_id"] != BATCH or events[0]["run_id"] != case["run_id"]
                    or len(end) != 1 or events[-1]["event"] != "run_finished"
                    or len(reserved) != len(finished)
                    or {item["request_id"] for item in reserved} != {item["request_id"] for item in finished}
                    or any(item["request_kind"] != "generate_content" for item in reserved)
                    or any(item["status"] != "SUCCESS_USAGE_REPORTED" for item in finished)):
                raise SmokeBlocked("PRIOR_LEDGER_UNCERTAIN_OR_INCOMPLETE_STOP")
        except (OSError, ValueError, KeyError, TypeError):
            raise SmokeBlocked("PRIOR_LEDGER_DAMAGED_STOP") from None
        prior_requests += len(reserved)
        if not (run_dir / "observed.json").is_file() or not (run_dir / "score.json").is_file():
            raise SmokeBlocked("PRIOR_RESULT_INCOMPLETE_STOP")
        _verified_score(run_dir)
        try:
            archive_status = json.loads((run_dir / "archive_status.json").read_text(encoding="utf-8"))
            if archive_status.get("status") == "ARCHIVE_FAILED":
                archive_status = json.loads((run_dir / "archive_repair_status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            raise SmokeBlocked("PRIOR_ARCHIVE_INCOMPLETE_STOP") from None
        if archive_status.get("status") != "ARCHIVED":
            raise SmokeBlocked("PRIOR_ARCHIVE_INCOMPLETE_STOP")
        try:
            _verify_archive(run_dir / archive_status["archive"])
        except (KeyError, OSError, ValueError, TypeError, BadZipFile):
            raise SmokeBlocked("ARCHIVE_INTEGRITY_FAILED_STOP") from None
    if prior_requests >= TOTAL_REQUEST_CAP:
        raise SmokeBlocked("BATCH_REQUEST_CAP_REACHED")
    return min(18, TOTAL_REQUEST_CAP - prior_requests)


def _archive_runtime(source_root: Path, destination: Path) -> None:
    """Keep deep Runtime paths inside one archive; snapshot live SQLite first."""
    source_db = source_root / "runtime" / "checkpoints.sqlite"
    with TemporaryDirectory(prefix="linkloom-archive-") as temporary:
        backup = Path(temporary) / "checkpoints.sqlite"
        with closing(sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        pending = destination.with_suffix(".zip.partial")
        with ZipFile(pending, "x", ZIP_DEFLATED) as archive:
            members = {}
            for relative in ("runtime/results", "runtime/models", "traces", "vault", "scan"):
                folder = source_root / relative
                if not folder.is_dir():
                    if relative == "traces":
                        continue
                    raise FileNotFoundError(relative)
                for path in folder.rglob("*"):
                    if path.is_file():
                        member = (Path(relative) / path.relative_to(folder)).as_posix()
                        archive.write(path, member)
                        members[member] = _sha(path)
            for request in (source_root / "runtime").glob("request_*.json"):
                member = f"runtime/{request.name}"
                archive.write(request, member)
                members[member] = _sha(request)
            archive.write(backup, "runtime/checkpoints.sqlite")
            members["runtime/checkpoints.sqlite"] = _sha(backup)
            archive.writestr("manifest.json", json.dumps({"sha256": members}, sort_keys=True))
        _verify_archive(pending)
        final = destination.with_suffix(".zip")
        if final.exists():
            raise FileExistsError(final)
        pending.rename(final)


def _verify_archive(path: Path) -> bool:
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        members = manifest["sha256"]
        if not isinstance(members, dict) or not members:
            raise ValueError("empty archive manifest")
        if set(archive.namelist()) != set(members) | {"manifest.json"}:
            raise ValueError("archive member mismatch")
        for name, digest in members.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError("archive digest mismatch")
    return True


def _archive_after_terminal(source_root: Path, run_dir: Path, *, repair_root: Path | None = None) -> None:
    """Archive status is separate from the already durable Provider/Runtime terminal."""
    status_path = run_dir / "archive_status.json"
    try:
        _archive_runtime(source_root, run_dir / "runtime_artifacts")
    except Exception:
        status = {"status": "ARCHIVE_FAILED", "error_code": "ARTIFACT_ARCHIVE_FAILED"}
        if repair_root is not None:
            try:
                shutil.copytree(source_root, repair_root)
                source_db = source_root / "runtime" / "checkpoints.sqlite"
                with closing(sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)) as source:
                    with closing(sqlite3.connect(repair_root / "runtime" / "checkpoints.sqlite")) as target:
                        source.backup(target)
                status["repair_source"] = repair_root.name
            except Exception:
                status["repair_source_status"] = "UNAVAILABLE"
        status_path.write_text(
            json.dumps(status),
            encoding="utf-8",
        )
        raise SmokeBlocked("ARTIFACT_ARCHIVE_FAILED") from None
    status_path.write_text(json.dumps({"status": "ARCHIVED", "archive": "runtime_artifacts.zip"}), encoding="utf-8")


def _finalize_result(snapshot: dict, artifact: dict | None, gold: dict,
                     source_root: Path, run_dir: Path, *, repair_root: Path | None = None) -> dict:
    try:
        score = _score(snapshot, artifact, gold)
    except (KeyError, TypeError, ValueError):
        (run_dir / "score_status.json").write_text(
            json.dumps({"status": "SCORING_FAILED"}), encoding="utf-8",
        )
        raise SmokeBlocked("SCORING_FAILED") from None
    payload = json.dumps(score, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        path = run_dir / "score.json"
        with path.open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        with (run_dir / "score.sha256").open("x", encoding="ascii") as stream:
            stream.write(hashlib.sha256(payload).hexdigest())
            stream.flush()
            os.fsync(stream.fileno())
        _verified_score(run_dir)
    except (OSError, SmokeBlocked):
        try:
            (run_dir / "score_status.json").write_text(
                json.dumps({"status": "SCORE_PERSISTENCE_FAILED"}), encoding="utf-8",
            )
        except OSError:
            pass
        raise SmokeBlocked("SCORE_PERSISTENCE_FAILED") from None
    _archive_after_terminal(source_root, run_dir, repair_root=repair_root)
    return score


def _repair_archive(case_id: str) -> int:
    """Offline-only repair of an auxiliary archive from preserved Runtime files."""
    protocol = json.loads((ROOT / "PROTOCOL.json").read_text(encoding="utf-8"))
    if protocol.get("batch_id") != BATCH or protocol.get("model_id") != MODEL:
        raise SmokeBlocked("FROZEN_BATCH_IDENTITY_MISMATCH")
    for name in ("CASES.json", "GOLD.json"):
        if _sha(ROOT / name) != protocol["sha256"][name]:
            raise SmokeBlocked("FROZEN_INPUT_FINGERPRINT_MISMATCH")
    run_dir = ROOT / f"{BATCH}-{case_id}"
    _verified_score(run_dir)
    events = [json.loads(line) for line in (run_dir / "provider_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    if (not events or events[-1].get("event") != "run_finished"
            or any(event.get("status") == "UNCERTAIN" for event in events)):
        raise SmokeBlocked("LEDGER_UNCERTAIN_OR_INCOMPLETE_STOP")
    status = json.loads((run_dir / "archive_status.json").read_text(encoding="utf-8"))
    if status.get("status") != "ARCHIVE_FAILED" or status.get("repair_source") != f"repair-{case_id}":
        raise SmokeBlocked("ARCHIVE_REPAIR_SOURCE_UNAVAILABLE")
    if (run_dir / "archive_repair_status.json").exists():
        raise SmokeBlocked("ARCHIVE_REPAIR_ALREADY_ATTEMPTED")
    source = ROOT / f"repair-{case_id}"
    try:
        _archive_runtime(source, run_dir / "runtime_artifacts_repaired")
    except Exception:
        raise SmokeBlocked("ARCHIVE_REPAIR_FAILED") from None
    (run_dir / "archive_repair_status.json").write_text(
        json.dumps({"status": "ARCHIVED", "archive": "runtime_artifacts_repaired.zip"}), encoding="utf-8",
    )
    return 0


def _claim(source: dict, subject: str) -> dict:
    return {
        "claim_type": source["claim_type"], "subject": subject,
        "relation": "uses supplier", "value": source["value"],
        "temporal_status": "EXPLICIT" if source.get("valid_from") else (
            "NOT_STATED" if source["claim_type"] == "PROPOSAL" else "SOURCE_EVENT_TIME"
        ),
        "valid_from": source.get("valid_from"), "valid_to": None,
        "confidence": {"claim_type": .98, "entity": .95, "relation": .94,
                       "temporal": .91, "overall": .94},
    }


def _setup(run_dir: Path, setup: dict):
    claims = [_claim(source, setup["subject"]) for source in setup["sources"]]
    base, server, worker, app = _start_product_server(run_dir, claims)
    status, created = _client(base, "/api/workspaces", method="POST", data={"name": setup["workspace"]})
    if status != 201:
        raise SmokeBlocked("WORKSPACE_SETUP_FAILED")
    workspace_id = created["workspace"]["workspace_id"]
    for source in setup["sources"]:
        status, imported = _client(base, "/api/sources/import", method="POST", data={
            "name": source["name"], "content": source["content"],
        })
        if status != 202:
            raise SmokeBlocked("SOURCE_IMPORT_FAILED")
        item = _wait_source(base, imported["source"]["source_id"])
        if item["ingestion_status"] != "COMPLETE":
            raise SmokeBlocked("SOURCE_EXTRACTION_FAILED")
        if source["review"] == "approve":
            status, inbox = _client(base, "/api/review/candidates")
            candidate = next((item for item in inbox["candidates"]
                              if item["source_id"] == imported["source"]["source_id"]), None)
            if status != 200 or candidate is None:
                raise SmokeBlocked("REVIEW_CANDIDATE_MISSING")
            status, reviewed = _client(
                base, f"/api/review/candidates/{candidate['candidate_id']}/approve",
                method="POST", data={"reviewer_id": "synthetic-reviewer", "reason": "Synthetic approved decision"},
            )
            if status != 200 or reviewed.get("workflow_state") != "MATERIALIZED":
                raise SmokeBlocked("REVIEW_SETUP_FAILED")
    return base, server, worker, app, workspace_id


def _score(snapshot: dict, artifact: dict | None, expected: dict) -> dict:
    """Independent Gold is passed only after the Provider ledger is closed."""
    review = (artifact or {}).get("result", {}).get("review_decision")
    timeline = snapshot.get("temporal_decision")
    if snapshot.get("kind") not in {"success", "insufficient"} or not isinstance(timeline, dict):
        return {"label": "NOT_EVALUATED", "reason": "NO_ACCEPTED_TEMPORAL_FINAL", "reviewer": review}
    effective = timeline.get("effective") or {}
    transitions = timeline.get("transitions") or []
    proposals = timeline.get("proposals") or []
    checks = {
        "reviewer": review == "evidence_sufficient",
        "effective": effective.get("value") == expected["effective"],
        "review_receipt": bool((effective.get("authorization") or {}).get("receipt_id")),
        "source_read": snapshot.get("metrics", {}).get("read_count", 0) >= (2 if expected.get("replaces") else 1),
        "forbidden_current": effective.get("value") not in expected.get("forbidden_current", []),
    }
    if "replaces" in expected:
        checks["replaces"] = bool(transitions and transitions[0].get("from_value") == expected["replaces"])
    if "replacement_reason" in expected:
        checks["reason_unknown"] = bool(
            transitions and transitions[0].get("reason_status") == expected["replacement_reason"]
            and "replacement_reason" in (snapshot.get("uncertainty") or {}).get("unknown_fields", [])
        )
    if "candidate" in expected:
        checks["proposal_not_approved"] = any(
            item.get("value") == expected["candidate"]
            and item.get("status") == expected["candidate_status"] for item in proposals
        )
    return {"label": "ANSWERED_CORRECT" if all(checks.values()) else "FAILED",
            "checks": checks, "reviewer": review}


def _run(case_id: str) -> int:
    protocol, cases = _freeze()
    if os.environ.get(AUTHORIZATION_ENV) != REQUIRED_AUTHORIZATION:
        raise SmokeBlocked("EXACT_RECOVERY_BATCH_AUTHORIZATION_REQUIRED")
    all_cases = {case["id"]: case for case in list(cases["cases"]) + [cases["holdout_case"]]}
    case = all_cases[case_id]
    setup_key = case.get("setup_ref", "holdout_setup" if case_id == "T05" else "setup")
    setup = cases[setup_key]
    if not os.environ.get("GEMINI_API_KEY"):
        raise SmokeBlocked("GEMINI_CREDENTIAL_UNAVAILABLE")
    with _BatchLock():
        max_requests = _remaining(case_id, cases)
        run_dir = ROOT / case["run_id"]
        run_dir.mkdir(exist_ok=False)
        os.environ["LINKLOOM_RETRIEVAL_MODE"] = "bm25"
        base = server = worker = app = sdk = ledger = None
        try:
            base, server, worker, app, workspace_id = _setup(run_dir, setup)
            status, reviewed = _client(base, "/api/decisions/recover", method="POST", data={
                "subject": setup["subject"], "relation": setup["relation"], "as_of": case["as_of"],
            })
            if status != 200:
                raise SmokeBlocked("REVIEW_IDENTITY_SETUP_FAILED")
            identities = [{
                "decision_id": item["decision_id"],
                "receipt_id": item["authorization"]["receipt_id"],
                "authorization_type": item["authorization"]["authorization_type"],
                "source_versions": [evidence["source_version"] for evidence in item["evidence"]],
            } for item in reviewed["recovery"]["history"]]
            if not identities or any(item["authorization_type"] != "HUMAN_APPROVAL" for item in identities):
                raise SmokeBlocked("REVIEW_IDENTITY_SETUP_FAILED")
            (run_dir / "setup_identity.json").write_text(json.dumps({
                "workspace_id": workspace_id, "reviewed_decisions": identities,
            }, indent=2), encoding="utf-8")
            from google.genai import types as genai_types
            sdk = build_gemini_sdk_client(
                os.environ["GEMINI_API_KEY"],
                http_options_kwargs={"timeout": 130000,
                                     "retry_options": genai_types.HttpRetryOptions(attempts=1)},
            )
            ledger = AppendOnlyProviderLedger(
                run_dir / "provider_ledger.jsonl", batch_id=BATCH, run_id=case["run_id"],
                max_requests=max_requests, max_runtime_runs=1,
                max_input_tokens_per_request=None, case_id=case_id,
            )
            client = LedgeredGeminiClient(sdk, ledger)
            client.assert_developer_api()
            service = ProductInvestigationService(
                app, model_factory=lambda _query: GeminiProviderAdapter(client, model_id=MODEL),
                run_mode="GEMINI_38_FLASH_LEDGERED_HTTP", max_steps=18,
            )
            server.investigation_service = service
            ledger.runtime_started()
            status, started = _client(base, "/api/agent/decisions/ask", method="POST", data={
                "query": case["query"], "workspace_id": workspace_id,
                "subject": setup["subject"], "relation": setup["relation"], "as_of": case["as_of"],
            })
            if status != 202:
                ledger.finish_run(status="HTTP_START_REJECTED", usage_summary={})
                raise SmokeBlocked("HTTP_AGENT_START_REJECTED")
            http_run_id = started["run"]["id"]
            snapshot = started
            while snapshot["kind"] == "running":
                sleep(1)
                status, snapshot = _client(base, f"/api/agent/decisions/runs/{http_run_id}")
                if status != 200 and not (
                    snapshot.get("kind") == "error"
                    and status in {403, 500, 502, 503}
                ):
                    raise SmokeBlocked("HTTP_RUN_INSPECTION_FAILED")
            job = service._jobs[http_run_id]
            state = job.backend.engine.checkpointer.get_latest(snapshot["run"]["thread_id"])
            status_record = job.backend.engine.inspect(snapshot["run"]["thread_id"])
            artifact = job.backend._safe_result_artifact(status_record)
            observed = {
                "batch_id": BATCH, "run_id": case["run_id"], "http_run_id": http_run_id,
                "case_id": case_id, "requested_model": MODEL,
                "actual_models": ledger.model_versions, "snapshot": snapshot,
                "result_artifact": artifact,
                "model_turns": len(state.model_executions),
                "provider_requests": ledger.request_count,
                "usage": client.generation_usage_records,
                "estimated_cost_usd": client.estimated_cost_usd,
            }
            ledger.finish_run(status=snapshot["run"]["status"], usage_summary={
                "input_tokens": client.reported_input_tokens,
                "output_tokens": client.reported_output_tokens,
                "thinking_tokens": client.reported_thinking_tokens,
                "cached_tokens": client.reported_cached_tokens,
                "estimated_cost_usd": client.estimated_cost_usd,
            })
            (run_dir / "observed.json").write_text(json.dumps(observed, ensure_ascii=False, indent=2), encoding="utf-8")
            if ledger.halted:
                raise SmokeBlocked("LEDGER_TERMINAL_STOP")
            if _sha(ROOT / "GOLD.json") != protocol["sha256"]["GOLD.json"]:
                raise SmokeBlocked("FROZEN_GOLD_FINGERPRINT_MISMATCH")
            gold = json.loads((ROOT / "GOLD.json").read_text(encoding="utf-8"))["cases"][case_id]
            score = _finalize_result(snapshot, artifact, gold, Path(job.temporary_root.name), run_dir,
                                     repair_root=ROOT / f"repair-{case_id}")
            print(json.dumps({"run_id": case["run_id"], "http_run_id": http_run_id,
                              "kind": snapshot["kind"], "score": score["label"],
                              "requests": ledger.request_count}, ensure_ascii=False))
            return 0
        except BaseException:
            if ledger is not None and not ledger.run_finished and not ledger.in_flight and not ledger.halted:
                ledger.finish_run(status="RUNNER_FAILED_OR_STOPPED", usage_summary={})
            raise
        finally:
            if ledger is not None:
                ledger.close()
            if server is not None:
                _stop_product_server(server, worker, app)
            if sdk is not None:
                sdk.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case")
    parser.add_argument("--next-stage", action="store_true", help="independent 3-case authorization")
    parser.add_argument("--execute", action="store_true", help="requires separate real Provider authorization")
    parser.add_argument("--repair-archive", action="store_true", help="offline repair only; no Provider calls")
    args = parser.parse_args()
    try:
        if args.next_stage:
            _select_next_stage()
        if args.case not in ORDER:
            raise SmokeBlocked("CASE_NOT_IN_AUTHORIZED_BATCH")
        if args.repair_archive:
            if args.execute:
                raise SmokeBlocked("INCOMPATIBLE_RUN_MODES")
            return _repair_archive(args.case)
        if not args.execute:
            _, cases = _freeze()
            remaining = _remaining(args.case, cases)
            print(json.dumps({"status": "PREFLIGHT_ONLY", "batch_id": BATCH,
                              "case_id": args.case, "model_id": MODEL,
                              "remaining_generation_requests": remaining,
                              "provider_requests": 0}, ensure_ascii=False))
            return 0
        return _run(args.case)
    except SmokeBlocked as error:
        code = error.args[0] if error.args and isinstance(error.args[0], str) else "SMOKE_BLOCKED"
        print(json.dumps({"status": "BLOCKED", "error_code": code}, ensure_ascii=False))
        return 2
    except Exception as error:
        print(json.dumps({"status": "ERROR", "error_class": type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
