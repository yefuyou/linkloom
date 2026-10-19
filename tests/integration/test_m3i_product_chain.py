from tests.m3i_support import product_case, finish, QUESTION
from dataclasses import replace
import json

import pytest


def test_natural_question_uses_f_g_existing_runtime_context_and_reviewer(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    result, status = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    assert status == 202, result
    result = finish(ask, workspace, result)
    assert result['status'] == 'PARTIAL', result
    assert result['reviewer']['accepted'], result
    assert len(stages[0].requests) == 2 and len(stages[1].requests) == len(stages[2].requests) == 1
    assert len(result['evidence']) == 2
    assert all(e['source_id'] and e['source_version'] == e['artifact_version_id'] for e in result['evidence'])
    assert result['preparation']['evidence_layers']['real_model_semantics'] == 'NOT_EVALUATED'
    assert 'UNSATISFIED_PREMISE:revision' in result['preparation']['gaps']
    assert result['working_context_audit']['recovery']['status'] == 'complete'
    assert [t['tool'] for t in result['trace']] == ['inspect_task_context', 'query_task_relations', 'analyze_task_semantics']
    assert not result['conclusions']


@pytest.mark.parametrize('case', ['missing', 'conflict', 'replan'])
def test_missing_conflict_and_host_verified_unknown_replanning_are_visible(tmp_path, case):
    app, service, ask, stages, workspace = product_case(tmp_path, **{case: True})
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    assert result['status'] in {'UNKNOWN', 'PARTIAL'}, result
    assert not result['conclusions']
    if case == 'missing':
        assert result['status'] == 'UNKNOWN'
        assert any('ABSENCE_NOT_PROVEN' in gap for gap in result['preparation']['gaps'])
        assert len(stages[1].requests) == 2
        assert not stages[2].requests
    if case == 'conflict':
        analysis = result['task_investigation']['semantic_analysis']
        assert analysis['proposals'][0]['proposed_verdict'] == 'UNKNOWN'
        assert analysis['proposals'][0]['missing_evidence'] == ['日期存在冲突']
        assert len(result['evidence']) == 3
    if case == 'replan':
        assert 'BOUNDED_DISCOVERY_CANNOT_ESTABLISH_MISSING_ENTITY_ABSENCE' in result['preparation']['gaps']
        assert len(stages[1].requests) == 2 and len(stages[0].requests) == 2
        plans = result['preparation']['planning']
        assert plans[1]['plan']['parent_plan_id'] == plans[0]['plan']['plan_id']
        assert plans[1]['plan']['verified_results'][0]['outcome'] == 'UNKNOWN'


@pytest.mark.parametrize('mutation', ['update', 'remove'])
def test_completed_product_run_cannot_publish_stale_or_withdrawn_sources(tmp_path, mutation):
    app, service, ask, stages, workspace = product_case(tmp_path)
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    source = app.snapshot_investigation_sources(workspace)[1][0]
    if mutation == 'remove':
        app.repository.delete_source(workspace, source['source_id'])
    else:
        artifact = app._artifact_from_version(dict(source, content=source['content'] + ' 来源纠正。'))
        from linkloom.semantic_ingestion.document_text import parse_source_text
        app.repository.update_source(workspace, source['source_id'], source['name'], artifact,
            tuple(s.evidence_span for s in parse_source_text(artifact).segments))
    blocked, status = ask.inspect(result['agent_run_id'], workspace)
    assert blocked['status'] == 'BLOCKED' and blocked['error']['code'] == 'SOURCE_SNAPSHOT_CHANGED'
    assert not blocked['evidence'] and not blocked['trace']
    assert 'task_investigation' not in blocked


def test_source_changed_during_projection_is_rejected_before_publication(tmp_path, monkeypatch):
    app, service, ask, stages, workspace = product_case(tmp_path)
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    source = app.snapshot_investigation_sources(workspace)[1][0]
    backend = service._jobs[result['agent_run_id']].backend
    inspect = backend.inspect
    def change_during_projection(run_id):
        snapshot = inspect(run_id)
        app.repository.delete_source(workspace, source['source_id'])
        return snapshot
    monkeypatch.setattr(backend, 'inspect', change_during_projection)
    blocked, _ = ask.inspect(result['agent_run_id'], workspace)
    assert blocked['status'] == 'BLOCKED' and not blocked['evidence'] and not blocked['trace']


def test_planning_budget_blocks_before_runtime_and_retains_stage_failure(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    service.task_models = replace(service.task_models,
        planning_limits=replace(service.task_models.planning_limits, max_input_tokens=100))
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    assert result['status'] == 'BLOCKED'
    assert 'INPUT_BUDGET_EXCEEDED' in result['preparation']['gaps']
    assert not stages[1].requests and not stages[2].requests and not result['trace']


def test_unconfigured_and_external_task_chain_never_calls_models(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    service.external_provider = True
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace, allow_external_provider=True))
    assert result['status'] == 'BLOCKED'
    assert not any(s.requests for s in stages)
    assert not service._jobs


def test_advanced_date_is_never_silently_ignored_by_task_route(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace, as_of='2029-06-02'))
    assert result['status'] == 'BLOCKED' and result['error']['code'] == 'TASK_DATE_SCOPE_UNSUPPORTED'
    assert not any(stage.requests for stage in stages) and not service._jobs


def test_invalid_semantic_citation_is_explicitly_blocked_not_a_publishable_partial(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    complete = stages[2].complete
    def invalid(request):
        response = complete(request)
        response.action.tool_call.arguments['proposals'][0]['supporting_evidence'][0]['span_id'] = 'fabricated'
        return response
    stages[2].complete = invalid
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    assert result['status'] == 'BLOCKED', result
    assert result['uncertainty']['stage_errors'] and not result['conclusions']
    assert result['task_investigation']['semantic_analysis']['analysis_status'] == 'OUTPUT_INVALID'
    assert all(e['evidence_ref'] != 'fabricated' for e in result['evidence'])


def test_g_plan_never_triggers_the_generic_runtime_planner(tmp_path):
    app, service, ask, stages, workspace = product_case(tmp_path)
    factory, calls = service.model_factory, []
    def trap_factory(query):
        model = factory(query)
        def forbidden(*args, **kwargs):
            calls.append(True)
            raise AssertionError('A second generic planner must never run.')
        model.propose_task_plan = forbidden
        return model
    service.model_factory = trap_factory
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    assert result['status'] == 'PARTIAL', result
    assert not calls


@pytest.mark.parametrize('pressure', ['compact', 'stop'])
def test_product_context_pressure_recovers_originals_or_stops_before_model(tmp_path, monkeypatch, pressure):
    from linkloom.runtime.context_engineering import ContextRuntimeConfig
    import linkloom.ui.investigation as bridge
    config = ContextRuntimeConfig(history_soft_bytes=1)
    if pressure == 'stop':
        config = replace(config, local_budget=replace(config.local_budget, max_evidence=1))
    monkeypatch.setattr(bridge, 'ContextRuntimeConfig', lambda: config)
    app, service, ask, stages, workspace = product_case(tmp_path)
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    if pressure == 'stop':
        assert result['status'] == 'BLOCKED', result
        assert result['error']['code'] == 'CONTEXT_INCOMPLETE'
        assert result['metrics']['model_turns'] == 0 and not stages[2].requests
    else:
        assert result['status'] == 'PARTIAL' and result['reviewer']['accepted'], result
        job = service._jobs[result['agent_run_id']]
        state = job.backend.engine.checkpointer.get_latest(job.backend._jobs[result['agent_run_id']].thread_id)
        audits = [json.loads((job.backend.engine.checkpoint_dir / 'models' / record.request_ref).read_text(encoding='utf8'))
                  ['context_audit']['engineering'] for record in state.model_executions]
        assert any(a['compaction'] for a in audits)
        assert all(a['recovery']['status'] == 'complete' for a in audits)
        assert len(result['evidence']) == 2


def test_checkpoint_resume_after_prepared_product_tools_does_not_repeat_confirmed_actions(tmp_path, monkeypatch):
    from linkloom.ui.task_investigation import ProductTaskSources, prepare_task_investigation
    from linkloom.runtime.context_engineering import ContextRuntimeConfig
    from linkloom.runtime.graph import RuntimeEngine
    from linkloom.runtime.models import RunRequest
    from tests.integration.test_task_relation_runtime import engine_for
    from tests.m3i_support import runtime_model
    from linkloom.agents.model_adapter import FakeModelAdapter
    app, service, ask, stages, workspace = product_case(tmp_path)
    prepared = prepare_task_investigation(QUESTION, ProductTaskSources(workspace,
        app.snapshot_investigation_sources(workspace)[1], lambda: app.snapshot_investigation_sources(workspace)[1]),
        service.task_models, tmp_path / 'preparation')
    runtime_root = tmp_path / 'execution'
    runtime_root.mkdir()
    outer = runtime_model(QUESTION)
    engine = engine_for(runtime_root, prepared.tools, outer)
    config = ContextRuntimeConfig()
    engine._context_runtime_config = config
    saved, save = {}, engine.checkpointer.save
    class Crash(BaseException):
        pass
    def crash(state, *args, **kwargs):
        ref = save(state, *args, **kwargs)
        if len(state.tool_ledger) == 3 and state.model_executions[-1].status == 'tool_result_durable':
            saved['state'] = state
            raise Crash()
        return ref
    monkeypatch.setattr(engine.checkpointer, 'save', crash)
    with pytest.raises(Crash):
        engine.start_multi_agent(RunRequest(request_id='product-resume', workflow='task_relations', query=QUESTION,
            max_steps=4, max_provider_requests=4, planning_mode='template'))
    before = saved['state']
    # The resumed process supplies the same current Host source authority and
    # adapters, but the confirmed tool actions must come from the checkpoint.
    final = runtime_model(QUESTION)._script[-1]
    model = FakeModelAdapter([final])
    cold = RuntimeEngine(engine.vault_root, engine.index_path, engine.checkpoint_dir,
        trace_dir=engine.trace_dir, model=model, task_relation_tools=prepared.tools,
        context_runtime_config=config, memory_enabled=False, decision_memory_enabled=False)
    result = cold.resume_multi_agent(before.thread_id)
    assert result.status == 'completed', result.to_dict()
    after = cold.checkpointer.get_latest(before.thread_id)
    assert [r.to_dict() for r in after.tool_ledger] == [r.to_dict() for r in before.tool_ledger]
    assert [len(stage.requests) for stage in stages] == [2, 1, 1]
    assert model.call_count == 1


@pytest.mark.parametrize('gap', ['missing_document', 'discovery_budget'])
def test_missing_original_document_and_unexamined_source_budget_are_explicit(tmp_path, gap):
    app, service, ask, stages, workspace = product_case(tmp_path)
    if gap == 'missing_document':
        source = app.snapshot_investigation_sources(workspace)[1][-1]
        app.repository.delete_source(workspace, source['source_id'])
    else:
        service.task_models = replace(service.task_models,
            discovery_limits=replace(service.task_models.discovery_limits, max_calls=1))
    # Scripted planner explicitly requests the missing evidence instead of
    # manufacturing an endpoint. Discovery still processes the available source.
    stages[1].missing = True
    result, _ = ask.ask(dict(query=QUESTION, workspace_id=workspace))
    result = finish(ask, workspace, result)
    assert result['status'] == 'PARTIAL' and not result['conclusions'], result
    assert len(stages[0].requests) == 1 and not stages[2].requests
    assert 'BOUNDED_DISCOVERY_CANNOT_ESTABLISH_MISSING_ENTITY_ABSENCE' in result['preparation']['gaps']
    if gap == 'discovery_budget':
        assert 'DISCOVERY_COVERAGE_INCOMPLETE' in result['preparation']['gaps']
        assert result['preparation']['discovery']['unprocessed_sources'][0]['reason'] == 'CALL_BUDGET_EXHAUSTED'
