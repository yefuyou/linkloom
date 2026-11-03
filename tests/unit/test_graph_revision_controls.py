"""Reuse controls only when legacy code/options and raw public executions match."""
from pathlib import Path
import ast,hashlib,json,zipfile
import pytest
from run_graph_profile_revision import verify_control_reuse,ALLOWED_GRAPH_CHANGES,GRAPH_ONLY_METHODS,without_graph_method


def given(tmp_path,*,legacy_change=False,gold=False):
    paths=sorted(ALLOWED_GRAPH_CHANGES|({'src/linkloom/runtime/task_plan.py'} if legacy_change else set()))
    freeze=tmp_path/'runtime.json';archive=tmp_path/'baseline.zip';deps={}
    with zipfile.ZipFile(archive,'x') as z:
        for relative in paths:
            raw=('old baseline '+relative).encode()
            if relative in GRAPH_ONLY_METHODS:
                tree=ast.parse((Path(__file__).resolve().parents[2]/relative).read_text(encoding='utf8'));owner,method=GRAPH_ONLY_METHODS[relative]
                for node in tree.body:
                    if isinstance(node,ast.ClassDef) and node.name==owner:
                        for item in node.body:
                            if isinstance(item,ast.FunctionDef) and item.name==method:item.body=[ast.Pass()]
                raw=ast.unparse(tree).encode()
            z.writestr(relative,raw)
            deps['code:'+relative]=dict(sha256=hashlib.sha256(raw).hexdigest())
    freeze.write_text(json.dumps(dict(dependencies=deps)),encoding='utf8')
    config=tmp_path/'config.json';config.write_text(json.dumps(dict(model_id='fixed',max_output_tokens=1024)),encoding='utf8')
    public=tmp_path/'public.json';rows=[dict(route='legacy_only' if i<159 else 'graph_only') for i in range(243)]
    if gold:rows[0]['score']={}
    public.write_text(json.dumps(dict(rows=rows)),encoding='utf8')
    return dict(model_id='fixed',max_output_tokens=1024,baseline_runtime_freeze=str(freeze),baseline_source_archive=str(archive),baseline_configuration=str(config),baseline_public_executions=str(public))


def test_reuses_all_controls_only_if_both_changed_files_are_graph_only(tmp_path):
    rows,changes=verify_control_reuse(given(tmp_path))
    assert len(rows)==159 and set(changes)==ALLOWED_GRAPH_CHANGES


def test_legacy_source_change_disables_control_reuse(tmp_path):
    with pytest.raises(ValueError,match='CODE_CHANGED'):verify_control_reuse(given(tmp_path,legacy_change=True))


def test_legacy_model_or_output_cap_change_disables_reuse(tmp_path):
    config=given(tmp_path);config['max_output_tokens']=2048
    with pytest.raises(ValueError,match='CONFIGURATION_CHANGED'):verify_control_reuse(config)


def test_control_loader_rejects_scored_data_before_inference(tmp_path):
    with pytest.raises(ValueError,match='GOLD_BLIND'):verify_control_reuse(given(tmp_path,gold=True))


def test_reuse_ast_guard_rejects_any_non_graph_method_change():
    path='src/linkloom/semantic_ingestion/task_relation_question_planner.py'
    left=b'class InvestigationPlanner:\n def plan_graph(self):pass\n def plan(self):return 1\n'
    right=left.replace(b'return 1',b'return 2')
    assert without_graph_method(left,path)!=without_graph_method(right,path)
    assert without_graph_method(left,path)==without_graph_method(left.replace(b'plan_graph(self):pass',b'plan_graph(self):return 99'),path)



def test_only_graph_input_reservation_can_change_while_reusing_legacy(tmp_path):
    config=given(tmp_path);path=Path(config['baseline_configuration'])
    old=json.loads(path.read_text());old['input_token_caps']={'legacy_only':8192,'graph_only':6144};path.write_text(json.dumps(old))
    config['input_token_caps']={'legacy_only':8192,'graph_only':8192}
    assert len(verify_control_reuse(config)[0])==159
    config['input_token_caps']['legacy_only']=4096
    with pytest.raises(ValueError,match='CONFIGURATION_CHANGED'):verify_control_reuse(config)



def test_rechecked_price_expiry_is_metadata_but_cannot_expand_without_bound(tmp_path):
    config=given(tmp_path);path=Path(config['baseline_configuration'])
    old=json.loads(path.read_text());old['pricing_valid_until_utc']='2026-11-03T00:30:00+00:00';path.write_text(json.dumps(old))
    config.update(pricing_valid_until_utc='2026-11-03T00:55:00+00:00',pricing_rechecked_at_utc='2026-11-02T23:56:45+00:00')
    assert len(verify_control_reuse(config)[0])==159
    config['pricing_valid_until_utc']='2026-11-03T04:00:00+00:00'
    with pytest.raises(ValueError,match='PRICE_WINDOW'):verify_control_reuse(config)
