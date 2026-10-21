"""Product HTTP regressions: hints are navigable, proof authority is unchanged."""
from copy import deepcopy
from dataclasses import replace

import pytest

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.tools.contracts import ToolCall
from tests.integration.test_product_application_flow import _client
from tests.m3p_support import cold_read, http_ask, product_case, product_http, revision_product


class EditedOfflineStage:
    """Change scripted responses only; the real validators still run."""
    def __init__(self, original, edit):
        self.original, self.edit = original, edit

    def complete(self, request):
        response = self.original.complete(request)
        call = response.action.tool_call
        arguments = deepcopy(call.arguments)
        self.edit(arguments)
        return ModelResponse(action=ModelAction.tool(ToolCall(call.call_id, call.tool_id, arguments)),
            usage=response.usage)


def checks(result, predicate):
    return [row for row in result['task_review']['relation_triage']['checks'] if row['predicate'] == predicate]


def test_http_discovery_pair_omitted_by_planner_is_visible_without_promotion(tmp_path):
    app, service, _, stages, ws = product_case(tmp_path)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        row, = checks(result, 'RELATED_TO')
        assert row['state'] == 'POSSIBLE'
        assert row['candidates'] and not row['proposals'] and not row['steps']
        assert row['evidence'] and {e['origin'] for e in row['evidence']} == {'DISCOVERY_HINT'}
        assert all(e['source_id'] and e['source_version'] and e['line_start'] for e in row['evidence'])
        assert {e['raw_text'] for e in row['endpoints']} == {o['raw_text'] for o in result['task_review']['observations']}
        assert not result['conclusions'] and result['status'] == 'PARTIAL'
        identity, = checks(result, 'SAME_TASK')
        assert identity['state'] == 'POSSIBLE' and identity['proposals']
        revision, = checks(result, 'REVISES_PLAN')
        assert revision['state'] == 'NOT_ESTABLISHED'
        assert 'DEPENDENCY_NOT_SUPPORTED:identity' in revision['reason_codes']
        assert [len(stage.requests) for stage in stages] == [2, 1, 1]
        assert not (tmp_path / 'relations.sqlite').exists()
        code, payload = cold_read(tmp_path / 'product.sqlite', result['agent_run_id'], ws)
        assert code == 200
        assert payload['result']['task_review']['relation_triage'] == result['task_review']['relation_triage']
    finally:
        app.close()


@pytest.mark.parametrize('verdict', ['UNKNOWN', 'REFUTED', 'CONFLICT'])
def test_http_unconfirmed_negative_or_unknown_model_never_establishes_identity(tmp_path, verdict):
    app, service, _, stages, ws = product_case(tmp_path, conflict=True)

    def edit(arguments):
        for proposal in arguments['proposals']:
            proposal['proposed_verdict'] = verdict
            original = proposal['supporting_evidence']
            # Cite the whole host-declared inventory even for UNKNOWN/REFUTED;
            # source coverage is a separate, unchanged release requirement.
            proposal['supporting_evidence'] = original if verdict == 'UNKNOWN' else original[:2]
            proposal['counter_evidence'] = original[-1:] if verdict in {'CONFLICT', 'REFUTED'} else []
            proposal['missing_evidence'] = ['No independent identity proof']
    service.task_models = replace(service.task_models,
        semantics=EditedOfflineStage(stages[2], edit))
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        row, = checks(result, 'SAME_TASK')
        assert row['state'] == 'NOT_ESTABLISHED', row
        assert row['proposals'][0]['evidence_status'] == 'EVIDENCE_VALID'
        assert row['proposals'][0]['proposed_verdict'] == verdict
        if verdict == 'CONFLICT':
            assert {e['role'] for e in row['evidence']} == {'SUPPORT', 'COUNTER'}
            assert any('尚未批准' in e['quote'] for e in row['evidence'] if e['role'] == 'COUNTER')
        assert not result['conclusions']
    finally:
        app.close()


@pytest.mark.parametrize('reverse_revision', [False, True])
def test_http_p4_support_is_exact_and_directional(tmp_path, reverse_revision):
    app, service, stages, ws, _, _, _ = revision_product(tmp_path)
    before = (tmp_path / 'relations.sqlite').read_bytes()
    if reverse_revision:
        def reverse(arguments):
            step = next(s for s in arguments['steps'] if s['id'] == 'revision')
            step['handles'].reverse()
        service.task_models = replace(service.task_models,
            planner=EditedOfflineStage(stages[1], reverse))
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        identity, = checks(result, 'SAME_TASK')
        assert identity['state'] == 'ESTABLISHED'
        revisions = checks(result, 'REVISES_PLAN')
        planned = next(r for r in revisions if r['steps'])
        assert planned['state'] == ('NOT_ESTABLISHED' if reverse_revision else 'ESTABLISHED')
        assert planned['directed'] is True
        if reverse_revision:
            assert not any(r['state'] == 'ESTABLISHED' for r in revisions)
            assert planned['steps'][0]['status'] == 'BLOCKED'
        else:
            assert '本条' in planned['endpoints'][0]['raw_text']
            assert any(e['origin'] == 'P4_RECORD' for e in planned['evidence'])
        assert (tmp_path / 'relations.sqlite').read_bytes() == before
        assert not result['conclusions']
    finally:
        app.close()


def test_http_revocation_and_other_workspace_hide_entire_triage_hot_and_cold(tmp_path):
    app, service, _, _, ws = product_case(tmp_path)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
            other = app.repository.create_workspace('Other synthetic workspace')['workspace_id']
            run_url = f"/api/product/ask/runs/{result['agent_run_id']}?workspace_id="
            code, payload = _client(base, run_url + other)
            assert code == 409 and payload['error']['code'] == 'WORKSPACE_MISMATCH', payload
            assert 'task_review' not in payload.get('result', {})
            app.repository.select_workspace(ws)
            with app.repository._lock, app.repository._connection:
                app.repository._connection.execute(
                    'DELETE FROM ll_source_evidence WHERE workspace_id=? AND evidence_ref=?',
                    (ws, result['evidence'][0]['evidence_ref']))
            code, payload = _client(base, run_url + ws)
            assert code == 200 and payload['result']['status'] == 'BLOCKED'
            assert not payload['result'].get('task_review') and not payload['result']['evidence']
    finally:
        app.close()
    code, payload = cold_read(tmp_path / 'product.sqlite', result['agent_run_id'], ws)
    assert code == 200 and payload['result']['status'] == 'BLOCKED'
    assert not payload['result'].get('task_review') and not payload['result']['evidence']


def test_http_no_candidates_retains_unknown_without_inventing_pair(tmp_path):
    app, service, _, _, ws = product_case(tmp_path, missing=True)
    try:
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        assert result['status'] == 'UNKNOWN'
        assert result['task_review']['relation_triage']['checks'] == []
    finally:
        app.close()


def test_fresh_http_after_retraction_discloses_missing_history_and_never_restores_proof(tmp_path):
    from tests.unit.test_task_relation_query import retract
    app, service, _, ws, identity, *_ = revision_product(tmp_path)
    try:
        retract(tmp_path / 'relations.sqlite', ws, identity.assessment_id)
        before = (tmp_path / 'relations.sqlite').read_bytes()
        with product_http(app, service) as base:
            result = http_ask(base, ws)
        view = result['task_review']['relation_triage']
        assert view['p4_history_included'] is False
        # The unchanged query omits historical P4 records. New hints are not a
        # diagnostic of prior revocation and cannot restore any trusted step.
        assert checks(result, 'SAME_TASK')[0]['state'] == 'POSSIBLE'
        assert all(row['state'] != 'ESTABLISHED' for row in view['checks'])
        assert all(step['status'] == 'BLOCKED' for step in result['task_review']['steps'])
        assert not result['conclusions'] and (tmp_path / 'relations.sqlite').read_bytes() == before
    finally:
        app.close()
