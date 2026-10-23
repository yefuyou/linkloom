import json
from zipfile import ZipFile

from scripts.analyze_sprint_trust_v2 import analyze_archive


def _write_zip(path, *, replay_proven=True):
    def put(zf, name, value):
        zf.writestr(name, json.dumps(value, ensure_ascii=False, separators=(",", ":")))

    with ZipFile(path, "w") as zf:
        # The fixture intentionally contains only the two failure boundaries.
        put(zf, "work/ce-b-20261023/authorization.json", {"script_sha256": "old-b"})
        put(zf, "work/ce-b-20261023/closed.json", {"requests": 1, "wire_requests": 1, "reserved_usd": .25, "failure": "MODEL_TRANSIENT_FAILURE", "actual_bill_usd": None})
        put(zf, "work/ce-b-20261023/provider/budget.json", {
            "requests_reserved": 1,
            "attempts": [{"sequence": 1, "cost_reserved_usd": .25, "outcome": "FAILED", "usage": {"total_tokens": None}}],
        })
        put(zf, "work/ce-b-20261023/wire/001-request.json", {"request": "redacted"})
        put(zf, "work/ce-b-20261023/wire/001-error.json", {"exception_type": "ConnectError", "status_code": None})
        put(zf, "work/ce-b-20261023/normalized/001-request.json", {"request": "redacted"})
        put(zf, "work/ce-b-20261023/normalized/001-response.json", {"usage": {"total_tokens": None}, "error": {"code": "MODEL_CANCELLED", "outcome": "known_failure"}})

        put(zf, "work/ce-d-20261023/authorization.json", {"script_sha256": "old-d"})
        put(zf, "work/ce-d-20261023/closed.json", {"requests": 2, "wire_requests": 1, "reserved_usd": 1.0, "failure": "PROVIDER_UNCERTAIN", "actual_bill_usd": None})
        put(zf, "work/ce-d-20261023/provider/budget.json", {
            "requests_reserved": 2,
            "attempts": [
                {"sequence": 1, "cost_reserved_usd": .5, "outcome": "SUCCEEDED", "provider_response_id": "r1", "usage": {"total_tokens": 10}},
                {"sequence": 2, "cost_reserved_usd": .5, "outcome": "UNCERTAIN", "usage": {"total_tokens": None}},
            ],
        })
        put(zf, "work/ce-d-20261023/wire/001-request.json", {"request": "redacted"})
        put(zf, "work/ce-d-20261023/wire/001-response.json", {"response": {"response_id": "r1", "usage_metadata": {"total_token_count": 10}}})
        put(zf, "work/ce-d-20261023/normalized/001-request.json", {"request": "redacted"})
        put(zf, "work/ce-d-20261023/normalized/001-response.json", {"usage": {"total_tokens": 10}})
        put(zf, "work/ce-d-20261023/normalized/002-request.json", {"request": "temperature=0"})
        put(zf, "work/ce-d-20261023/normalized/002-response.json", {"usage": {"total_tokens": None}, "error": {"code": "MODEL_UNKNOWN_FAILURE", "outcome": "unknown_provider_outcome", "details": {"reason": "PROVIDER_UNCERTAIN"}}})
        put(zf, "work/ce-d-20261023/prewire-resolution.json", {
            "sequence": 2,
            "resolution": "NOT_SENT_PREWIRE",
            "known_error": "MODEL_INVALID_REQUEST",
            "details": {"reason": "temperature_unsupported_for_gemini_3"},
            "wire_requests": 1,
            "missing_second_wire_proven_by_replay": replay_proven,
            "budget_reserved_retained_usd": 1,
            "actual_bill_for_first_probe": None,
        })


def test_historical_b_tls_and_d_prewire_are_classified_from_positive_evidence(tmp_path):
    archive = tmp_path / "fixture.zip"
    _write_zip(archive)
    result = analyze_archive(archive)

    assert result["counts"] == {
        "reservations": 3,
        "wire_attempts": 2,
        "SENT_SUCCEEDED": 1,
        "SENT_FAILED": 0,
        "NOT_SENT": 1,
        "OUTCOME_UNKNOWN": 1,
        "unresolved_provider_outcomes": 1,
    }
    by_key = {(row["phase"], row["root"], row["sequence"]): row for row in result["attempts"]}
    b = by_key[("B", "work/ce-b-20261023", 1)]
    d_wire = by_key[("D", "work/ce-d-20261023", 1)]
    d_prewire = by_key[("D", "work/ce-d-20261023", 2)]
    assert b["outcome"] == "OUTCOME_UNKNOWN"
    assert b["original_session"]["normalized_error"]["outcome"] == "known_failure"
    assert b["member_hashes"]["wire_error"]["sha256"]
    assert d_wire["outcome"] == "SENT_SUCCEEDED"
    assert d_prewire["outcome"] == "NOT_SENT"
    assert d_prewire["original_session"]["ledger_outcome"] == "UNCERTAIN"
    assert result["usage_and_billing"]["actual_billed_usd"] is None
    assert result["usage_and_billing"]["missing_usage_is_free"] is False


def test_source_gap_and_identity_fields_are_explicit_for_fixture(tmp_path):
    archive = tmp_path / "fixture.zip"
    _write_zip(archive)
    result = analyze_archive(archive)
    assert result["analyzer_identity"]["analyzer_source_sha256"]
    assert result["frozen_evidence"]["historical_commit"]
    assert len(result["source_gaps"]) == 7
    assert all(row["status"] == "NOT_VERIFIED" for row in result["source_gaps"])
    assert result["evaluation_class"] == "DEV_DIAGNOSTIC"
    assert result["independent_review"] == "INDEPENDENT_REVIEW_NOT_VERIFIED"


def test_missing_wire_alone_and_a_resolution_label_do_not_prove_not_sent(tmp_path):
    archive = tmp_path / 'no-proof.zip'
    _write_zip(archive, replay_proven=False)
    result = analyze_archive(archive)
    assert result['counts']['NOT_SENT'] == 0
    assert result['counts']['unresolved_provider_outcomes'] == 2
