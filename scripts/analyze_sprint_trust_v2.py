"""Offline transport-trust audit for the archived A-E credit sprint.

The archive is treated as evidence, not as an executable run directory.  The
analyzer only reads JSON and ZIP member bytes, then classifies each reserved
attempt from attributable wire/SDK evidence.  In particular, a missing
Provider response is kept unknown and a missing usage receipt is never treated
as a free call.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import platform
import subprocess
from typing import Any
from zipfile import ZipFile

from linkloom.evaluation.canary_trust import VERSION as TRUST_VERSION
from linkloom.evaluation.canary_trust import canonical, classify_attempt, save_new, sha


ANALYZER_VERSION = "canary-sprint-trust/2.0"
HISTORICAL_COMMIT = "f26191487f459d7af7016898422b9bbcfa1a5cee"
KNOWN_ARCHIVE_SHA256 = "61d2c582df96d1e0f399ac06128f4cfd8e231c2e9ce0bf5440cc9e707711d5ac"
FINAL_RUNNER_MEMBER = "work/cloud-credit-sprint-20261023/campaign_support.py"
FINAL_ADAPTER_MEMBER = "work/cloud-credit-sprint-20261023/independent_model.py"

# These are the bounded historical directories in the public package.  The
# zero-request C directory is retained in ``segments`` to make the NOT_RUN
# boundary visible, but it contributes no attempt.
RUNS = (
    ("A", "work/cloud-credit-sprint-20261023/batch-a", "campaign/budget.json"),
    ("A", "work/cloud-credit-sprint-20261023/a3", "campaign/budget.json"),
    ("B", "work/ce-b-20261023", "provider/budget.json"),
    ("B", "work/ce-b-20261023-tls-recheck", "provider/budget.json"),
    ("B", "work/ce-b-20261023-unattempted", "provider/budget.json"),
    ("C", "work/ce-c-20261023", "provider/budget.json"),
    ("C", "work/ce-c-20261023-resume", "provider/budget.json"),
    ("D", "work/ce-d-20261023", "provider/budget.json"),
    ("D", "work/ce-d-20261023-parameter-repair", "provider/budget.json"),
    ("E", "work/ce-e-20261023", "provider/budget.json"),
)

EARLY_RUNNER_GAPS = (
    ("ce-b-20261023", "a2b78510ca9088afc9356be3bd9a63b07d6b47f7a1d0605997078cc7659db352"),
    ("ce-b-20261023-tls-recheck", "4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4"),
    ("ce-b-20261023-unattempted", "4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4"),
    ("ce-c-20261023", "4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4"),
    ("ce-c-20261023-resume", "4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4"),
    ("ce-d-20261023", "4710b844ec8c94cbd16d363c57252375549162e9464b6d37a837b285f1b8e2a4"),
)
EARLY_ADAPTER_SHA256 = "9af19e3ba06813d2318bb11bba4c72024cf1ca470e600a7b5ca16bcc2bc8cde6"


def _json(zf: ZipFile, member: str) -> dict[str, Any]:
    value = json.loads(zf.read(member).decode("utf8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {member}")
    return value


def _member_ref(zf: ZipFile, names: set[str], member: str) -> dict[str, Any] | None:
    if member not in names:
        return None
    raw = zf.read(member)
    return {"member": member, "bytes": len(raw), "sha256": sha(raw)}


def _member_text(zf: ZipFile, names: set[str], member: str) -> str | None:
    if member not in names:
        return None
    return zf.read(member).decode("utf8", errors="replace")


def _line_numbers(zf: ZipFile, names: set[str], member: str, needle: str) -> list[int]:
    text = _member_text(zf, names, member)
    if text is None:
        return []
    return [index for index, line in enumerate(text.splitlines(), 1) if needle in line]


def _first_int(*values: Any) -> int | None:
    for value in values:
        if type(value) is int:
            return value
    return None


def _provider_payload(value: dict[str, Any]) -> dict[str, Any]:
    response = value.get("response")
    return response if isinstance(response, dict) else value


def _provider_receipt(value: dict[str, Any] | None) -> tuple[str | None, int | None]:
    if not value:
        return None, None
    response = _provider_payload(value)
    response_id = response.get("response_id")
    if not isinstance(response_id, str) or not response_id:
        response_id = None
    sdk_http = response.get("sdk_http_response")
    sdk_http = sdk_http if isinstance(sdk_http, dict) else {}
    status = _first_int(
        response.get("http_status"),
        response.get("status_code"),
        sdk_http.get("status_code"),
    )
    return response_id, status


def _script_sha(auth: dict[str, Any]) -> str | None:
    value = auth.get("script_sha256")
    if isinstance(value, str):
        return value
    plan = auth.get("plan")
    if isinstance(plan, dict) and isinstance(plan.get("script_sha256"), str):
        return plan["script_sha256"]
    return None


def _budget_attempt(budget: dict[str, Any], sequence: int) -> dict[str, Any]:
    for attempt in budget.get("attempts", []):
        if isinstance(attempt, dict) and attempt.get("sequence") == sequence:
            return attempt
    return {}


def _usage_present(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    return any(type(value.get(key)) is int for key in ("input_tokens", "output_tokens", "total_tokens"))


def _run_count(budget: dict[str, Any], closed: dict[str, Any] | None) -> int:
    count = budget.get("requests_reserved")
    if type(count) is int:
        return count
    if isinstance(closed, dict) and type(closed.get("requests")) is int:
        return closed["requests"]
    return 0


def _attempt_row(
    zf: ZipFile,
    names: set[str],
    phase: str,
    root: str,
    budget_member: str,
    budget: dict[str, Any],
    closed: dict[str, Any] | None,
    sequence: int,
) -> dict[str, Any]:
    prefix = f"{root}/"
    wire_request = f"{prefix}wire/{sequence:03d}-request.json"
    wire_response = f"{prefix}wire/{sequence:03d}-response.json"
    wire_error = f"{prefix}wire/{sequence:03d}-error.json"
    normalized_request = f"{prefix}normalized/{sequence:03d}-request.json"
    normalized_response = f"{prefix}normalized/{sequence:03d}-response.json"
    prewire_member = f"{prefix}prewire-resolution.json"

    wire_response_value = _json(zf, wire_response) if wire_response in names else None
    wire_error_value = _json(zf, wire_error) if wire_error in names else None
    normalized_response_value = _json(zf, normalized_response) if normalized_response in names else None
    prewire_value = _json(zf, prewire_member) if prewire_member in names else None
    provider_id, http_status = _provider_receipt(wire_response_value)
    budget_attempt = _budget_attempt(budget, sequence)

    prewire_applies = bool(
        isinstance(prewire_value, dict)
        and prewire_value.get("sequence") == sequence
        and str(prewire_value.get("resolution", "")).startswith("NOT_SENT")
        and prewire_value.get('missing_second_wire_proven_by_replay') is True
        and prewire_value.get('known_error') == 'MODEL_INVALID_REQUEST'
        and (prewire_value.get('details') or {}).get('reason') == 'temperature_unsupported_for_gemini_3'
        and wire_request not in names and wire_response not in names
    )
    if prewire_applies:
        evidence = {
            "provider_response_received": False,
            "http_status": None,
            "provider_response_id": None,
            "sdk_entered": False,
            "pre_transport_rejection": "sdk_argument_validation",
            "basis": "prewire-resolution plus absent second wire request",
        }
    elif wire_response_value is not None:
        evidence = {
            "provider_response_received": True,
            "http_status": http_status,
            "http_response_observed": http_status is not None,
            "provider_response_id": provider_id,
            "sdk_entered": True,
            "pre_transport_rejection": None,
            "basis": "wire response member",
        }
    elif wire_error_value is not None:
        # A local transport exception proves an SDK/wire attempt, but without an
        # attributable Provider response it does not prove remote receipt.
        evidence = {
            "provider_response_received": False,
            "http_status": _first_int(wire_error_value.get("http_status"), wire_error_value.get("status_code")),
            "provider_response_id": None,
            "sdk_entered": True,
            "pre_transport_rejection": None,
            "basis": "wire error member without Provider receipt",
        }
    else:
        evidence = {
            "provider_response_received": False,
            "http_status": None,
            "provider_response_id": None,
            "sdk_entered": None,
            "pre_transport_rejection": None,
            "basis": "no attributable terminal receipt",
        }

    normalized_error = None
    if isinstance(normalized_response_value, dict) and isinstance(normalized_response_value.get("error"), dict):
        error = normalized_response_value["error"]
        normalized_error = {
            key: error.get(key)
            for key in ("code", "category", "outcome", "retryable", "provider_request_id")
            if key in error
        }
        details = error.get("details")
        if isinstance(details, dict) and "reason" in details:
            normalized_error["reason"] = details["reason"]

    usage = budget_attempt.get("usage")
    if not isinstance(usage, dict) and isinstance(normalized_response_value, dict):
        usage = normalized_response_value.get("usage")
    reserved_usd = budget_attempt.get("cost_reserved_usd")
    if not isinstance(reserved_usd, (int, float)):
        reserved_usd = (closed or {}).get("reserved_usd")
        count = max(_run_count(budget, closed), 1)
        if isinstance(reserved_usd, (int, float)):
            reserved_usd = reserved_usd / count

    member_hashes = {
        "authorization": _member_ref(zf, names, f"{prefix}authorization.json"),
        "budget": _member_ref(zf, names, budget_member),
        "closed": _member_ref(zf, names, f"{prefix}closed.json"),
        "wire_request": _member_ref(zf, names, wire_request),
        "wire_response": _member_ref(zf, names, wire_response),
        "wire_error": _member_ref(zf, names, wire_error),
        "normalized_request": _member_ref(zf, names, normalized_request),
        "normalized_response": _member_ref(zf, names, normalized_response),
        "prewire_resolution": _member_ref(zf, names, prewire_member),
        "selection": _member_ref(zf, names, f"{prefix}selection.json"),
    }

    return {
        "phase": phase,
        "root": root,
        "sequence": sequence,
        "reserved_usd": reserved_usd,
        "outcome": classify_attempt(evidence),
        "evidence": evidence,
        "member_hashes": member_hashes,
        "original_session": {
            "closed_failure": (closed or {}).get("failure"),
            "closed_actual_bill_usd": (closed or {}).get("actual_bill_usd"),
            "ledger_outcome": budget_attempt.get("outcome"),
            "ledger_code": budget_attempt.get("code"),
            "normalized_error": normalized_error,
            "provider_request_id": budget_attempt.get("provider_request_id"),
            "provider_response_id": budget_attempt.get("provider_response_id") or provider_id,
            "usage_present": _usage_present(usage),
        },
    }


def _segments_and_attempts(zf: ZipFile, names: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    segments: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for phase, root, budget_relative in RUNS:
        budget_member = f"{root}/{budget_relative}"
        auth_member = f"{root}/authorization.json"
        if budget_member not in names and auth_member not in names:
            continue
        budget = _json(zf, budget_member) if budget_member in names else {}
        closed_member = f"{root}/closed.json"
        closed = _json(zf, closed_member) if closed_member in names else None
        count = _run_count(budget, closed)
        segment = {
            "phase": phase,
            "root": root,
            "authorization": _member_ref(zf, names, auth_member),
            "budget": _member_ref(zf, names, budget_member),
            "closed": _member_ref(zf, names, closed_member),
            "reserved_attempts": count,
            "wire_requests": int((closed or {}).get("wire_requests", len([n for n in names if n.startswith(f"{root}/wire/") and n.endswith("-request.json")]))) if closed else len([n for n in names if n.startswith(f"{root}/wire/") and n.endswith("-request.json")]),
            "session_failure": (closed or {}).get("failure"),
        }
        segments.append(segment)
        for sequence in range(1, count + 1):
            attempts.append(_attempt_row(zf, names, phase, root, budget_member, budget, closed, sequence))
    return segments, attempts


def _source_gaps(zf: ZipFile, names: set[str]) -> list[dict[str, Any]]:
    final_ref = _member_ref(zf, names, FINAL_RUNNER_MEMBER)
    final_hash = final_ref["sha256"] if final_ref else None
    gaps: list[dict[str, Any]] = []
    for root_name, declared in EARLY_RUNNER_GAPS:
        authorization = f"work/{root_name}/authorization.json"
        auth = _json(zf, authorization) if authorization in names else {}
        recorded = _script_sha(auth)
        gaps.append({
            "kind": "runner_support",
            "original_source_path": FINAL_RUNNER_MEMBER,
            "declaration_member": authorization,
            "declaration_script_sha256": recorded,
            "expected_declared_script_sha256": declared,
            "available_actual_member": FINAL_RUNNER_MEMBER if final_ref else None,
            "available_actual_sha256": final_hash,
            "status": "VERIFIED" if recorded and final_hash == recorded else "NOT_VERIFIED",
            "gap": "historical source bytes for the declared hash are absent; only the final archived support file is available",
        })

    selection_member = "work/ce-d-20261023/selection.json"
    selection = _json(zf, selection_member) if selection_member in names else {}
    recorded_adapter = selection.get("adapter_sha256")
    adapter_ref = _member_ref(zf, names, FINAL_ADAPTER_MEMBER)
    adapter_hash = adapter_ref["sha256"] if adapter_ref else None
    gaps.append({
        "kind": "d_initial_adapter",
        "declaration_member": selection_member,
        "declaration_adapter_sha256": recorded_adapter or EARLY_ADAPTER_SHA256,
        "expected_declared_adapter_sha256": EARLY_ADAPTER_SHA256,
        "available_actual_member": FINAL_ADAPTER_MEMBER if adapter_ref else None,
        "available_actual_sha256": adapter_hash,
        "status": "NOT_VERIFIED" if adapter_hash != EARLY_ADAPTER_SHA256 else "VERIFIED",
        "gap": "initial adapter source bytes do not match the archived final adapter",
    })
    return gaps


def _legacy_hardcoded_checks(zf: ZipFile, names: set[str]) -> list[dict[str, Any]]:
    members = (
        "work/cloud-credit-sprint-20261023/analyze_continuation.py",
        "work/cloud-credit-sprint-20261023/finalize_evidence.py",
        "work/cloud-credit-sprint-20261023/continuation-analysis-v1/aggregate.json",
        "work/cloud-credit-sprint-20261023/continuation-analysis-v2/aggregate.json",
    )
    rows = []
    for member in members:
        ref = _member_ref(zf, names, member)
        rows.append({
            "member": member,
            "member_hash": ref,
            "needle": "unresolved_provider_outcomes",
            "line_numbers": _line_numbers(zf, names, member, "unresolved_provider_outcomes"),
            "status": "VERIFIED" if ref and _line_numbers(zf, names, member, "unresolved_provider_outcomes") else "NOT_VERIFIED",
            "finding": "legacy value must not be reused; current unresolved count is derived from classified attempts",
        })
    return rows


def code_identity() -> dict[str, Any]:
    source = Path(__file__).read_bytes()
    return {
        "analyzer_version": ANALYZER_VERSION,
        "analyzer_source_sha256": sha(source),
        "trust_library_version": TRUST_VERSION,
        "frozen_historical_commit": HISTORICAL_COMMIT,
        "analyzer_git_sha": subprocess.check_output(['git', 'rev-parse', 'HEAD'],
            cwd=Path(__file__).resolve().parents[1], text=True).strip(),
        "config_sha256": sha(canonical({'runs': RUNS, 'archive_sha256': KNOWN_ARCHIVE_SHA256,
                                        'early_runner_gaps': EARLY_RUNNER_GAPS})),
        "python_version": platform.python_version(),
        "trust_library_sha256": sha((Path(__file__).resolve().parents[1] /
            'src/linkloom/evaluation/canary_trust.py').read_bytes()),
    }


def analyze_archive(archive: str | Path) -> dict[str, Any]:
    """Return a deterministic, read-only audit of one evidence ZIP."""
    archive_path = Path(archive)
    archive_sha = sha(archive_path.read_bytes())
    archive_status = "VERIFIED" if archive_sha == KNOWN_ARCHIVE_SHA256 else "NOT_VERIFIED"
    with ZipFile(archive_path) as zf:
        names = set(zf.namelist())
        segments, attempts = _segments_and_attempts(zf, names)
        counts = Counter(row["outcome"] for row in attempts)
        by_phase: dict[str, Counter[str]] = {}
        for row in attempts:
            by_phase.setdefault(row["phase"], Counter())[row["outcome"]] += 1
        final_manifest = _json(zf, "work/cloud-credit-sprint-20261023/final-manifest.json") if "work/cloud-credit-sprint-20261023/final-manifest.json" in names else {}
        usage_missing = sum(not row["original_session"]["usage_present"] for row in attempts)
        wire_attempts = sum(
            1
            for row in attempts
            if row["member_hashes"]["wire_request"]
            or row["member_hashes"]["wire_response"]
            or row["member_hashes"]["wire_error"]
        )
        return {
            "schema": "linkloom-canary-trust-audit/v2",
            "analyzer_identity": code_identity(),
            "analysis_identity": sha(canonical(dict(archive_sha256=archive_sha, identity=code_identity()))),
            "archive": {
                "path": str(archive_path),
                "sha256": archive_sha,
                "expected_sha256": KNOWN_ARCHIVE_SHA256,
                "sha256_status": archive_status,
                "member_count": len(names),
                "evidence_zip_is_read_only": True,
            },
            "frozen_evidence": {
                "historical_commit": HISTORICAL_COMMIT,
                "source_code_shas": final_manifest.get("code_shas", []),
                "final_manifest_member": _member_ref(zf, names, "work/cloud-credit-sprint-20261023/final-manifest.json"),
            },
            "source_gaps": _source_gaps(zf, names),
            "legacy_hardcoded_checks": _legacy_hardcoded_checks(zf, names),
            "segments": segments,
            "attempts": attempts,
            "counts": {
                "reservations": len(attempts),
                "wire_attempts": wire_attempts,
                "SENT_SUCCEEDED": counts["SENT_SUCCEEDED"],
                "SENT_FAILED": counts["SENT_FAILED"],
                "NOT_SENT": counts["NOT_SENT"],
                "OUTCOME_UNKNOWN": counts["OUTCOME_UNKNOWN"],
                "unresolved_provider_outcomes": counts["OUTCOME_UNKNOWN"],
            },
            "counts_by_phase": {
                phase: {key: counter[key] for key in ("SENT_SUCCEEDED", "SENT_FAILED", "NOT_SENT", "OUTCOME_UNKNOWN")}
                for phase, counter in sorted(by_phase.items())
            },
            "usage_and_billing": {
                "attempts_without_usage_receipt": usage_missing,
                "actual_billed_usd": None,
                "missing_usage_is_free": False,
                "reserved_usd": sum(row["reserved_usd"] for row in attempts if isinstance(row["reserved_usd"], (int, float))),
                "billing_status": "NOT_VERIFIED",
            },
            "evaluation_class": "DEV_DIAGNOSTIC",
            "independent_review": "INDEPENDENT_REVIEW_NOT_VERIFIED",
        }


def write_report(archive: str | Path, output: str | Path) -> dict[str, Any]:
    result = analyze_archive(archive)
    save_new(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="historical evidence.zip")
    parser.add_argument("--output", type=Path, required=True, help="new JSON output path")
    args = parser.parse_args()
    result = write_report(args.archive, args.output)
    print(json.dumps({"counts": result["counts"], "archive": result["archive"]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
