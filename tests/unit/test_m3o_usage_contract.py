"""Usage arithmetic and the production staged path, always offline."""
from copy import deepcopy
from decimal import Decimal
import json
import sqlite3

import pytest

from linkloom.evaluation import task_profile_ab as ab
from tests.unit.test_m3o_ab_checkpoint import campaign


def record(usage):
    return dict(usage=deepcopy(usage),sdk_response={'usage_metadata':deepcopy(usage)},
        normalized={'usage':dict(input_tokens=usage.get('prompt_token_count'),
            output_tokens=usage.get('candidates_token_count'),total_tokens=usage.get('total_token_count'))})


def usage(**changes):
    return dict(prompt_token_count=100,candidates_token_count=10,total_token_count=110,**changes)


def test_optional_usage_completes_real_stages_without_resending(campaign):
    stage,sends,root,output,grant,fault=campaign
    # Neither optional component is reported; R=15 bounds both without inventing a split.
    fault['usage_override']={'promptTokenCount':100,'candidatesTokenCount':10,'totalTokenCount':125}
    assert stage('FIRST_PAIR')['sends_this_invocation']==2
    assert len(sends)==2
    assert stage('VERIFY_FIRST_PAIR')['FIRST_PAIR_GATE']=='PASS'
    assert len(sends)==2
    assert stage('CONTINUE_REMAINING')['sends_this_invocation']==24
    assert len(sends)==26
    assert stage('CONTINUE_REMAINING')['sends_this_invocation']==0
    assert len(sends)==26
    with sqlite3.connect(root/'budget.sqlite3') as db:
        rows=db.execute('SELECT identity,status,response FROM requests').fetchall()
    assert len(rows)==len({r[0] for r in rows})==26
    assert all(status=='COMPLETE' for _,status,_ in rows)
    for _,_,payload in rows:
        saved=json.loads(payload)
        assert saved['usage']['thoughts_token_count'] is None
        assert saved['usage_accounting']['state']=='BOUNDED'
        assert saved['usage_accounting']['thinking']['upper']==15
    assert 26*ab.load_freeze()['config']['reservation_usd']==pytest.approx(2.076672)


@pytest.mark.parametrize('extra,total,state,thinking,tool',[
    ({'thoughts_token_count':4,'tool_use_prompt_token_count':3},117,'REPORTED',(4,4),(3,3)),
    ({},110,'DERIVED',(0,0),(0,0)),
    ({'thoughts_token_count':None,'tool_use_prompt_token_count':3},120,'DERIVED',(7,7),(3,3)),
    ({'thoughts_token_count':None,'tool_use_prompt_token_count':None},125,'BOUNDED',(0,15),(0,15)),
    ({'thoughts_token_count':2},117,'DERIVED',(2,2),(5,5)),
])
def test_accounting_provenance_and_raw_preservation(extra,total,state,thinking,tool):
    r=record(usage(**extra));r['usage']['total_token_count']=total
    r['sdk_response']['usage_metadata']['total_token_count']=total
    r['normalized']['usage']['total_tokens']=total
    before=deepcopy(r)
    result=ab.account_usage(r,ab.load_freeze())
    assert result['state']==state
    assert (result['thinking']['lower'],result['thinking']['upper'])==thinking
    assert (result['tool_use_prompt']['lower'],result['tool_use_prompt']['upper'])==tool
    assert r==before


@pytest.mark.parametrize('changes',[
    {'total_token_count':None}, {'total_token_count':109}, {'total_token_count':-1},
    {'total_token_count':True}, {'total_token_count':'110'}, {'prompt_token_count':-1},
    {'total_token_count':110.0}, {'prompt_token_count':True}, {'candidates_token_count':10.0},
    {'candidates_token_count':False}, {'thoughts_token_count':-1},
    {'thoughts_token_count':'0'}, {'tool_use_prompt_token_count':-1},
    {'thoughts_token_count':False}, {'tool_use_prompt_token_count':0.0},
    {'cached_content_token_count':101}, {'cached_content_token_count':-1},
    {'thoughts_token_count':1,'tool_use_prompt_token_count':0},
    {'total_token_count':120,'thoughts_token_count':0,'tool_use_prompt_token_count':0},
    {'total_token_count':4000}, {'prompt_token_count':65537,'total_token_count':65547},
])
def test_unsafe_usage_is_unknown(changes):
    u=usage();u.update(changes)
    assert not ab.account_usage(record(u),ab.load_freeze())['safe']


@pytest.mark.parametrize('field',['input_tokens','output_tokens','total_tokens'])
def test_normalized_usage_cannot_disagree(field):
    r=record(usage());r['normalized']['usage'][field]+=1
    assert ab.account_usage(r,ab.load_freeze())['state']=='UNKNOWN'


def test_raw_sdk_usage_cannot_disagree():
    r=record(usage());r['sdk_response']['usage_metadata']['total_token_count']=111
    assert ab.account_usage(r,ab.load_freeze())['state']=='UNKNOWN'


@pytest.mark.parametrize('raw_value',[100.0,True])
def test_raw_snapshot_numeric_types_cannot_hide_behind_dict_equality(raw_value):
    u=usage();u['prompt_token_count']=1 if raw_value is True else 100
    u['total_token_count']=u['prompt_token_count']+10
    r=record(u);r['sdk_response']['usage_metadata']['prompt_token_count']=raw_value
    assert ab.account_usage(r,ab.load_freeze())['state']=='UNKNOWN'


def test_unknown_tool_use_must_fit_total_input_cap():
    f=deepcopy(ab.load_freeze());f['config']['input_token_cap']=100
    assert ab.account_usage(record(usage(thoughts_token_count=None)),f)['state']=='DERIVED'
    r=record(dict(prompt_token_count=100,candidates_token_count=10,total_token_count=111))
    assert not ab.account_usage(r,f)['safe']


def test_reservation_is_a_separate_guard():
    f=deepcopy(ab.load_freeze());f['config']['reservation_usd']=0.00001
    result=ab.account_usage(record(usage()),f)
    assert result['state']=='DERIVED' and not result['safe']
    assert not result['within_reservation']
    assert result['reason']=='RESERVATION_UPPER_BOUND_EXCEEDED'


def test_derived_tool_input_uses_known_input_rate_without_double_counting():
    result=ab.account_usage(record(dict(prompt_token_count=100,candidates_token_count=10,
        thoughts_token_count=2,total_token_count=117)),ab.load_freeze())
    assert result['tool_use_prompt']['state']=='DERIVED'
    assert Decimal(result['cost_upper_usd'])==Decimal('0.00012375')


def test_missing_total_and_known_components_exceeding_total_are_unknown():
    for u in ({'prompt_token_count':100,'candidates_token_count':10},
        dict(prompt_token_count=100,candidates_token_count=10,thoughts_token_count=3,total_token_count=112)):
        assert ab.account_usage(record(u),ab.load_freeze())['state']=='UNKNOWN'


@pytest.mark.parametrize('thinking,tool,total',[(3063,0,3173),(0,65500,65610)])
def test_reported_components_still_obey_generation_and_input_caps(thinking,tool,total):
    r=record(dict(prompt_token_count=100,candidates_token_count=10,total_token_count=total,
        thoughts_token_count=thinking,tool_use_prompt_token_count=tool))
    result=ab.account_usage(r,ab.load_freeze())
    assert result['state']=='REPORTED' and not result['safe']
    assert result['reason']=='TOKEN_UPPER_BOUND_EXCEEDED'


def test_cached_prompt_is_not_added_twice():
    result=ab.account_usage(record(usage(cached_content_token_count=50)),ab.load_freeze())
    assert result['safe'] and result['input_upper']==100
    assert Decimal(result['cost_upper_usd'])==Decimal('0.0001125')


@pytest.mark.parametrize('legacy',['m3o-ab/v1','m3o-ab/v2'])
def test_old_experiment_grant_is_not_transferred(campaign,legacy):
    stage,sends,root,_,grant,_=campaign
    grant['experiment']=legacy
    with pytest.raises(ValueError,match='AUTHORIZATION'):
        stage('FIRST_PAIR')
    assert not sends and not root.exists()


def test_verify_recomputes_accounting(campaign):
    from linkloom.evaluation.task_profile_checkpoint import _usage_valid
    stage,_,root,output,_,fault=campaign
    fault['usage_override']={'promptTokenCount':100,'candidatesTokenCount':10,'totalTokenCount':110}
    stage('FIRST_PAIR')
    saved=ab.read(output/'D01-A.sdk.json')
    assert _usage_valid(saved,ab.load_freeze())
    saved['usage_accounting']['thinking']['upper']=99
    assert not _usage_valid(saved,ab.load_freeze())


@pytest.mark.parametrize('tamper',['prompt_float','safe_int','bound_float'])
def test_verifier_requires_exact_accounting_receipt_types(tamper):
    from linkloom.evaluation.task_profile_checkpoint import _usage_valid
    f=ab.load_freeze();r=record(usage())
    r.update(actual_model_version=f['config']['model_id'],reservation_usd=f['config']['reservation_usd'],wire_sends=1)
    r['usage_accounting']=ab.account_usage(r,f)
    assert _usage_valid(r,f)
    if tamper=='prompt_float':r['usage_accounting']['prompt']['value']=100.0
    elif tamper=='safe_int':r['usage_accounting']['safe']=1
    else:r['usage_accounting']['thinking']['upper']=0.0
    assert not _usage_valid(r,f)
