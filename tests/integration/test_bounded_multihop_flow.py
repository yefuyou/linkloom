import json
from dataclasses import replace
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
                next(tmp_path.glob('run_*/vault/n1.md')).write_text('The source was changed.', encoding='utf-8')
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


@pytest.mark.parametrize('attack', ['unread_claim', 'offchain_claim'])
def test_final_cannot_smuggle_a_source_supported_but_unread_or_offchain_fact(tmp_path, monkeypatch, attack):
    original = demo.SourceOnlyController

    class SmugglingController(original):
        extra = None

        def __call__(self, request):
            observation = request.observation
            if observation and observation.tool_id == 'search_notes' and observation.status == 'ok':
                self.extra = next((x for x in observation.value if x['relative_path'] == 'n3.md'), self.extra)
                if attack == 'unread_claim':
                    request = replace(request, observation=replace(observation, value=[
                        x for x in observation.value if x['relative_path'] != 'n3.md']))
            action = super().__call__(request)
            if action.kind != 'final':
                return action
            assert self.extra is not None
            final = json.loads(action.final_answer)
            unrelated = 'Yara Wells is the accountable owner of Other Bay'
            if attack == 'offchain_claim':
                final['rationale'] = [{'point': final['decision']['value'],
                                      'evidence_refs': list(final['decision']['evidence_refs'])}]
                final['decision']['value'] = unrelated
            else:
                final['decision']['value'] += '; ' + unrelated
            final['decision']['evidence_refs'].append(self.extra['evidence_id'])
            final['evidence_refs'].append(self.extra['evidence_id'])
            return ModelAction.final(json.dumps(final))

    monkeypatch.setattr(demo, 'SourceOnlyController', SmugglingController)
    public = public_case()
    public['documents'].append({'workspace_id': public['workspace_id'], 'path': 'n3.md',
        'content': '# Glacier Bay notes\nYara Wells is the accountable owner of Other Bay.'})
    actual = demo.run_case(public, tmp_path)
    assert actual['run_status'] == 'failed'
    assert actual['final'] is None


def test_reusing_output_directory_cannot_reuse_previous_workspace_sources(tmp_path):
    first = demo.run_case(public_case(), tmp_path)
    assert first['outcome'] == 'complete'
    second = public_case()
    second['workspace_id'] = 'another-workspace'
    second['documents'] = [{**second['documents'][0], 'workspace_id': 'another-workspace'}]
    actual = demo.run_case(second, tmp_path)
    assert actual['outcome'] == 'partial' and actual['values'] == ['mira cole']
