"""基于真实失败的脱敏复现 (#94). The messages, names, dates and IDs are fixture data.

From the repository root, run:
    python -m pytest -q tests/unit/test_issue_94_message_time_repro.py

This exercises the offline Final validator. It does not replay the original
trace or test how a provider responds to the updated prompt.
"""

import hashlib
import json

import pytest

from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.runtime.errors import ValidationError
from linkloom.scanner import scan_vault


MESSAGES = (
    (
        "ownership.md",
        "2031-06-04T09:00:00Z",
        "Riley Vale is the accountable owner of Lantern Plan.",
        "Lantern Plan",
        "owner_of_project",
        "Riley Vale",
    ),
    (
        "reporting.md",
        "2031-06-05T09:00:00Z",
        "Riley Vale reports directly to Morgan Reed.",
        "Riley Vale",
        "reports_to",
        "Morgan Reed",
    ),
)


@pytest.fixture
def saved_claims(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    for filename, timestamp, body, *_ in MESSAGES:
        (vault / filename).write_bytes(f"[{timestamp}] Speaker: {body}".encode("utf-8"))

    adapter = RuntimeAgentAdapter(
        vault,
        scan_vault(vault, tmp_path / "index").index_path,
        retrieval_mode="bm25",
        decision_memory_enabled=False,
    )
    passages = {
        item["relative_path"]: item
        for item in adapter._search_notes("Riley Vale Lantern Plan Morgan Reed", {}, 5)
    }
    evidence = {}
    claims = []
    for filename, timestamp, _, subject, predicate, object_value in MESSAGES:
        source = (vault / filename).read_bytes()
        ref = passages[filename]["evidence_id"]
        verified = adapter._read_verified_note_for_tool(ref)
        assert verified["quote"] == source.decode("utf-8")
        digest = hashlib.sha256(source).hexdigest()
        assert verified["content_sha256"] == verified["quote_sha256"] == digest
        evidence[ref] = verified
        claims.append({
            "subject": subject,
            "predicate": predicate,
            "object": object_value,
            "quote": verified["quote"],
            "evidence_ids": [ref],
            "valid_on": timestamp[:10],
            "temporal_kind": "snapshot",
        })

    def validate(proposed_claims):
        return TeamDecisionResult.from_grounded_final(
            json.dumps({"schema_version": "claim-proposal/v1", "claims": proposed_claims}),
            list(evidence),
            evidence_by_ref=evidence,
            expected_workspace_id=adapter.workspace_id,
            final_protocol="claim-proposal/v1",
            read_evidence_ids=list(evidence),
            require_read_evidence=True,
        )

    return claims, validate


@pytest.mark.parametrize(
    "bad_claims",
    [(0, 1), (0,), (1,)],
    ids=["both_misdated", "ownership_misdated", "reporting_misdated"],
)
def test_timestamp_in_claim_fails_final_validation(saved_claims, bad_claims):
    claims, validate = saved_claims
    corrected = [{**claim, "valid_on": None, "temporal_kind": None} for claim in claims]
    proposed = [
        claim if index in bad_claims else corrected[index]
        for index, claim in enumerate(claims)
    ]

    # The RetrievalAgent maps this ValidationError to TEAM_DECISION_CONTRACT_ERROR.
    with pytest.raises(
        ValidationError, match=r"ClaimProposal relation or temporal scope is not supported by its quote\."
    ):
        validate(proposed)

    accepted = validate(corrected)
    assert accepted.decision["status"] == "approved"
    assert accepted.decision["evidence_refs"] == [claim["evidence_ids"][0] for claim in claims]
