"""Observable Product partial completion after one rejected query (plan-free source investigation)."""
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'integration'))
from run_bounded_multihop import SourceOnlyController
from linkloom.agents.model_adapter import FakeModelAdapter, ModelAction
from linkloom.tools.contracts import ToolCall
from linkloom.ui.investigation import ProductInvestigationService
from scripts.reliability_probe import seed


def run_partial(tmp_path, *, rejection='argument', repeat=False, max_steps=12, forged=False, extra_hit=False):
    documents = ['Lena Marr is the accountable owner of Cedar Lantern.']
    if extra_hit:
        documents.append('Lena Marr appears on a roster. The roster has no reporting relationship.')
    app, wid = seed(tmp_path, documents)
    controller = SourceOnlyController()
    pending_search = None
    last_search = None
    injected = 0

    def respond(request):
        nonlocal pending_search, last_search, injected
        if pending_search is not None:
            if repeat and injected == 1:
                injected += 1
                return rejected(request)
            action = pending_search
            pending_search = None
            return action
        action = controller(request)
        if action.kind == 'tool_call' and action.tool_call.tool_id == 'search_notes':
            last_search = action.tool_call
            if rejection is not None and injected == 0:
                # Exercise the existing one-correction window before tools close.
                pending_search = action
                injected += 1
                return rejected(request)
        if action.kind == 'final':
            if forged:
                payload = json.loads(action.final_answer)
                payload['claims'].append(dict(payload['claims'][0], subject='Lena Marr',
                                              predicate='reports_to', object='Fabricated Person'))
                return ModelAction.final(json.dumps(payload))
        return action

    def rejected(request):
        arguments = {**last_search.arguments, 'source_context': {**last_search.arguments['source_context']}}
        if rejection == 'argument':
            arguments['limit'] = 0
        elif rejection == 'identity':
            arguments['source_context']['vault_root_fingerprint'] = 'root_foreign'
        elif rejection == 'document_count':
            arguments['source_context']['document_count'] += 1
        return ModelAction.tool(ToolCall(f'rejected_{request.sequence}', 'search_notes', arguments))

    model = FakeModelAdapter([respond] * 12)
    service = ProductInvestigationService(app, model_factory=lambda _: model,
        external_provider=False, relation_max_steps=max_steps)
    query = 'Who is the direct leader of the owner of Cedar Lantern?'
    try:
        snapshot = service.start(query, wid, source_relation=True)
        deadline = time.monotonic() + 20
        while snapshot['kind'] == 'running' and time.monotonic() < deadline:
            time.sleep(.01)
            snapshot = service.inspect(snapshot['run']['id'])
        return snapshot, model
    finally:
        app.close()


def test_one_rejected_query_argument_can_finish_reviewed_partial(tmp_path):
    snapshot, model = run_partial(tmp_path)
    assert snapshot['kind'] == 'insufficient', snapshot
    assert snapshot['reviewer']['decision'] == 'evidence_sufficient'
    assert snapshot['decision']['value'] is None
    assert 'Lena Marr' in json.dumps(snapshot['rationale'])
    assert snapshot['evidence']
    assert model.call_count <= 12


# An omitted source field is not a conflict: the retrieval executor is already bound to the
# active source (tools.runtime). A conflicting identity field is rejected.
@pytest.mark.parametrize('rejection', ['identity', 'document_count'])
def test_identity_errors_cannot_be_recovered_as_partial(tmp_path, rejection):
    snapshot, _ = run_partial(tmp_path, rejection=rejection)
    assert snapshot['kind'] == 'error'
    assert snapshot['reviewer']['performed'] is False


def test_second_invalid_query_stops_instead_of_finishing(tmp_path):
    snapshot, _ = run_partial(tmp_path, repeat=True)
    assert snapshot['kind'] == 'error'
    assert snapshot['error']['code'] == 'MODEL_SCHEMA_CORRECTION_EXHAUSTED'


def test_budget_exhaustion_is_not_partial_completion(tmp_path):
    snapshot, model = run_partial(tmp_path, max_steps=5, extra_hit=True)
    assert snapshot['kind'] == 'error'
    assert snapshot['error']['classification'] == 'budget_exhausted'
    assert snapshot['decision'] is None
    assert model.call_count == 5


def test_recovery_does_not_admit_fabricated_partial_fact(tmp_path):
    snapshot, _ = run_partial(tmp_path, forged=True)
    assert snapshot['kind'] == 'error'
    assert snapshot['reviewer']['performed'] is False


def test_typed_partial_retains_cited_fact_and_names_the_unknown_relation(tmp_path):
    snapshot, model = run_partial(tmp_path, rejection=None)
    assert snapshot['kind'] == 'insufficient', snapshot
    assert snapshot['reviewer']['decision'] == 'evidence_sufficient'
    assert snapshot['decision']['value'] is None
    assert snapshot['uncertainty']['unknown_fields'] == ['reports_to']
    assert snapshot['rationale'] and snapshot['evidence']
    evidence_ids = {item['id'] for item in snapshot['evidence']}
    assert all(point['citations'] and set(point['citations']) <= evidence_ids
               for point in snapshot['rationale'])
    assert 'Lena Marr' in snapshot['rationale'][0]['point']
    assert snapshot['source_investigation']['facts'][0]['value'] == 'Lena Marr'
    assert model.call_count <= 12
