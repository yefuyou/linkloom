import json
from pathlib import Path

import pytest

import run_bounded_multihop as demo
from linkloom.agents.model_adapter import ModelAction
from linkloom.tools.contracts import ToolCall


def public_case():
    case = json.loads(Path('tests/fixtures/bounded_multihop/dev.json').read_text(encoding='utf-8'))['cases'][0]
    return {key: value for key, value in case.items() if key not in {'id', 'tags', 'expected'}}


@pytest.mark.parametrize('attack', ['foreign_source', 'invented_dependency', 'changed_source'])
def test_runtime_rejects_an_attack_on_the_actual_hop_boundary(tmp_path, monkeypatch, attack):
    original = demo.SourceOnlyController

    class AdversarialController(original):
        def __call__(self, request):
            action = super().__call__(request)
            if action.kind != 'tool_call':
                return action
            call = action.tool_call
            arguments = dict(call.arguments)
            if call.tool_id == 'search_notes':
                context = dict(arguments['source_context'])
                if attack == 'foreign_source':
                    context['vault_root_fingerprint'] = 'root_other_workspace'
                if attack == 'invented_dependency' and context['dependency_entities']:
                    context['dependency_entities'] = {'owner': 'Invented Person'}
                arguments['source_context'] = context
            if attack == 'changed_source' and call.tool_id == 'read_verified_note':
                (tmp_path/'vault'/'n1.md').write_text('The source was changed.', encoding='utf-8')
            return ModelAction.tool(ToolCall(call_id=call.call_id, tool_id=call.tool_id, arguments=arguments))

    monkeypatch.setattr(demo, 'SourceOnlyController', AdversarialController)
    actual = demo.run_case(public_case(), tmp_path)
    assert actual['run_status'] == 'failed'
    assert actual['final'] is None and actual['values'] == []
    assert len(actual['calls']) <= 20
    if attack == 'foreign_source':
        # The immutable source identity is rejected before executing a tool.
        assert actual['calls'] == []
    else:
        assert any(row['status'] == 'failed' for row in actual['calls'])


def test_real_loop_records_parent_read_before_dependent_search(tmp_path):
    result = demo.run_case(public_case(), tmp_path)
    assert result['run_status'] == 'completed'
    assert result['values'] == ['mira cole', 'owen park']
    assert result['citation_valid'] and result['bindings_verified']
    leader_search = next(row for row in result['calls'] if row['tool'] == 'search_notes' and row['hop'] == 'leader')
    assert leader_search['dependencies'] == {'owner': 'Mira Cole'}
    assert any(row['tool'] == 'read_verified_note' and row['source'] == 'n1.md'
               and row['sequence'] < leader_search['sequence'] for row in result['calls'])
