"""Development harness isolation and population semantics, with fake providers."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from tests.unit.test_meeting_tasks_v3 import Adapter, proposal
from tests.unit.test_meeting_tasks_v2 import ROSTER

spec=importlib.util.spec_from_file_location('meeting_v3_eval',Path(__file__).resolve().parents[2]/'scripts/eval_meeting_v3.py')
ev=importlib.util.module_from_spec(spec);sys.modules[spec.name]=ev;spec.loader.exec_module(ev)


def test_prediction_keeps_low_and_exact_quotes_and_does_not_read_labels(tmp_path):
    bodies=['I will send the package.','Please do it before the next workshop.']
    text='';utterances=[]
    for i,body in enumerate(bodies):
        line=f'X [{i:.3f}-{i+1:.3f}]: {body}\n'
        utterances.append({'speaker':'X','text':body,'start':i,'end':i+1,'char_start':len(text),'char_end':len(text)+len(line)})
        text+=line
    meeting={'meeting_id':'fixture','text':text,'utterances':utterances,'speakers':ROSTER}
    adapter=Adapter([proposal(confidence='LOW',confidence_reason='Casual self-commitment.')])
    adapter.output_dir=tmp_path;adapter.gemini_max_input_bytes=196608
    predictions,batch=ev.predict(meeting,adapter,task_extraction_mode='meeting_v3')
    assert len(predictions)==1 and predictions[0]['confidence']=='LOW'
    assert predictions[0]['evidence_quotes']==bodies
    assert predictions[0]['resolved_at'] is None
    assert all(x not in adapter.requests[0].user_input for x in ('gold_id','keep','ES2004'))


def test_all_predictions_before_gold_and_high_subset_independently_scored(tmp_path,monkeypatch):
    events=[]
    meetings=[{'meeting_id':f'{team}{p}'} for team in ('ES2002','ES2003') for p in 'abcd']
    monkeypatch.setattr(ev,'load_inputs',lambda *a:meetings)
    def predict(meeting,client,**kwargs):
        events.append(meeting['meeting_id'])
        assert client.counter.max_requests==24 and client.counter.max_usd==0.6
        return [{'task':'Work one','confidence':'HIGH'},{'task':'Work two','confidence':'LOW'}],{'rejected':[]}
    def score(subset,gold,target,client,predictions):
        assert len(events)==8
        assert all(len(v['baseline'])==2 and len(v['linkloom'])==1 for v in predictions.values())
        ev.ev.new_json(target/'results.json',{})
        ev.ev.new_json(target/'judgments.json',[])
    monkeypatch.setattr(ev,'predict',predict)
    monkeypatch.setattr(ev.ev,'finish_evaluation',score)
    monkeypatch.setattr(ev,'write_report',lambda *a:None)
    gold=tmp_path/'gold.jsonl';gold.write_text('',encoding='utf-8')
    ev.run({},tmp_path,{'ES2002':gold,'ES2003':gold},tmp_path/'out',live=True)


def test_loader_allowlist_contains_only_two_authorized_teams():
    assert ev.ALLOWED==tuple(f'{team}{p}' for team in ('ES2002','ES2003') for p in 'abcd')
    with pytest.raises(ValueError,match='Unknown task_extraction_mode'):
        ev.predict({},None,task_extraction_mode='invalid')


def test_identical_high_population_reuses_current_meeting_judgment_only(tmp_path,monkeypatch):
    import random
    client=ev.Client(ev.ev.Counter(24,0.6),live=False)
    client.output_dir=tmp_path
    client.resume_source=tmp_path/'old'
    client.resume_plan=[('ES2002a','linkloom')]
    client.saved_predictions={'ES2002a':{'baseline':[{'task':'Ship package','confidence':'HIGH'}],
                                       'linkloom':[{'task':'Ship package','confidence':'HIGH'}]}}
    client.identical_population_reuse=[]
    left=random.Random('20261029:ES2002a:baseline');right=random.Random('20261029:ES2002a:linkloom')
    original=f'p-{left.getrandbits(64):016x}';expected=f'p-{right.getrandbits(64):016x}'
    ev.ev.new_json(tmp_path/'ES2002a.baseline.judge.json',{'judgments':[{'prediction_id':original,'category':'MATCH','gold_id':'g'}]})
    monkeypatch.setattr(ev.ev.Client,'generate',lambda *a:pytest.fail('No model call for identical population'))
    payload=client.generate(ev.ev.DEEPSEEK,'\nDATA:\n'+json.dumps({'meeting':{'meeting_id':'ES2002a'}}),{},32768)
    assert payload['judgments'][0]=={'prediction_id':expected,'category':'MATCH','gold_id':'g'}
    assert client.counter.requests==0 and client.identical_population_reuse==['ES2002a']


def test_high_subset_with_low_is_separately_judged(tmp_path,monkeypatch):
    client=ev.Client(ev.ev.Counter(24,0.6),live=False)
    client.output_dir=tmp_path;client.resume_source=tmp_path/'old'
    client.resume_plan=[('ES2003c','linkloom')];client.identical_population_reuse=[]
    client.saved_predictions={'ES2003c':{'baseline':[{'task':'One','confidence':'HIGH'},{'task':'Two','confidence':'LOW'}],
                                       'linkloom':[{'task':'One','confidence':'HIGH'}]}}
    ev.ev.new_json(tmp_path/'ES2003c.baseline.judge.json',{'judgments':[]})
    calls=[]
    def generated(*args):calls.append(1);return {'judgments':[]}
    monkeypatch.setattr(ev.ev.Client,'generate',generated)
    client.generate(ev.ev.DEEPSEEK,'\nDATA:\n'+json.dumps({'meeting':{'meeting_id':'ES2003c'}}),{},32768)
    assert len(calls)==1 and client.identical_population_reuse==[]


@pytest.mark.parametrize('changed', ['source','roster','prediction','gold'])
def test_resume_rejects_changed_inputs_before_cached_judgments(tmp_path,changed):
    from copy import deepcopy
    gold=tmp_path/'gold.jsonl';gold.write_text('first gold',encoding='utf-8')
    meetings=[{'meeting_id':'ES2002a','text':'Original text','speakers':ROSTER}]
    predictions={'ES2002a':{'baseline':[{'task':'Work','confidence':'HIGH'}]}}
    original=ev.resume_manifest(meetings,predictions,{'ES2002':gold})
    ev.ev.new_json(tmp_path/'resume_manifest.json',original)
    ev.validate_resume_manifest(tmp_path,original)
    meetings,predictions=deepcopy(meetings),deepcopy(predictions)
    if changed=='source':meetings[0]['text']='Changed text'
    if changed=='roster':meetings[0]['speakers'][0]['role']='Different role'
    if changed=='prediction':predictions['ES2002a']['baseline'][0]['task']='Different work'
    if changed=='gold':gold.write_text('changed gold',encoding='utf-8')
    with pytest.raises(ValueError,match='cannot be reused'):
        ev.validate_resume_manifest(tmp_path,ev.resume_manifest(meetings,predictions,{'ES2002':gold}))


def test_resume_without_frozen_inputs_fails_closed(tmp_path):
    with pytest.raises(FileNotFoundError):ev.validate_resume_manifest(tmp_path,{})
