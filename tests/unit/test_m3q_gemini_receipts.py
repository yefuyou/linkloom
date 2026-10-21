from linkloom.agents.providers.gemini_api import _function_response_payload
from linkloom.tools.contracts import ToolResult


def test_wire_result_carries_runtime_receipt_identity_for_structured_final():
    result = ToolResult(tool_id='inspect_task_context', status='ok', value={'workspace_id': 'synthetic'},
                        call_id='runtime-receipt-1')
    assert _function_response_payload(result)['call_id'] == 'runtime-receipt-1'
