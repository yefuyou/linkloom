from pathlib import Path

import pytest

from linkloom.evaluation.task_profile_ab import load_freeze, run


ROOT = Path(__file__).resolve().parents[2]


def test_complete_mock_pair_and_exact_replay(tmp_path):
    freeze = load_freeze()
    report = run(tmp_path / 'mock')
    assert report['real_provider_requests'] == 0
    assert report['REAL_MODEL_SEMANTIC_EFFECT'] == 'NOT_EVALUATED'
    assert report['counts'] == {'A': len(freeze['sources']), 'B': len(freeze['sources'])}
    assert all(p['schema_equal'] and p['options_equal'] and p['profile_different']
               for p in report['pairs'])
    replayed = run(tmp_path / 'replay', mode='replay', replay=tmp_path / 'mock')
    assert replayed['counts'] == report['counts']
    assert replayed['scores'] == report['scores']


def test_live_default_denies_before_sdk_or_output(tmp_path):
    with pytest.raises(ValueError, match='AUTHORIZATION'):
        run(tmp_path / 'denied', mode='live')
    assert not (tmp_path / 'denied').exists()


def test_mock_does_not_construct_sdk_or_read_labels(tmp_path, monkeypatch):
    import linkloom.evaluation.task_profile_ab as ab
    monkeypatch.setattr(ab, 'open_live', lambda *a, **k: pytest.fail('SDK constructed'))
    original = ab.read_frozen
    def read(name, manifest):
        assert name not in ('labels.json','groups.json'), 'labels leaked into execution'
        return original(name,manifest)
    monkeypatch.setattr(ab, 'read_frozen', read)
    result = ab.execute(tmp_path / 'execute')
    assert result['counts']['A'] == result['counts']['B']


def test_frozen_source_is_parsed_from_hashed_bytes_not_a_later_read(tmp_path,monkeypatch):
    import json
    import linkloom.evaluation.task_profile_ab as ab
    original = ab.read
    sources = original(ab.FREEZE/'sources.json')
    # Simulate file replacement between hash verification and later JSON read.
    altered = json.loads(json.dumps(sources))
    altered[0]['content'] += '\nUNAPPROVED_SOURCE_REPLACEMENT'
    from linkloom.semantic_ingestion.artifacts import RawArtifact
    from datetime import datetime
    row = altered[0]
    artifact = RawArtifact(row['workspace_id'],row['id'],row['source_type'],row['content'],
        ingestion_time=datetime.fromisoformat(row['ingestion_time']))
    row['artifact_version_id'],row['content_sha256'] = artifact.artifact_version_id,artifact.content_hash
    monkeypatch.setattr(ab,'read',lambda path: altered if Path(path).name=='sources.json' else original(path))
    assert ab.load_freeze()['sources'] == sources


def test_execution_never_opens_score_only_files(tmp_path,monkeypatch):
    import linkloom.evaluation.task_profile_ab as ab
    original = Path.open
    def guarded(path,*args,**kwargs):
        if path.parent == ab.FREEZE and path.name in ('labels.json','groups.json','rubric.json'):
            pytest.fail('execution opened scoring-only bytes: '+path.name)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    ab.execute(tmp_path/'no-score-io')


@pytest.mark.parametrize('shape',['extra_root','over_limit'])
def test_root_schema_error_keeps_raw_task_candidates(shape):
    from linkloom.evaluation.task_profile_score import score_case
    from tests.unit.test_task_observation_discovery import claim, setup, source
    artifact = source('安排验证远程备份。')
    proposal = claim('验证远程备份','验证远程备份')
    payload = {'claims':[proposal], 'extra':True} if shape=='extra_root' else {'claims':[proposal]*21}
    service,provider,_,_ = setup(artifact,payload=payload)
    result = service.discover((artifact,),workspace_id=artifact.workspace_id)
    labels = [dict(text=artifact.content,description_parts=['验证远程备份'],owner=None,project=None,vendor=None,deadline_text=None)]
    scored = score_case(artifact,labels,provider.complete(provider.requests[0]),result)
    assert scored['raw']['matched'] == 1 and scored['raw_proposals'] >= 1
    assert not scored['schema_valid'] and not scored['attribution']['model_omission']


def test_raw_candidate_and_host_rejection_are_separate():
    from linkloom.evaluation.task_profile_score import score_case
    from tests.unit.test_task_observation_discovery import claim, setup, source
    artifact = source('安排验证远程备份。')
    proposal = claim('验证远程备份', '验证远程备份')
    proposal['confidence']['overall'] = 2  # malformed output, before Host projection
    service, provider, _, _ = setup(artifact, [proposal])
    result = service.discover((artifact,), workspace_id=artifact.workspace_id)
    expected = [dict(text=artifact.content,description_parts=['验证远程备份'],
        owner=None,project=None,vendor=None,deadline_text=None)]
    response = provider.complete(provider.requests[0])
    score = score_case(artifact,expected,response,result)
    assert score['raw']['matched'] == 1
    assert score['host']['matched'] == 0
    assert score['attribution']['model_omission'] == []
    assert score['attribution']['host_rejection'] == [0]
    assert score['attribution']['format_failure']
    assert score['attribution']['provider_infrastructure_failure'] is None
    assert score['false_rejections'] == 0  # rejecting malformed output is correct


def test_same_work_in_different_messages_cannot_match_wrong_occurrence():
    from linkloom.evaluation.task_profile_score import _score
    source = '项目甲：整理清单。项目乙：整理清单。'
    labels = [dict(text='项目甲：整理清单。',description_parts=['整理清单'],
        owner=None,project=None,vendor=None,deadline_text=None)]
    assert _score([dict(text='项目乙：整理清单。',description_parts=['整理清单'])],labels,source)['matched'] == 0


def test_explicit_cross_message_group_gets_all_raw_work_credit():
    from linkloom.evaluation.task_profile_score import score_case
    from tests.unit.test_task_observation_discovery import claim, setup, source
    artifact = source('[2029-06-01T09:00:00Z] 林枫: 安排日志迁移。\n'
        '[2029-06-01T09:01:00Z] 周岚: 日志迁移还包括补齐回归脚本。')
    service,provider,_,_ = setup(artifact,[claim(artifact.content,'日志迁移','补齐回归脚本')])
    result = service.discover((artifact,),workspace_id=artifact.workspace_id)
    labels = [dict(text='安排日志迁移。',description_parts=['日志迁移'],owner=None,project=None,vendor=None,deadline_text=None),
        dict(text='日志迁移还包括补齐回归脚本。',description_parts=['补齐回归脚本'],owner=None,project=None,vendor=None,deadline_text=None)]
    response = provider.complete(provider.requests[0])
    assert score_case(artifact,labels,response,result)['raw']['matched'] == 2
    score = score_case(artifact,labels,response,result,allowed_groups=((0,1),))
    assert score['raw']['matched'] == score['host']['matched'] == 2
    assert score['raw']['erroneous_merges'] == score['host']['erroneous_merges'] == 0
    assert score['raw_proposals'] == 1


def test_pending_reservation_survives_reopen_and_stops_new_identity(tmp_path):
    from linkloom.evaluation.task_profile_ab import Budget
    budget = Budget(tmp_path/'budget.sqlite3',load_freeze())
    budget.reserve('D01-A', {'request':'exact'})
    budget.close()
    reopened = Budget(tmp_path/'budget.sqlite3',load_freeze())
    with pytest.raises(ValueError, match='PENDING'):
        reopened.reserve('D01-B', {'request':'other'})
    reopened.close()


def test_budget_never_refunds_or_repeats_completed_request(tmp_path):
    from linkloom.evaluation.task_profile_ab import Budget
    freeze = load_freeze()
    budget = Budget(tmp_path/'budget.sqlite3',freeze)
    for i in range(freeze['config']['max_requests']):
        budget.reserve(str(i),{})
        budget.complete(str(i),{},safe=True)
    with pytest.raises(ValueError, match='BUDGET_EXCEEDED'):
        budget.reserve('extra',{})
    with pytest.raises(ValueError, match='DUPLICATE'):
        budget.reserve('0',{})
    budget.close()


def test_live_reserves_before_sdk_and_unknown_usage_stops(tmp_path):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace
    from linkloom.evaluation.task_profile_ab import Budget, LiveTransport
    freeze = load_freeze()
    config = freeze['config']
    grant = dict(approved=True,experiment='m3o-ab/v1',manifest_sha256=freeze['manifest_sha256'],
        expires_at=(datetime.now(UTC)+timedelta(hours=1)).isoformat(),
        **{k:config[k] for k in ('model_id','provider_id','max_requests','max_cost_usd',
            'input_price_ceiling_per_million','output_price_ceiling_per_million')})
    budget = Budget(tmp_path/'budget.sqlite3',freeze)
    def send(**kwargs):
        assert budget.db.execute('SELECT status FROM requests').fetchone() == ('RESERVED',)
        return {'candidates':[{'content':{'parts':[{'function_call':{
            'name':'emit_semantic_candidates','args':{'claims':[]}}}]},'finish_reason':'STOP'}],
            'model_version':config['model_id'], 'usage_metadata':{'prompt_token_count':10,'candidates_token_count':5}}
    live = LiveTransport(freeze,grant,SimpleNamespace(models=SimpleNamespace(generate_content=send)),budget)
    # Capture the canonical extractor request offline, then use only a Fake SDK.
    from tests.unit.test_task_observation_discovery import setup, source
    service, provider, _, _ = setup(source('安排整理清单。'))
    service.discover((service.current_source('',''),),workspace_id='public-workspace')
    request = provider.requests[0]
    from dataclasses import replace
    request = replace(request,model_id=config['model_id'])
    with pytest.raises(ValueError,match='OUTCOME_STOP'):
        live.complete(request,'D01-A')
    assert budget.db.execute('SELECT status FROM requests').fetchone() == ('UNKNOWN_STOP',)
    with pytest.raises(ValueError,match='PENDING'):
        live.complete(request,'D01-B')
    budget.close()
