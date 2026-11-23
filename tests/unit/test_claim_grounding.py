"""Host-deterministic checks for model-proposed Typed Claims (Scenario D negatives).

The Host never re-reads source or answer wording. A claim is rejected only by a
check code can make exactly: read provenance, span integrity, workspace and
blocked entities, typed-claim structure, and conflicts with Host-confirmed facts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from linkloom.agents.claim_grounding import (
    ClaimSupportStatus,
    claim_conflicts,
    claim_status,
    unchained_claims,
)
from linkloom.agents.confirmed_facts import ConfirmedFactIndex, FrozenSource
from linkloom.agents.team_decision import TeamDecisionResult, _VerifiedClaim
from linkloom.evidence_identity import passage_evidence_id
from linkloom.runtime.errors import ValidationError

WORKSPACE = "ws-a"
OWNER_NOTE = "Meeting 3\nCobalt B is owned by Mira Chen from now on.\n"


def _span(path, content, line, *, workspace=WORKSPACE, version="v1"):
    """A real EvidenceSpan: recomputable ID, quote hash and physical content hash."""
    quote = content.splitlines()[line - 1]
    content_sha = FrozenSource(path, "src-" + path, version, content).physical_sha256
    quote_sha = hashlib.sha256(quote.encode("utf-8")).hexdigest()
    evidence_id = passage_evidence_id(
        workspace_id=workspace, resource_id=path, document_id=path, logical_path="/" + path,
        source_ref=path, content_hash=content_sha, line_start=line, line_end=line, quote_hash=quote_sha)
    return {"evidence_id": evidence_id, "relative_path": path, "content_sha256": content_sha,
            "quote": quote, "quote_sha256": quote_sha, "line_start": line, "line_end": line,
            "status": "verified", "workspace_id": workspace}


def _claim(span, subject="Cobalt B", predicate="owner_of_project", obj="Mira Chen", valid_on=None):
    return _VerifiedClaim(subject, predicate, obj, (span["evidence_id"],), span["quote"], valid_on,
                          "snapshot" if valid_on else None)


def _edge(edge_type, source, target, *, path="owner.md", blocker=False, valid_from=None, version="v1"):
    def node(label):
        return SimpleNamespace(label=label, scope=None, workspace_id=WORKSPACE)
    evidence = SimpleNamespace(workspace_id=WORKSPACE, role="blocker" if blocker else "source",
                               source_id="src-" + path, source_version_id=version, line_start=1, line_end=9)
    return SimpleNamespace(edge_type=edge_type, source=node(source), target=node(target), blocker=blocker,
                           valid_time_kind="snapshot" if valid_from else "atemporal", valid_from=valid_from,
                           valid_to=None, evidence=(evidence,))


def _index(*edges, content=OWNER_NOTE, path="owner.md", version="v1"):
    return ConfirmedFactIndex(WORKSPACE, edges, [FrozenSource(path, "src-" + path, version, content)])


def _status(claim, span, **options):
    options.setdefault("read_evidence_ids", {span["evidence_id"]})
    return claim_status(claim, {span["evidence_id"]: span}, workspace_id=WORKSPACE, **options)


def test_unconfirmed_meeting_text_is_answered_from_a_valid_evidence_span():
    # A phrasing no relation template knows: the model reads it, the Host checks the span.
    span = _span("owner.md", OWNER_NOTE, 2)
    assert _status(_claim(span), span) == (ClaimSupportStatus.SUPPORTED, None)
    assert _status(_claim(span), span, confirmed_facts=_index()) == (ClaimSupportStatus.SUPPORTED, None)


def test_search_only_evidence_is_not_a_read():
    span = _span("owner.md", OWNER_NOTE, 2)
    assert _status(_claim(span), span, read_evidence_ids=set())[1] == "EVIDENCE_NOT_READ"


@pytest.mark.parametrize("field,value,reason", [
    ("evidence_id", "ev_v2_" + "0" * 64, "EVIDENCE_UNKNOWN"),
    ("quote", "Cobalt B is owned by Kai Wu from now on.", "EVIDENCE_TAMPERED"),
    ("line_start", 1, "EVIDENCE_TAMPERED"),
    ("status", "unverified", "EVIDENCE_UNVERIFIED"),
])
def test_forged_or_tampered_evidence_span_is_rejected(field, value, reason):
    span = _span("owner.md", OWNER_NOTE, 2)
    forged = dict(span, **{field: value})
    by_ref = {span["evidence_id"]: forged}
    claim = _claim(span) if field != "quote" else _claim(forged, obj="Kai Wu")
    status, code = claim_status(claim, by_ref, workspace_id=WORKSPACE, read_evidence_ids={span["evidence_id"]})
    assert (status, code) == (ClaimSupportStatus.UNVERIFIABLE, reason)


def test_cross_workspace_citation_is_rejected():
    span = _span("owner.md", OWNER_NOTE, 2, workspace="ws-other")
    assert _status(_claim(span), span)[1] == "WORKSPACE_MISMATCH"


def test_stale_source_version_is_rejected():
    span = _span("owner.md", OWNER_NOTE, 2)
    newer = _index(content=OWNER_NOTE.replace("Mira Chen", "Kai Wu"), version="v2")
    assert _status(_claim(span), span, confirmed_facts=newer)[1] == "SOURCE_VERSION_STALE"


def test_object_must_be_copied_from_the_cited_quote_and_blocked_entities_never_pass():
    span = _span("owner.md", OWNER_NOTE, 2)
    assert _status(_claim(span, obj="Kai Wu"), span)[1] == "OBJECT_NOT_IN_QUOTE"
    assert _status(_claim(span), span, blocked_entities=["mira  CHEN"])[1] == "BLOCKED_ENTITY"
    assert _status(_claim(span, predicate="manages"), span)[1] == "PREDICATE_UNSUPPORTED"


@pytest.mark.parametrize("edge,conflict", [
    (_edge("owner_of", "Kai Wu", "Cobalt B"), True),                       # object conflict
    (_edge("owner_of", "Mira Chen", "Cobalt B", blocker=True), True),      # confirmed negation
    (_edge("owner_of", "Mira Chen", "Cobalt B"), False),                   # confirmed agreement
    (_edge("owner_of", "Kai Wu", "Cobalt B", valid_from="2020-01-01"), False),  # other time: history
])
def test_confirmed_fact_conflicts_are_deterministic(edge, conflict):
    span = _span("owner.md", OWNER_NOTE, 2)
    status, reason = _status(_claim(span), span, confirmed_facts=_index(edge))
    assert (status is ClaimSupportStatus.CONTRADICTED) is conflict
    assert reason == ("CONFIRMED_FACT_CONFLICT" if conflict else None)


def test_reversed_subject_conflicts_with_an_asymmetric_confirmed_fact():
    note = "Weekly sync\nMira Chen reports directly to Kai Wu.\n"
    span = _span("report.md", note, 2)
    reversed_claim = _claim(span, subject="Kai Wu", predicate="reports_to", obj="Mira Chen")
    index = _index(_edge("reports_to", "Mira Chen", "Kai Wu", path="report.md"), content=note, path="report.md")
    assert _status(reversed_claim, span, confirmed_facts=index)[1] == "CONFIRMED_FACT_CONFLICT"


def test_conflicting_claims_keep_both_sides_and_require_conflicting_uncertainty():
    first = _span("a.md", "A\nCobalt B is owned by Mira Chen.\n", 2)
    second = _span("b.md", "B\nCobalt B is owned by Kai Wu.\n", 2)
    evidence = {first["evidence_id"]: first, second["evidence_id"]: second}
    claims = [{"subject": "Cobalt B", "predicate": "owner_of_project", "object": name,
               "evidence_ids": [span["evidence_id"]], "quote": span["quote"], "valid_on": None,
               "temporal_kind": None} for name, span in (("Mira Chen", first), ("Kai Wu", second))]
    kwargs = dict(evidence_by_ref=evidence, expected_workspace_id=WORKSPACE, final_protocol="claim-proposal/v1",
                  require_read_evidence=True, read_evidence_ids=list(evidence))
    with pytest.raises(ValidationError) as error:
        TeamDecisionResult.from_grounded_final(json.dumps({"schema_version": "claim-proposal/v1", "claims": claims}),
                                              list(evidence), **kwargs)
    assert error.value.details["reason"] == "claim_conflict"
    result = TeamDecisionResult.from_grounded_final(json.dumps({
        "schema_version": "claim-proposal/v1", "claims": claims,
        "uncertainty": {"status": "conflicting_evidence", "unknown_fields": ["owner_of_project"]}}),
        list(evidence), **kwargs)
    assert result.decision["value"] is None
    assert result.claim_evidence_refs() == list(evidence)
    assert len(claim_conflicts(result.verified_claims.claims)) == 1


def test_wrong_upstream_relation_cannot_start_a_dependent_hop():
    owner = _span("owner.md", OWNER_NOTE, 2)
    report = _span("report.md", "Sync\nKai Wu reports directly to Ada Lin.\n", 2)
    chained = [_claim(owner), _claim(report, subject="Kai Wu", predicate="reports_to", obj="Ada Lin")]
    # Kai Wu is neither in the question nor the object of a verified upstream claim.
    assert unchained_claims(chained, "Who is the direct manager of Cobalt B's owner?") == (chained[1],)
    assert unchained_claims(chained[:1], "Who is the direct manager of Cobalt B's owner?") == ()


@pytest.mark.parametrize("mutation", ["bad_date", "bad_id", "extra_field", "bad_kind"])
def test_typed_claim_schema_id_and_date_are_validated(mutation):
    span = _span("owner.md", OWNER_NOTE, 2)
    claim = {"subject": "Cobalt B", "predicate": "owner_of_project", "object": "Mira Chen",
             "evidence_ids": [span["evidence_id"]], "quote": span["quote"],
             "valid_on": "2026-04-01", "temporal_kind": "snapshot"}
    if mutation == "bad_date":
        claim["valid_on"] = "2026-02-30"
    elif mutation == "bad_id":
        claim["evidence_ids"] = ["../owner.md"]
    elif mutation == "extra_field":
        claim["answer"] = "Mira Chen"
    else:
        claim["temporal_kind"] = "forever"
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(
            json.dumps({"schema_version": "claim-proposal/v1", "claims": [claim]}), [span["evidence_id"]],
            evidence_by_ref={span["evidence_id"]: span}, expected_workspace_id=WORKSPACE,
            final_protocol="claim-proposal/v1", require_read_evidence=True, read_evidence_ids=[span["evidence_id"]])


def test_write_wording_is_not_a_wordlist_denial_and_structured_write_signal_is_refused():
    from linkloom.ui.ask_admission import admission_restriction, router_safety_reason

    # Wording never denies; the model's structured WRITE_REQUEST operation and the read-only Tool ACL do.
    for query in ("Delete the note that says Mira Chen owns Cobalt B.", "请把 Cobalt B 的负责人改成 Kai Wu",
                  "Cobalt B 的 owner 是谁？"):
        assert admission_restriction(query) is None
    assert router_safety_reason(operation="WRITE_REQUEST") == "WRITE_NOT_ALLOWED"


@pytest.fixture
def verified_claim_source(tmp_path: Path):
    """Generate a real scanned source; no archived run or placeholder hash."""
    from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
    from linkloom.scanner import scan_vault

    vault = tmp_path / "vault"
    vault.mkdir()
    quote = "Mira Chen reports directly to Kai Wu."
    (vault / "reporting.md").write_text(quote + "\n", encoding="utf-8")
    adapter = RuntimeAgentAdapter(
        vault, scan_vault(vault, tmp_path / "index").index_path,
        retrieval_mode="bm25", decision_memory_enabled=False,
    )
    passages = adapter._search_notes("Mira Chen reports directly", {}, 5)
    assert len(passages) == 1
    evidence = passages[0]
    assert evidence["quote"] == quote
    assert evidence["quote_sha256"] == hashlib.sha256(quote.encode("utf-8")).hexdigest()
    assert evidence["content_sha256"] == hashlib.sha256(
        (vault / "reporting.md").read_bytes()
    ).hexdigest()
    assert adapter._read_verified_note_for_tool(evidence["evidence_id"]) == evidence
    return adapter, evidence


@pytest.mark.parametrize(
    "tampered_quote",
    [
        "Mira Chen reports directly to Kai Wuu.",
        "Mira Chen reports directly to Kai Wu",
        "Mira Chen reports directly to Kai Xu.",
    ],
    ids=["one_character_added", "one_character_removed", "one_character_replaced"],
)
def test_verified_claim_source_rejects_tampered_quote(verified_claim_source, tampered_quote):
    adapter, evidence = verified_claim_source
    ref = evidence["evidence_id"]
    # Keep the observed ID and hashes: changing even punctuation is tampering.
    adapter._evidence_by_id[ref] = {**evidence, "quote": tampered_quote}

    with pytest.raises(ValueError, match="no longer matches its source version"):
        adapter._read_verified_note_for_tool(ref)


def test_verified_claim_source_rejects_tampered_source_hash(verified_claim_source):
    adapter, evidence = verified_claim_source
    ref = evidence["evidence_id"]
    digest = evidence["content_sha256"]
    tampered_hash = ("0" if digest[0] != "0" else "1") + digest[1:]
    adapter._evidence_by_id[ref] = {**evidence, "content_sha256": tampered_hash}

    with pytest.raises(ValueError, match="no longer matches its source version"):
        adapter._read_verified_note_for_tool(ref)


@pytest.mark.parametrize("protocol", ["team-decision-result/v1", "claim-proposal/v1"])
def test_grounded_final_rejects_valid_but_unobserved_evidence(verified_claim_source, protocol):
    adapter, evidence = verified_claim_source
    ref = evidence["evidence_id"]
    if protocol == "claim-proposal/v1":
        payload = {
            "schema_version": protocol,
            "claims": [{
                "subject": "Mira Chen", "predicate": "reports_to", "object": "Kai Wu",
                "evidence_ids": [ref], "quote": evidence["quote"],
                "valid_on": None, "temporal_kind": None,
            }],
        }
    else:
        payload = {
            "schema_version": protocol,
            "decision": {"value": evidence["quote"], "status": "approved", "evidence_refs": [ref]},
            "rationale": [], "rejected_alternatives": [], "actions": [], "unresolved_items": [],
            "uncertainty": {
                "status": "none", "statement": None, "unknown_fields": [], "evidence_refs": [],
            },
            "evidence_refs": [ref],
        }
    text = json.dumps(payload)
    kwargs = dict(
        evidence_by_ref={ref: evidence}, expected_workspace_id=adapter.workspace_id,
        final_protocol=protocol, require_read_evidence=True, read_evidence_ids=[ref],
    )
    accepted = TeamDecisionResult.from_grounded_final(text, [ref], **kwargs)
    accepted.validate_claim_support({ref: evidence})
    assert accepted.decision["status"] == "approved"

    # All source facts remain valid; only the model's observed allowlist changes.
    with pytest.raises(ValidationError, match="not observed by the model"):
        TeamDecisionResult.from_grounded_final(text, [], **kwargs)
