"""Build only public DEV source/confirmation inputs; never hand Gold to confirmers."""
from pathlib import Path
import argparse,hashlib,json
from copy import deepcopy
from tests.meeting_graph_eval_support import build_meetings,build_meridian


def prepare(dataset,bindings,freeze,database,public_output,index_output):
    dataset,bindings,freeze,database=map(Path,(dataset,bindings,freeze,database))
    frozen=json.loads(freeze.read_text(encoding='utf8'))
    for key,path in [('questions',dataset),('fixture_bindings',bindings)]:
        if hashlib.sha256(path.read_bytes()).hexdigest()!=frozen[key]['sha256']:raise ValueError('FROZEN_DATA_CHANGED')
    if database.exists():raise ValueError('NEW_EVALUATION_DATABASE_REQUIRED')
    # This boundary discards questions/expected before calling either confirmer.
    fixtures=deepcopy(json.loads(dataset.read_text(encoding='utf-8-sig'))['fixtures'])
    root=Path(__file__).resolve().parents[2];dev=root/'tests/fixtures/bounded_multihop/dev.json'
    bound=json.loads(bindings.read_text(encoding='utf-8-sig'))
    if hashlib.sha256(dev.read_bytes()).hexdigest()!=bound['source_fixture_sha256']:raise ValueError('BOUND_DEV_CHANGED')
    meetings=build_meetings(fixtures['graph-dev-meetings'],database,recorded_at='2026-11-03T00:00:00+08:00')
    meridian=build_meridian(fixtures['meridian'],database)
    case_workspace=meridian['workspaces'][('meridian',fixtures['meridian']['workspace'])]
    cases=[dict(case_id='meridian',workspace_id=fixtures['meridian']['workspace'],confirmed_workspace_id=case_workspace,
        documents=[dict(workspace_id=fixtures['meridian']['workspace'],path=s['id']+'.md',content=s['text']) for s in fixtures['meridian']['sources']]),
        dict(case_id='graph-dev-meetings',workspace_id=fixtures['graph-dev-meetings']['workspace'],confirmed_workspace_id=meetings['workspace_id'],
        documents=[dict(workspace_id=fixtures['graph-dev-meetings']['workspace'],path=m['id']+'.md',content='\n'.join(m['lines']))
            for m in fixtures['graph-dev-meetings']['meetings']])]
    for case in json.loads(dev.read_text(encoding='utf8'))['cases']:
        cases.append(dict(case_id=case['id'],workspace_id=case['workspace_id'],documents=deepcopy(case['documents'])))
    public=dict(split='dev',confirmation_label='模拟人工确认',cases=cases,source_fixture_sha256=hashlib.sha256(dev.read_bytes()).hexdigest())
    index=dict(meetings=meetings,meridian=dict(workspace_id=case_workspace,confirmed=meridian['confirmed']))
    for path,value in [(public_output,public),(index_output,index)]:
        with Path(path).open('x',encoding='utf8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)
    return dict(workspaces=2,meetings=len(meetings['decisions']),tasks=len(meetings['tasks']),regression_cases=len(cases)-2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for key in ('dataset','bindings','freeze','database','public_output','index_output'):parser.add_argument('--'+key.replace('_','-'),required=True,type=Path)
    args=vars(parser.parse_args());print(json.dumps(prepare(**args)))

if __name__=='__main__':main()
