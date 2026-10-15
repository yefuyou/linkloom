"""Observable Product partial completion after one rejected planned query."""
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from run_bounded_multihop import SourceOnlyController
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.runtime.team_decision_planner import plan_relation_investigation
from linkloom.tools.contracts import ToolCall
from linkloom.ui.investigation import ProductInvestigationService
from scripts.reliability_probe import seed


def run_partial(tmp_path, *, rejection='alternate', repeat=False, max_steps=12, forged=False):
    app, wid = seed(tmp_path, ['Lena Marr is the accountable owner of Cedar Lantern.'])
    controller = SourceOnlyController()
    saved = None
    last_search = None
    injected = 0

    def respond(request):
        nonlocal saved, last_search, injected
        if saved is not None:
            if repeat and injected == 1:
                injected += 1
                return rejected(request)
            return saved
        action = controller(request)
        if action.kind == 'tool_call' and action.tool_call.tool_id == 'search_notes':
            last_search = action.tool_call
        if action.kind == 'final':
            saved = action
            if forged:
                payload = json.loads(action.final_answer)
                payload['rationale'][0]['point'] = 'Lena Marr reports directly to Fabricated Person'
                saved = ModelAction.final(json.dumps(payload))
            injected += 1
            return rejected(request)
        return action

    def rejected(request):
        arguments = {**last_search.arguments, 'source_context': {**last_search.arguments['source_context']}}
        if rejection == 'alternate':
            arguments['source_context']['query_variant'] = 'alternate'
        elif rejection == 'identity':
            arguments['source_context']['vault_root_fingerprint'] = 'root_foreign'
        elif rejection == 'missing_identity':
            arguments['source_context'].pop('index_sha256')
        return ModelAction.tool(ToolCall(f'rejected_{request.sequence}', 'search_notes', arguments))

    model = FakeModelAdapter([respond] * 12)
    service = ProductInvestigationService(app, model_factory=lambda _: model,
        external_provider=False, relation_max_steps=max_steps)
    query = 'Who is the direct leader of the owner of Cedar Lantern?'
    try:
        snapshot = service.start(query, wid, task_plan=plan_relation_investigation(query))
        deadline = time.monotonic() + 20
        while snapshot['kind'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
            snapshot = service.inspect(snapshot['run']['id'])
        return snapshot, model
    finally:
        app.close()


def test_one_rejected_unplanned_alternate_can_finish_reviewed_partial(tmp_path):
    snapshot, model = run_partial(tmp_path)
    assert snapshot['kind'] == 'insufficient', snapshot
    assert snapshot['reviewer']['decision'] == 'evidence_sufficient'
    assert snapshot['decision']['value'] is None
    assert 'Lena Marr' in json.dumps(snapshot['rationale'])
    assert snapshot['evidence']
    assert model.call_count <= 12


@pytest.mark.parametrize('rejection', ['identity', 'missing_identity'])
def test_identity_errors_cannot_be_recovered_as_partial(tmp_path, rejection):
    snapshot, _ = run_partial(tmp_path, rejection=rejection)
    assert snapshot['kind'] == 'error'
    assert snapshot['reviewer']['performed'] is False


def test_second_invalid_query_stops_instead_of_finishing(tmp_path):
    snapshot, _ = run_partial(tmp_path, repeat=True)
    assert snapshot['kind'] == 'error'
    assert snapshot['error']['code'] == 'MODEL_SCHEMA_CORRECTION_EXHAUSTED'


def test_budget_exhaustion_is_not_partial_completion(tmp_path):
    snapshot, _ = run_partial(tmp_path, max_steps=5)
    assert snapshot['kind'] == 'error'
    assert snapshot['error']['classification'] == 'budget_exhausted'


def test_recovery_does_not_admit_fabricated_partial_fact(tmp_path):
    snapshot, _ = run_partial(tmp_path, forged=True)
    assert snapshot['kind'] == 'error'
    assert snapshot['reviewer']['performed'] is False
