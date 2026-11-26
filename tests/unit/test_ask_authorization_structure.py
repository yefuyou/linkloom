"""Authorization comes from structure (Tool ACL, workspace id, output protection), never from question wording."""
from types import SimpleNamespace

import pytest

from linkloom.runtime.errors import PermissionDeniedError, ValidationError
from linkloom.runtime.policy import ReadOnlyPolicy
from linkloom.tools.tool_policy import P4_TOOL_POLICY_VERSION, ToolCallPolicy, ToolPolicyEnforcer
from linkloom.ui.ask_admission import leaks_protected_text
from linkloom.ui.product import ProductApplicationError
from linkloom.ui.unified_ask import UnifiedAskService


def policy(**changes):
    fields = dict(policy_version=P4_TOOL_POLICY_VERSION, agent_id="agent", allowed_tool_ids=["validate_evidence"],
                  denied_tool_ids=[], max_calls=3, network="deny", vault_write="deny", gold_access="deny")
    return ToolCallPolicy(**(fields | changes))


@pytest.mark.parametrize("tool_id", ["write_file", "rename_file", "raw_filesystem", "read_gold"])
def test_write_tools_cannot_be_allowed_or_called_whatever_the_question_says(tool_id):
    with pytest.raises(ValidationError):
        policy(allowed_tool_ids=[tool_id])
    enforcer = ToolPolicyEnforcer(policy())
    with pytest.raises(ValidationError) as error:
        enforcer.check_call_eligibility(tool_id)
    assert error.value.details["reason"] == "core_denial"
    with pytest.raises(ValidationError):
        enforcer.authorize_call(tool_id)
    assert enforcer.call_count == 0


def test_tool_policy_cannot_enable_vault_write_or_network():
    for field in ("vault_write", "network", "gold_access"):
        with pytest.raises(ValidationError):
            policy(**{field: "allow"})


def test_read_only_runtime_policy_refuses_write_capability():
    with pytest.raises(ValidationError):
        ReadOnlyPolicy(write_capability=True)
    with pytest.raises(PermissionDeniedError):
        ReadOnlyPolicy().check_can_write()


def test_ask_rejects_another_workspace_id_without_looking_at_wording():
    service = UnifiedAskService(SimpleNamespace(_workspace_id=lambda: "current", repository=SimpleNamespace(
        database_path=":memory:")), lambda: None, graph_service=False)
    service._check_workspace("current")
    for workspace_id, code in (("other", "WORKSPACE_MISMATCH"), ("", "WORKSPACE_REQUIRED")):
        with pytest.raises(ProductApplicationError) as error:
            service._check_workspace(workspace_id)
        assert error.value.code == code and error.value.status == 409


def test_output_protection_withholds_configured_keys_and_system_prompt_text(monkeypatch):
    from linkloom.ui.security_gate import PROMPT
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test-key-0123456789")
    assert leaks_protected_text({"conclusions": [{"rationale": "the key is ds-test-key-0123456789"}]})
    for name in ("LINKLOOM_JEV_API_KEY", "TYPESAFE_API_KEY"):
        monkeypatch.setenv(name, "extra-key-" + name)
        assert leaks_protected_text({"conclusions": ["leak extra-key-" + name]})
    assert leaks_protected_text({"uncertainty": {"statement": "Quote: " + PROMPT[40:200]}})
    assert leaks_protected_text({"conclusions": ["  ".join(PROMPT[40:200].split())]})


def test_output_protection_does_not_block_normal_answers_or_the_users_own_question(monkeypatch):
    from linkloom.ui.security_gate import PROMPT
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert not leaks_protected_text({"query": PROMPT[:300], "conclusions": [{"value": "Wrenwell"}],
                                     "uncertainty": {"statement": "Owner is not stated in the cited passage."}})
    assert not leaks_protected_text({})


def test_ask_http_boundary_withholds_a_leaking_result_and_passes_a_clean_one(monkeypatch):
    from linkloom.ui.server import LinkLoomRequestHandler
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-key-0123456789")
    sent = []
    handler = SimpleNamespace(
        _send_error_payload=lambda status, code, message, *a: sent.append(("error", status, code)),
        _send_json=lambda body, status=200: sent.append(("json", status, body)))
    LinkLoomRequestHandler._send_ask_result(handler, {"conclusions": ["gm-test-key-0123456789"]}, 200)
    LinkLoomRequestHandler._send_ask_result(handler, {"conclusions": ["Wrenwell"]}, 200)
    assert sent[0] == ("error", 502, "SENSITIVE_OUTPUT_BLOCKED")
    assert sent[1] == ("json", 200, {"result": {"conclusions": ["Wrenwell"]}})
