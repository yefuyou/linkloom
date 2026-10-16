import pytest
from scripts.self_improvement.v2_run import compare
from scripts.self_improvement.v2_scorer import coverage, wilson
from scripts.self_improvement.v2_contracts import write


def report(passed, calls=10):
    return {"cases":[{"id":str(i),"passed":p,"calls":calls,"reads":1,
                         "context_total_bytes":100,"elapsed_ms":1} for i,p in enumerate(passed)],
            "metrics":{"correct":sum(passed),"calls":calls*len(passed),
                       "context_total_bytes":100*len(passed),"safety_violations":0}}


def test_aggregate_gain_cannot_hide_correct_case_regression():
    result = compare(report([True,False,False]),report([False,True,True],5))
    assert result["improved"] and not result["pass"] and result["regressions"] == ["0"]


def test_same_file_hit_does_not_prove_second_passage_read():
    spans = [{"path":"a.md","contains":"Alice owns X"},{"path":"a.md","contains":"Alice reports to Bob"}]
    assert coverage(spans,[{"relative_path":"a.md","quote":"Alice owns X"}]) == .5


def test_corrupt_or_existing_evidence_cannot_be_overwritten(tmp_path):
    path = tmp_path/"raw.json"
    path.write_text("{broken",encoding="utf-8")
    with pytest.raises(FileExistsError): write(path,{"passed":True})
    assert path.read_text(encoding="utf-8") == "{broken"


def test_wilson_small_sample_does_not_claim_certainty():
    assert wilson(6,6)[0] < .7


@pytest.mark.parametrize("cached",[False,True])
def test_final_exposure_closes_paid_generation(tmp_path,cached):
    from scripts.self_improvement.v2_live import proposals
    write(tmp_path/"FINAL_CLAIM.json",{"generation_closed":True})
    if cached: write(tmp_path/"GEMINI_PROPOSALS.json",{"candidates":[]})
    with pytest.raises(RuntimeError,match="permanently closed"):
        proposals(tmp_path,{},{},3)


def test_cached_proposal_cannot_use_old_identity_or_double_budget(tmp_path):
    from scripts.self_improvement.v2_live import proposals
    identity = {"experiment_id":"new","freeze_sha256":"f"*64}
    feedback = {**identity,"partition":"development","signals":[],"tested_configs":[]}
    write(tmp_path/"GEMINI_PROPOSALS.json",{"identity":{**identity,"experiment_id":"old"},"maximum":3,"candidates":[]})
    with pytest.raises(ValueError,match="identity or budget"):
        proposals(tmp_path,identity,feedback,2)


def test_known_dataset_defect_cannot_be_admitted():
    from scripts.self_improvement.v2_contracts import admission
    result = admission({"validation_pass":True,"final_pass":True,"improved":True,
        "core_complete":True,"live_qualified":True,"dataset_qualified":False})
    assert result["level"] == "REJECTED"


def test_authoring_rewrite_cannot_mutate_existing_freeze(tmp_path):
    from scripts.self_improvement.v2_dataset import build_dataset
    (tmp_path/"freeze.json").write_text("{}",encoding="utf-8")
    with pytest.raises(FileExistsError): build_dataset(tmp_path,rewrite=True)


def test_unknown_tool_rejection_without_ledger_entry_is_still_a_violation():
    from scripts.self_improvement.v2_scorer import score_case
    row = {"id":"x","outcome":"runtime_failure","values":[],"sources":[],"reason":"TOOL_UNKNOWN",
        "final":None,"calls":[],"reads":[],"search_observations":[],"error":{"code":"TOOL_UNKNOWN"},
        **{key:True for key in ("claim_supported","citation_valid","bindings_verified","workspace_valid","source_identity_valid","version_valid")},
        **{key:0 for key in ("verified_read_count","search_candidate_count","model_turns","context_bytes","context_total_bytes","elapsed_ms")}}
    expected = {"outcome":"complete","values":[],"supported":True,"required_sources":[],"reason":None,"classify":{"conflict":[]}}
    result = score_case(row,expected,{"category":"normal","family":"normal"})
    assert result["safety_violation"] and result["invalid_tool_arguments"] == 1


def test_final_claim_without_manifest_never_calls_generator(tmp_path,monkeypatch):
    from scripts.self_improvement.v2_run import main
    import scripts.self_improvement.generator as generator
    write(tmp_path/"FINAL_CLAIM.json",{})
    monkeypatch.setattr(generator,"propose",lambda _:pytest.fail("generation after Final"))
    with pytest.raises(FileNotFoundError,match="CANDIDATE_MANIFEST"):
        main(["--experiment",str(tmp_path),"--development-only"])


def test_live_dataset_mismatch_is_rejected_before_provider(tmp_path):
    from scripts.self_improvement.v2_live import verify_dataset
    write(tmp_path/"freeze.json",{"files":{}})
    with pytest.raises(ValueError,match="freeze mismatch"):
        verify_dataset(tmp_path,{"freeze_sha256":"f"*64})


def test_real_report_cannot_promote_advisory_transfer(tmp_path):
    from scripts.self_improvement.v2_live import verify_transfer_report
    from scripts.self_improvement.v2_contracts import sha
    identity = {"experiment_id":"x"}
    report = tmp_path/"REAL_TRANSFER_REPORT.json"
    write(report,{"identity":identity,"live_qualified":True})
    write(tmp_path/"REAL_TRANSFER_REPORT_SEAL.json",{"identity":identity,"sha256":sha(report)})
    with pytest.raises(ValueError,match="chain mismatch"):
        verify_transfer_report(tmp_path,identity)


def test_v1_partitions_repeat_the_same_six_structural_shapes():
    import json
    from scripts.self_improvement.run import ROOT
    shapes = []
    for split in ("dev","validation","final"):
        public = json.loads((ROOT/"tests/fixtures/self_improvement"/(split+"_public.json")).read_text(encoding="utf-8"))
        shapes.append([(row["id"].split("-",1)[1],len(row["documents"])) for row in public["cases"]])
    assert shapes[0] == shapes[1] == shapes[2]
    assert shapes[0] == [("normal",2),("ranking",7),("missing",1),("conflict",3),("foreign",2),("alias",3)]
