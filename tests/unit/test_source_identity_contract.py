"""Source identity is bound by the Host; the model may only echo it correctly.

Contract change (host-bound source identity): a model used to have to copy every
configured identity field of the active Runtime source into search_notes
source_context, and omitting one was rejected. The executor is now bound to the
active source by the Host, so omitted identity fields are accepted and never
select a source. What stays enforced: a field that IS present must match the
active source exactly (value and JSON type), and unsupported extra fields are
rejected. The last test proves the binding at the RuntimeAgentAdapter layer.
"""
import shutil
from pathlib import Path

import pytest

from linkloom.agents.runtime_adapter import RuntimeAgentAdapter
from linkloom.scanner import scan_vault
from linkloom.tools.contracts import ToolCall
from linkloom.tools.runtime import create_retrieval_tool_runtime
from linkloom.tools.tool_policy import ToolCallPolicy, ToolPolicyEnforcer


IDENTITY = {'index_path': 'vault_index.json', 'index_sha256': 'a'*64,
            'index_schema_version': 1, 'vault_root_fingerprint': 'root_synthetic',
            'fixture_id': None, 'document_count': 1}


def invoke(source):
    invoked = []
    runtime = create_retrieval_tool_runtime(lambda *args: invoked.append(args) or [],
        lambda ref: {}, expected_source_context=IDENTITY)
    enforcer = ToolPolicyEnforcer(ToolCallPolicy(policy_version='p4-readonly-tools-v1',
        agent_id='retrieval_agent', allowed_tool_ids=['search_notes'], max_calls=1,
        denied_tool_ids=[], network='deny', vault_write='deny', gold_access='deny'))
    result = runtime.execute(ToolCall(call_id='synthetic_call', tool_id='search_notes',
        arguments={'query': 'synthetic', 'source_context': source, 'limit': 1}), enforcer)
    return result, invoked


# Old: omitting any identity field -> TOOL_INVALID_ARGUMENTS, executor not called.
# New: the Host binds identity, so an omitted field is fine and the executor runs.
@pytest.mark.parametrize('field', [key for key, value in IDENTITY.items() if value is not None])
def test_omitted_identity_field_is_accepted(field):
    result, invoked = invoke({k: v for k, v in IDENTITY.items() if k != field})
    assert result.status == 'ok'
    assert len(invoked) == 1


# Old: {} and {'scope_path': None} were rejected as incomplete identity.
# New: they are valid because identity is not supplied by the model.
@pytest.mark.parametrize('source', [{}, {'scope_path': None}])
def test_model_supplied_identity_is_not_required(source):
    result, invoked = invoke(source)
    assert result.status == 'ok'
    assert len(invoked) == 1


# Unchanged: a present field that conflicts with the active source is rejected
# before the executor runs, including JSON type confusion (True == 1 in Python).
@pytest.mark.parametrize('source', [
    {**IDENTITY, 'vault_root_fingerprint': 'root_other'},
    {**IDENTITY, 'fixture_id': 'foreign'},
    {**IDENTITY, 'index_sha256': 'b'*64},
    {**IDENTITY, 'document_count': True},
    {**IDENTITY, 'index_schema_version': True},
    {'vault_root_fingerprint': 'root_other'},
    {'document_count': True},
])
def test_conflicting_identity_field_is_rejected(source):
    result, invoked = invoke(source)
    assert result.status == 'error'
    assert result.error.code == 'TOOL_INVALID_ARGUMENTS'
    assert invoked == []


def test_unsupported_extra_field_is_rejected():
    result, invoked = invoke({**IDENTITY, 'unsupported_field': 'x'})
    assert result.status == 'error'
    assert invoked == []


def test_complete_identity_with_plan_metadata_still_executes():
    result, invoked = invoke({**IDENTITY, 'subgoal_id': 'owner', 'dependency_entities': {},
                              'query_variant': 'primary', 'scope_path': None})
    assert result.status == 'ok'
    assert len(invoked) == 1


def test_unset_fixture_label_is_optional_but_cannot_be_replaced():
    source = {key: value for key, value in IDENTITY.items() if value is not None}
    assert invoke(source)[0].status == 'ok'
    assert invoke({**source, 'fixture_id': 'foreign'})[0].status == 'error'


def _bound_adapter(tmp_path, name, extra_note=None):
    root = Path(__file__).resolve().parents[2]
    vault = tmp_path / name
    shutil.copytree(root / 'tests' / 'fixtures' / 'sample_vault', vault)
    if extra_note:
        (vault / 'foreign_only.md').write_text(extra_note, encoding='utf-8')
    scan = scan_vault(vault, tmp_path / f'{name}_scan')
    return RuntimeAgentAdapter(vault, scan.index_path, retrieval_mode='bm25')


def test_search_is_bound_to_the_adapter_source_not_to_model_identity(tmp_path):
    adapter_a = _bound_adapter(tmp_path, 'vault_a')
    adapter_b = _bound_adapter(tmp_path, 'vault_b', '# Zebraunique\n\nZebraunique launch owner is Mira.\n')
    # Control: the distinctive note exists only in source B.
    assert any(item['relative_path'] == 'foreign_only.md'
               for item in adapter_b._search_notes('Zebraunique', {}, 5))
    omitted = adapter_a._search_notes('扫描', {}, 3)
    assert omitted
    # The model names source B's identity; adapter A still searches only A.
    foreign = {'index_sha256': 'f'*64,
               'vault_root_fingerprint': adapter_b.reader.vault_root_fingerprint}
    claimed = adapter_a._search_notes('扫描', foreign, 3)
    assert [item['relative_path'] for item in claimed] == [item['relative_path'] for item in omitted]
    assert adapter_a._search_notes('Zebraunique', foreign, 5) == []
    assert {item['workspace_id'] for item in omitted + claimed} == {adapter_a.workspace_id}
