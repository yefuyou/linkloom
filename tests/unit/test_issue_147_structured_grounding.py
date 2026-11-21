"""Issue #147: grounding uses confirmed structured facts, not more sentence regexes."""
from __future__ import annotations

import hashlib
import json
import os

import pytest

import linkloom.agents.claim_grounding as grounding
from linkloom.agents.claim_grounding import (
    ClaimSupportStatus, TYPED_CLAIM_RELATIONS, assess_claim_text, evidence_supports_typed_claim,
    validate_typed_relation_registry,
)
from linkloom.agents.confirmed_facts import ConfirmedFactIndex, FrozenSource
from linkloom.agents.team_decision import TeamDecisionResult, claim_proposal_schema
from linkloom.runtime.errors import ValidationError
from linkloom.temporal_graph.models import Edge, Evidence, Node

WS = "ws-a"
VERSION = "ver-1"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Vault:
    """One frozen run vault: each source is a single line, as runtime reads it."""

    def __init__(self, **lines: str) -> None:
        self.sources = {name: FrozenSource(f"src_{name}.md", f"src_{name}", VERSION, text)
                        for name, text in lines.items()}
        self.edges: list[Edge] = []

    def read(self, name: str, *, workspace: str = WS, **changes) -> dict:
        source = self.sources[name]
        evidence = {
            "evidence_id": f"ev-{name}", "relative_path": source.relative_path,
            "content_sha256": source.physical_sha256, "quote": source.content,
            "quote_sha256": _sha(source.content), "line_start": 1, "line_end": 1,
            "status": "verified", "workspace_id": workspace,
        }
        evidence.update(changes)
        return evidence

    def confirm(self, name: str, edge_type: str, source: tuple[str, str], target: tuple[str, str], *,
                time: tuple[str | None, str | None, str] = (None, None, "atemporal"), scope: str = "",
                blocker: bool = False, version: str = VERSION, workspace: str = WS) -> None:
        quote = self.sources[name].content
        evidence = Evidence(workspace, f"evidence:{name}", f"src_{name}", version, "c" * 64,
                            quote, _sha(quote), 1, 1)
        self.edges.append(Edge(
            Node(workspace, source[0], source[1].casefold(), source[1], scope),
            Node(workspace, target[0], target[1].casefold(), target[1], scope),
            edge_type, time[0], time[1], time[2], f"decision-{len(self.edges)}", f"receipt-{len(self.edges)}",
            (evidence,), blocker,
        ))

    def index(self, workspace: str = WS) -> ConfirmedFactIndex:
        return ConfirmedFactIndex(workspace, self.edges, self.sources.values())


def _owner(evidence, index=None, *, subject="Bay", owner="Ada", valid_on=None, kind=None, observed=None):
    return evidence_supports_typed_claim(
        evidence, predicate="owner_of_project", subject=subject, object_value=owner,
        valid_on=valid_on, temporal_kind=kind, confirmed_facts=index,
        observed_evidence_by_ref=observed)


# Wordings that no legacy clause pattern recognizes.
PARAPHRASES = {
    "zh": "Bay 项目从本周起由 Ada 牵头负责。",
    "en": "Ada has taken charge of the Bay effort this sprint.",
    "en_passive": "The Bay project is now being run by Ada.",
}


@pytest.mark.parametrize("name", sorted(PARAPHRASES))
def test_same_fact_in_different_wordings_is_supported_only_through_a_confirmed_fact(name):
    vault = Vault(**{name: PARAPHRASES[name]})
    evidence = vault.read(name)
    # Before #147: the source sentence is outside the regex vocabulary.
    assert _owner(evidence) is False
    vault.confirm(name, "owner_of", ("person", "Ada"), ("project", "Bay"))
    assert _owner(evidence, vault.index()) is True


def test_swapped_entities_reversed_direction_and_wrong_entity_types_fail():
    vault = Vault(owner=PARAPHRASES["zh"], lead="Ada 这季度跟着 Bo 干。")
    vault.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"))
    vault.confirm("lead", "reports_to", ("person", "Ada"), ("person", "Bo"))
    index = vault.index()
    assert _owner(vault.read("owner"), index) is True
    assert _owner(vault.read("owner"), index, subject="Ada", owner="Bay") is False

    def reports(subject, leader):
        return evidence_supports_typed_claim(
            vault.read("lead"), predicate="reports_to", subject=subject, object_value=leader,
            valid_on=None, temporal_kind=None, confirmed_facts=index)
    assert reports("Ada", "Bo") is True
    assert reports("Bo", "Ada") is False
    # An edge whose endpoint kinds do not match the registry never becomes a fact.
    typed = Vault(owner=PARAPHRASES["zh"])
    typed.confirm("owner", "owner_of", ("person", "Ada"), ("plan", "Bay"))
    assert _owner(typed.read("owner"), typed.index()) is False


def test_negation_and_observed_counter_evidence_veto_support():
    vault = Vault(yes=PARAPHRASES["zh"], no="Ada 已经不再负责 Bay。", other="Bay 这边改由 Cy 牵头。")
    vault.confirm("yes", "owner_of", ("person", "Ada"), ("project", "Bay"))
    vault.confirm("no", "owner_of", ("person", "Ada"), ("project", "Bay"), blocker=True)
    vault.confirm("other", "owner_of", ("person", "Cy"), ("project", "Bay"))
    index = vault.index()
    yes = vault.read("yes")
    assert _owner(yes, index) is True
    assert _owner(yes, index, observed={"ev-yes": yes, "ev-no": vault.read("no")}) is False
    assert _owner(yes, index, observed={"ev-yes": yes, "ev-other": vault.read("other")}) is False
    # A confirmed negation in the cited read itself also blocks support.
    blocked = Vault(both=PARAPHRASES["zh"])
    blocked.confirm("both", "owner_of", ("person", "Ada"), ("project", "Bay"))
    blocked.confirm("both", "owner_of", ("person", "Ada"), ("project", "Bay"), blocker=True)
    assert _owner(blocked.read("both"), blocked.index()) is False


def test_time_and_project_scope_are_compared_structurally():
    vault = Vault(snap="截至 2026-04-01，Bay 由 Ada 牵头。", old="Bay 年初那阵子由 Ada 带。",
                  lead="Ada 在 Bay 项目里跟着 Bo。", far="Ada 在 Kite 项目里跟着 Cora。",
                  near="Ada 在 Bay 项目里跟着 Dee。")
    vault.confirm("snap", "owner_of", ("person", "Ada"), ("project", "Bay"),
                  time=("2026-04-01", None, "snapshot"))
    vault.confirm("old", "owner_of", ("person", "Ada"), ("project", "Bay"),
                  time=("2026-01-01", "2026-03-01", "interval"))
    vault.confirm("lead", "reports_to", ("person", "Ada"), ("person", "Bo"), scope="bay")
    vault.confirm("far", "reports_to", ("person", "Ada"), ("person", "Cora"), scope="kite")
    vault.confirm("near", "reports_to", ("person", "Ada"), ("person", "Dee"), scope="bay")
    index = vault.index()
    snap, old = vault.read("snap"), vault.read("old")
    assert _owner(snap, index, valid_on="2026-04-01", kind="snapshot") is True
    assert _owner(snap, index, valid_on="2026-05-01", kind="snapshot") is False
    assert _owner(snap, index) is False  # a dated snapshot is not a current fact
    assert _owner(old, index, valid_on="2026-02-10", kind="snapshot") is True
    assert _owner(old, index, valid_on="2026-03-05", kind="snapshot") is False
    assert _owner(old, index) is False  # a closed interval is history, not current

    lead = vault.read("lead")

    def reports(observed):
        return evidence_supports_typed_claim(
            lead, predicate="reports_to", subject="Ada", object_value="Bo", valid_on=None,
            temporal_kind=None, confirmed_facts=index, observed_evidence_by_ref=observed)
    assert reports({"ev-lead": lead, "ev-far": vault.read("far")}) is True  # other project
    assert reports({"ev-lead": lead, "ev-near": vault.read("near")}) is False  # same project


def test_binding_rejects_forged_reads_stale_versions_and_other_workspaces():
    vault = Vault(owner=PARAPHRASES["zh"])
    vault.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"))
    index = vault.index()
    assert _owner(vault.read("owner"), index) is True
    assert _owner(vault.read("owner", quote="Bay 由 Ada 负责。"), index) is False  # not in source
    assert _owner(vault.read("owner", content_sha256="0" * 64), index) is False
    assert _owner(vault.read("owner", status="candidate"), index) is False
    assert _owner(vault.read("owner", relative_path="elsewhere.md"), index) is False
    assert _owner(vault.read("owner", line_start=4, line_end=4), index) is False
    assert _owner(vault.read("owner", workspace="ws-b"), index) is False
    assert _owner(vault.read("owner"), vault.index("ws-b")) is False
    stale = Vault(owner=PARAPHRASES["zh"])
    stale.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"), version="ver-0")
    assert _owner(stale.read("owner"), stale.index()) is False


def _proposal(*claims):
    return json.dumps({"schema_version": "claim-proposal/v1", "claims": [
        {"subject": s, "predicate": p, "object": o, "evidence_ids": [ref], "quote": quote,
         "valid_on": None, "temporal_kind": None} for s, p, o, ref, quote in claims]})


def _typed_final(text, evidence, index, *, read=None):
    return TeamDecisionResult.from_grounded_final(
        text, list(evidence), evidence_by_ref=evidence, expected_workspace_id=WS,
        final_protocol="claim-proposal/v1", read_evidence_ids=list(evidence) if read is None else read,
        require_read_evidence=True, confirmed_facts=index)


def test_typed_final_keeps_read_receipt_and_observed_id_checks_and_rejects_a_wrong_first_hop():
    vault = Vault(alias="大家私下把 Bay 叫做 Nimbus。", owner=PARAPHRASES["zh"])
    vault.confirm("alias", "alias_of", ("alias", "Nimbus"), ("project", "Bay"))
    vault.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"))
    index = vault.index()
    evidence = {"ev-alias": vault.read("alias"), "ev-owner": vault.read("owner")}
    alias_quote, owner_quote = evidence["ev-alias"]["quote"], evidence["ev-owner"]["quote"]
    good = _proposal(("Nimbus", "alias_of_project", "Bay", "ev-alias", alias_quote),
                     ("Bay", "owner_of_project", "Ada", "ev-owner", owner_quote))
    result = _typed_final(good, evidence, index)
    assert result.decision["value"] == "Nimbus是Bay的别名；Bay的负责人是Ada"
    result.validate_claim_support(evidence, observed_evidence_by_ref=evidence, confirmed_facts=index)
    # Before #147 (no confirmed facts) the same proposal is rejected.
    with pytest.raises(ValidationError):
        _typed_final(good, evidence, None)
    # Review uses a fresh index: a revoked confirmation stops supporting the result.
    with pytest.raises(ValidationError):
        result.validate_claim_support(evidence, observed_evidence_by_ref=evidence, confirmed_facts=None)
    wrong_first_hop = _proposal(("Nimbus", "alias_of_project", "Cedar", "ev-alias", alias_quote),
                                ("Cedar", "owner_of_project", "Ada", "ev-owner", owner_quote))
    with pytest.raises(ValidationError):
        _typed_final(wrong_first_hop, evidence, index)
    with pytest.raises(ValidationError, match="successful read"):
        _typed_final(good, evidence, index, read=["ev-alias"])  # searched, never read
    forged = _proposal(("Bay", "owner_of_project", "Ada", "ev-forged", owner_quote))
    with pytest.raises(ValidationError, match="not observed"):
        _typed_final(forged, evidence, index)


def test_v1_text_claims_use_confirmed_source_facts_for_unpatterned_source_wording():
    vault = Vault(owner="截至 2026-04-01，Bay 项目由 Ada 牵头。")
    vault.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"),
                  time=("2026-04-01", None, "snapshot"))
    evidence = {"ev-owner": vault.read("owner")}
    claim = "As of 2026-04-01 Ada is the accountable owner of Bay."
    before = assess_claim_text(claim, ["ev-owner"], evidence)
    after = assess_claim_text(claim, ["ev-owner"], evidence, confirmed_facts=vault.index())
    assert before.status is ClaimSupportStatus.INSUFFICIENT
    assert after.status is ClaimSupportStatus.SUPPORTED
    assert after.claims[0].supporting_evidence_refs == ("ev-owner",)
    reversed_claim = "As of 2026-04-01 Bay is the accountable owner of Ada."
    assert assess_claim_text(reversed_claim, ["ev-owner"], evidence,
                             confirmed_facts=vault.index()).status is not ClaimSupportStatus.SUPPORTED
    wrong_day = "As of 2026-05-01 Ada is the accountable owner of Bay."
    assert assess_claim_text(wrong_day, ["ev-owner"], evidence,
                             confirmed_facts=vault.index()).status is not ClaimSupportStatus.SUPPORTED


def test_reviewed_fact_never_hides_parsed_counter_evidence():
    # The legacy parser reads Cy; a reviewer approved Ada for the same line.
    vault = Vault(owner="Cy is the accountable owner of Bay.")
    evidence = vault.read("owner")
    assert _owner(evidence, owner="Cy") is True
    vault.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"))
    assert _owner(evidence, vault.index(), owner="Cy") is False
    assert _owner(evidence, vault.index(), owner="Ada") is False  # conflict fails closed
    # A parsed negation in another observed read still vetoes a confirmed fact.
    other = Vault(yes=PARAPHRASES["zh"], no="Ada is not the accountable owner of Bay.")
    other.confirm("yes", "owner_of", ("person", "Ada"), ("project", "Bay"))
    yes = other.read("yes")
    assert _owner(yes, other.index(), observed={"ev-yes": yes, "ev-no": other.read("no")}) is False


def test_cited_line_must_be_exactly_the_reviewed_span():
    # The reviewer approved only the first sentence; the rest of the line was never reviewed.
    vault = Vault(line=PARAPHRASES["zh"] + " 不过 Ada 下周就不再负责了。")
    quote = vault.sources["line"].content
    evidence = Evidence(WS, "evidence:line", "src_line", VERSION, "c" * 64, PARAPHRASES["zh"],
                        _sha(PARAPHRASES["zh"]), 1, 1)
    vault.edges.append(Edge(Node(WS, "person", "ada", "Ada"), Node(WS, "project", "bay", "Bay"),
                            "owner_of", None, None, "atemporal", "d", "r", (evidence,)))
    assert _owner(vault.read("line", quote=quote), vault.index()) is False
    assert _owner(vault.read("line", quote=PARAPHRASES["zh"]), vault.index()) is False
    # A timestamped message header is not part of the reviewed body.
    message = Vault(msg="[2026-04-01T09:00:00Z] Alice: " + PARAPHRASES["zh"])
    body = Evidence(WS, "evidence:msg", "src_msg", VERSION, "c" * 64, PARAPHRASES["zh"],
                    _sha(PARAPHRASES["zh"]), 1, 1)
    message.edges.append(Edge(Node(WS, "person", "ada", "Ada"), Node(WS, "project", "bay", "Bay"),
                              "owner_of", None, None, "atemporal", "d", "r", (body,)))
    assert _owner(message.read("msg"), message.index()) is True


def test_future_interval_is_not_a_current_fact_and_scoped_chain_stays_scoped():
    vault = Vault(future="Ada 自 2099-01-01 起牵头 Bay 项目。")
    vault.confirm("future", "owner_of", ("person", "Ada"), ("project", "Bay"),
                  time=("2099-01-01", None, "interval"))
    assert _owner(vault.read("future"), vault.index()) is False
    assert _owner(vault.read("future"), vault.index(), valid_on="2099-02-01", kind="snapshot") is True
    chain = Vault(owner="截至 2026-04-01，Bay 由 Ada 牵头。", far="Ada 在 Kite 项目里跟着 Cora。")
    chain.confirm("owner", "owner_of", ("person", "Ada"), ("project", "Bay"),
                  time=("2026-04-01", None, "snapshot"), scope="bay")
    chain.confirm("far", "reports_to", ("person", "Ada"), ("person", "Cora"), scope="kite")
    evidence = {"ev-owner": chain.read("owner"), "ev-far": chain.read("far")}
    text = "截至 2026-04-01，Bay的负责人是Ada；Ada直接向Cora汇报"
    result = assess_claim_text(text, list(evidence), evidence, confirmed_facts=chain.index())
    assert result.status is not ClaimSupportStatus.SUPPORTED


def test_host_index_failure_becomes_a_structured_validation_error():
    from linkloom.agents.retrieval_agent import RetrievalAgent

    def broken():
        raise RuntimeError("repository unavailable")
    with pytest.raises(ValidationError, match="could not be loaded"):
        RetrievalAgent(confirmed_fact_index=broken)._confirmed_facts()


def test_new_relation_needs_one_registry_entry_and_no_sentence_pattern(monkeypatch):
    patterns = {name for name, value in vars(grounding).items() if hasattr(value, "fullmatch")}
    spec = {
        "subject_type": "plan", "object_type": "plan",
        "direction": "The subject plan was replaced by the object plan.",
        "source_relations": frozenset({"replaced_by_plan"}), "fact_relation": "replaced_by_plan",
        "graph_edge": ("supersedes", "target"), "single_valued": True,
        "clause": "{subject}已被{object}取代",
    }
    monkeypatch.setitem(TYPED_CLAIM_RELATIONS, "replaced_by_plan", spec)
    validate_typed_relation_registry()
    # The frozen legacy parser cannot be extended or reused for a new relation.
    with pytest.raises(ValueError, match="frozen legacy"):
        validate_typed_relation_registry({"replaced_by_plan": {**spec, "legacy_clause_parser": True}})
    with pytest.raises(ValueError, match="single_valued"):
        validate_typed_relation_registry({"replaced_by_plan": spec, "replaced_by_plan_many": {
            **spec, "single_valued": False}})
    with pytest.raises(ValueError, match="legacy parser relations"):
        validate_typed_relation_registry({"replaced_by_plan": {
            **spec, "source_relations": frozenset({"replaced_by_plan", "superseded_by"})}})

    vault = Vault(plan="Atlas 方案退场，后面统一按 Orion 方案走。", legacy="Orion replaced Atlas.")
    vault.confirm("plan", "supersedes", ("plan", "Orion"), ("plan", "Atlas"))
    evidence = {"ev-plan": vault.read("plan"), "ev-legacy": vault.read("legacy")}

    def replaced(ref, subject="Atlas", value="Orion", index=vault.index()):
        return evidence_supports_typed_claim(
            evidence[ref], predicate="replaced_by_plan", subject=subject, object_value=value,
            valid_on=None, temporal_kind=None, confirmed_facts=index)
    assert replaced("ev-plan") is True
    assert replaced("ev-plan", subject="Orion", value="Atlas") is False
    # A sentence the legacy regex does parse still grants nothing to the new relation.
    assert replaced("ev-legacy") is False
    assert replaced("ev-plan", index=None) is False
    schema = claim_proposal_schema()
    assert "replaced_by_plan" in schema["properties"]["claims"]["items"]["properties"]["predicate"]["enum"]
    text = _proposal(("Atlas", "replaced_by_plan", "Orion", "ev-plan", evidence["ev-plan"]["quote"]))
    result = _typed_final(text, {"ev-plan": evidence["ev-plan"]}, vault.index())
    assert result.decision["value"] == "Atlas已被Orion取代"
    # A multi-valued relation does not treat a second object as a contradiction.
    monkeypatch.setitem(TYPED_CLAIM_RELATIONS, "member_of_plan", {
        **spec, "subject_type": "person", "direction": "The subject person works on the object plan.",
        "source_relations": frozenset({"member_of_plan"}), "fact_relation": "member_of_plan",
        "graph_edge": ("owner_of", "source"), "single_valued": False, "clause": "{subject}参与{object}"})
    team = Vault(a="Ada 也在 Orion 方案里。", b="Ada 同时支援 Atlas 方案。")
    team.confirm("a", "owner_of", ("person", "Ada"), ("plan", "Orion"))
    team.confirm("b", "owner_of", ("person", "Ada"), ("plan", "Atlas"))
    a, b = team.read("a"), team.read("b")
    assert evidence_supports_typed_claim(
        a, predicate="member_of_plan", subject="Ada", object_value="Orion", valid_on=None,
        temporal_kind=None, confirmed_facts=team.index(), observed_evidence_by_ref={"ev-a": a, "ev-b": b}) is True
    assert patterns == {name for name, value in vars(grounding).items() if hasattr(value, "fullmatch")}


def test_physical_hash_matches_platform_newlines_written_by_product_investigation(tmp_path):
    content = "first line\nBay 项目由 Ada 牵头。\n"
    path = tmp_path / "src_x.md"
    path.write_text(content, encoding="utf-8")
    assert FrozenSource("src_x.md", "src_x", VERSION, content).physical_sha256 == hashlib.sha256(
        path.read_bytes()).hexdigest()
    assert os.linesep in path.read_bytes().decode("utf-8")
