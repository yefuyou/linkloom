"""A configured source identity must be complete before invoking any executor."""
import pytest

from linkloom.runtime.errors import ValidationError
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


@pytest.mark.parametrize('field', [key for key, value in IDENTITY.items() if value is not None])
def test_omitted_identity_field_fails_before_callback(field):
    result, invoked = invoke({k: v for k, v in IDENTITY.items() if k != field})
    assert result.status == 'error'
    assert result.error.code == 'TOOL_INVALID_ARGUMENTS'
    assert invoked == []


@pytest.mark.parametrize('source', [{}, {'scope_path': None},
    {**IDENTITY, 'document_count': True}, {**IDENTITY, 'index_schema_version': True},
    {**IDENTITY, 'vault_root_fingerprint': 'root_other'}])
def test_incomplete_or_foreign_identity_cannot_execute(source):
    result, invoked = invoke(source)
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
