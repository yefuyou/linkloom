"""Evaluation wiring boundaries using tiny invented unit fixtures, not corpus data."""
import json
from pathlib import Path

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.agents.providers.gemini_api import build_gemini_request
from linkloom.semantic_ingestion.meeting_tasks_v2 import TOOL_ID
from linkloom.tools.contracts import ToolCall
from tests.unit.test_eval_tasks import ev


def test_v2_harness_exact_multi_quotes_owner_and_deadline_are_preserved(tmp_path):
    bodies = ["I will ship the package.", "Please ship it before the next workshop."]
    text, utterances = "", []
    for i, body in enumerate(bodies):
        line = f"X [{i:.3f}-{i+1:.3f}]: {body}\n"
        utterances.append({"segment_id":f"s{i}","speaker":"X","start":i,"end":i+1,
            "text":body,"char_start":len(text),"char_end":len(text)+len(line)})
        text += line
    meeting = {"meeting_id":"ES2002a","text":text,"utterances":utterances,
        "speakers":[{"speaker":"X","participant_id":"person-x","role":"Coordinator"}]}
    class FakeClient:
        output_dir=tmp_path
        gemini_max_input_bytes=196608
        calls=0
        def complete(self,request):
            self.calls+=1
            build_gemini_request(request,max_input_bytes=self.gemini_max_input_bytes)
            assert "keep" not in request.user_input and "ami_actions" not in request.user_input
            return ModelResponse(action=ModelAction.tool(ToolCall(call_id="offline",tool_id=TOOL_ID,
                arguments={"coverage_complete":True,"tasks":[{"task":"Ship the package","owner_ids":["X"],
                    "deadline":{"raw_text":"before the next workshop","kind":"EVENT_BASED"},"adopted":True,
                    "scope":"AFTER_MEETING","end_state":"PENDING","deliverable_key":"package shipment",
                    "reason":"A promise and the related delivery deadline.","evidence":[
                        {"message_ordinal":0,"supports":["work","owner"]},
                        {"message_ordinal":1,"supports":["deadline"]}]}]})))
    client=FakeClient()
    predictions, diagnostics = ev.linkloom_predict(meeting,client,task_extraction_mode="meeting_v2")
    assert client.calls==1 and len(diagnostics)==1
    p,=predictions
    assert p["quote"]==bodies[0] and p["evidence_quotes"]==bodies
    assert p["owner"]=="Coordinator" and p["deadline"]=="before the next workshop"
    assert p["deadline_kind"]=="EVENT_BASED" and p["resolved_at"] is None
    raw=json.loads((tmp_path/"ES2002a.v2-discovery.json").read_text('utf8'))
    assert raw["candidates"][0]["deadline"]["resolved_at"] is None
    assert all(e["span"]["quote"] in text for e in raw["candidates"][0]["evidence"])


def test_data_loader_reads_only_four_approved_transcripts(monkeypatch):
    reads=[]
    def read(path,**kwargs):
        reads.append(str(path))
        return json.dumps({"meeting_id":path.stem})
    monkeypatch.setattr(Path,"read_text",read)
    meetings=ev.load_meetings(Path("fixture"))
    assert [m["meeting_id"] for m in meetings]==["ES2002a","ES2002b","ES2002c","ES2002d"]
    assert len(reads)==4 and all("transcripts" in p and "ES2002" in p for p in reads)


def test_unknown_extraction_mode_cannot_fall_back_to_billable_legacy():
    class Never:
        def complete(self,request):
            raise AssertionError("No request allowed")
    import pytest
    with pytest.raises(ValueError,match="task_extraction_mode"):
        ev.linkloom_predict({},Never(),task_extraction_mode="unknown")


def test_offline_rebuild_reuses_judgment_only_for_safe_deadline_abstention(tmp_path, monkeypatch):
    import random
    import hashlib
    body="I will ship the package inside the spreadsheet."
    line="X [0.000-1.000]: "+body+"\n"
    meeting={"meeting_id":"ES2002a","text":line,"utterances":[{"segment_id":"s0","speaker":"X",
        "start":0,"end":1,"text":body,"char_start":0,"char_end":len(line)}],
        "speakers":[{"speaker":"X","participant_id":"person-x","role":"Coordinator"}]}
    payload={"coverage_complete":True,"tasks":[{"task":"Ship the package","owner_ids":["X"],
        "deadline":{"raw_text":"inside the spreadsheet","kind":"EVENT_BASED"},"adopted":True,
        "scope":"AFTER_MEETING","end_state":"PENDING","deliverable_key":"package shipment",
        "reason":"Explicit undertaking; the model mislabelled its storage medium.","evidence":[{
            "message_ordinal":0,"supports":["work","owner","deadline"]}]}]}
    class Saved:
        output_dir=tmp_path/'scratch'
        gemini_max_input_bytes=196608
        def complete(self,request):
            return ModelResponse(action=ModelAction.tool(ToolCall(call_id='saved',tool_id=TOOL_ID,arguments=payload)))
    Saved.output_dir.mkdir()
    current,_=ev.meeting_v2_predict(meeting,Saved())
    old=[{**current[0],"deadline":"inside the spreadsheet","deadline_kind":"EVENT_BASED"}]
    source=tmp_path/'saved';source.mkdir()
    ev.new_json(source/'predictions.json',{'predictions':{'ES2002a':{'baseline':[],'linkloom':old}}})
    ev.new_json(source/'ES2002a.v2-discovery.json',{'responses':[payload]})
    translations=[{'gold_id':'g1','task_zh':'寄送包裹','quote_zh':'我会寄送包裹。'}]
    ev.new_json(source/'ES2002a.baseline.judge.json',{'judgments':[],'gold_translations':translations})
    pid=f"p-{random.Random('20261029:ES2002a:linkloom').getrandbits(64):016x}"
    ev.new_json(source/'ES2002a.linkloom.judge.json',{'judgments':[{'prediction_id':pid,'gold_id':'g1',
        'category':'MATCH','owner_correct':True,'deadline_correct':False,'reason':'任务一致，无明确截止时间。',
        'evidence_quote':body,'prediction_task_zh':'寄送包裹','evidence_quote_zh':'我会寄送包裹。'}],
        'gold_translations':translations})
    gold=tmp_path/'gold.jsonl'
    gold.write_text(json.dumps({'gold_id':'g1','meeting_id':'ES2002a','quote':body,'task':'Ship the package',
        'owner':'Coordinator','deadline':ev.UNKNOWN,'notes':''})+'\n',encoding='utf8')
    ev.new_json(source/'results.json',{'gold_sha256':hashlib.sha256(gold.read_bytes()).hexdigest()})
    ev.new_json(source/'ES2002a.v2-request-0.json',json.loads((Saved.output_dir/'ES2002a.v2-request-0.json').read_text('utf8')))
    monkeypatch.setattr(ev,'load_meetings',lambda path:[meeting])
    def forbidden(*args,**kwargs):
        raise AssertionError('Offline reconstruction must never call a provider')
    monkeypatch.setattr(ev.Client,'generate',forbidden)
    monkeypatch.setattr(ev.Client,'complete',forbidden)
    out=tmp_path/'rebuilt'
    ev.rebuild_meeting_v2(tmp_path,gold,source,out)
    report=json.loads((out/'results.json').read_text('utf8'))
    audit=json.loads((out/'offline_rebuild.json').read_text('utf8'))
    assert report['counter']['requests']==0 and report['actual_cost_usd_from_usage']==0
    assert report['micro']['linkloom']['tp']==1 and len(audit['changed_fields'])==1
    assert audit['changed_fields'][0]['new_deadline']==ev.UNKNOWN
    old[0]['task']='A different obligation'
    (source/'predictions.json').write_text(json.dumps({'predictions':{'ES2002a':{'baseline':[],'linkloom':old}}}),encoding='utf8')
    import pytest
    with pytest.raises(ValueError,match='non-deadline'):
        ev.rebuild_meeting_v2(tmp_path,gold,source,tmp_path/'denied')
    gold.write_text(gold.read_text('utf8').replace('Coordinator','Other role'),encoding='utf8')
    with pytest.raises(ValueError,match='gold changed'):
        ev.rebuild_meeting_v2(tmp_path,gold,source,tmp_path/'changed-gold')
