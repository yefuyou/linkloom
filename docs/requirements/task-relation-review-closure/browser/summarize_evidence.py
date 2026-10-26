"""Summarize captured browser responses; never generate acceptance results."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys


def main():
    root = Path.cwd()
    directory = root / "output/playwright/task-relation-review" / sys.argv[1]
    rows = [json.loads(line) for line in (directory / "http.jsonl").read_text(encoding="utf8").splitlines()]
    consoles = [json.loads(line) for line in (directory / "console.jsonl").read_text(encoding="utf8").splitlines()]
    result = json.loads((directory / "result.json").read_text(encoding="utf8"))
    assert result["status"] == "PASS"
    sources = {}
    operations = []
    for line, row in enumerate(rows, 1):
        body = row.get("body") or {}
        source = body.get("source")
        if isinstance(source, dict) and "content" in source:
            sources[(source["source_id"], source["source_version"])] = (line, source)
        if row["method"] == "POST" and row["url"].endswith("/api/product/task-review"):
            request = json.loads(row["request_body"])
            receipt = body.get("receipt") or {}
            operations.append(dict(http_line=line, action=request["action"], step_id=request["step_id"],
                run_id=request["run_id"], request_id=request["request_id"], http_status=row["status"],
                result_status=body.get("status"), receipt_revision=receipt.get("revision"),
                receipt_status=receipt.get("status"), idempotent=body.get("idempotent"),
                error=(body.get("error") or {}).get("code"), blockers=body.get("blockers")))

    sha = lambda text: hashlib.sha256(text.encode("utf8")).hexdigest()
    source_checks = []
    quote_checks = []
    for (source_id, version), (line, source) in sources.items():
        source_checks.append(dict(http_line=line, source_id=source_id, version=version,
            sha256=source["content_sha256"], content_sha_matches=sha(source["content"]) == source["content_sha256"]))
    def verify(value):
        if isinstance(value, list):
            for item in value:
                verify(item)
        elif isinstance(value, dict):
            if all(k in value for k in ("artifact_id", "artifact_version_id", "content_hash", "char_start", "char_end", "quote")):
                line, source = sources[(value["artifact_id"], value["artifact_version_id"])]
                quote_checks.append(dict(http_line=line, artifact_id=value["artifact_id"],
                    version=value["artifact_version_id"], char_start=value["char_start"], char_end=value["char_end"],
                    content_sha_matches=value["content_hash"] == source["content_sha256"],
                    exact_span_matches=source["content"][value["char_start"]:value["char_end"]] == value["quote"],
                    quote_sha_matches=sha(value["quote"]) == value.get("quote_sha256")))
            if isinstance(value.get("span"), dict) and "char_start" in value:
                span = value["span"]
                line, source = sources[(span["artifact_id"], span["artifact_version_id"])]
                quote_checks.append(dict(http_line=line, artifact_id=span["artifact_id"],
                    version=span["artifact_version_id"], role=value.get("role"),
                    exact_subspan_matches=source["content"][value["char_start"]:value["char_end"]] == value["quote"]))
            for item in value.values():
                verify(item)
    verify(result["committed"]["review"]["steps"])
    all_valid = all(item["content_sha_matches"] for item in source_checks) and all(
        all(value for key, value in item.items() if key.endswith("matches")) for item in quote_checks)
    assert all_valid and quote_checks
    summary = dict(status="PASS", http_responses=len(rows), local_review_operations=operations,
        non_success_http=[dict(http_line=n, method=r["method"], url=r["url"], status=r["status"],
            error=((r.get("body") or {}).get("error") or {}).get("code"))
            for n, r in enumerate(rows, 1) if r["status"] >= 400],
        console_entries=len(consoles), pageerrors=[r for r in consoles if r["type"] == "pageerror"],
        external_requests=(directory / "external.jsonl").read_text(encoding="utf8").splitlines(),
        source_versions_verified=source_checks, quote_checks=quote_checks, all_source_checks_pass=all_valid)
    (directory / "verification-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(dict(http=len(rows), operations=len(operations), source_versions=len(source_checks),
                         quote_checks=len(quote_checks), valid=all_valid, console=len(consoles),
                         pageerrors=len(summary["pageerrors"]), external=len(summary["external_requests"]))))


if __name__ == "__main__":
    main()
