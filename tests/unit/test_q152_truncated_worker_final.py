"""Replay the actual truncated Worker Final without calling Gemini."""
import json
from pathlib import Path

import pytest

from linkloom.agents.team_decision import TeamDecisionResult
from linkloom.runtime.errors import ValidationError


def test_q152_truncated_worker_final_is_rejected():
    path = Path(__file__).parents[1] / 'fixtures/q152_truncated_worker_final.json'
    receipt = json.loads(path.read_text(encoding='utf-8'))
    assert receipt['finish_reason'] == 'length'
    assert receipt['usage']['output_tokens'] == 4092
    assert receipt['usage']['reasoning_tokens'] == 3928
    assert receipt['max_output_tokens'] == 4096
    with pytest.raises(ValidationError):
        TeamDecisionResult.from_grounded_final(receipt['final'], ())
