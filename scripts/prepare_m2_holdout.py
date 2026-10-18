"""Materialize the already published H01-H04 design, before predictions exist.

This is fixture authoring, not a production extraction/Agent implementation.
Never import this module from the prediction process (it writes evaluator Gold).
"""
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'tests/fixtures/m2_final'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main(dest=DEST):
    spec = (ROOT / 'M2_EVAL_SPEC.md').read_text(encoding='utf8')
    cases = []
    for ident in ('H01', 'H02', 'H03', 'H04'):
        section = spec.split('### ' + ident + ' ', 1)[1].split('\n### ', 1)[0]
        block = section.split('```text\n', 1)[1].split('```', 1)[0]
        sources, lines = {}, {}
        for line in block.splitlines():
            match = re.fullmatch(r'([AB]\.md)(?:/v([12]))? ([AB]\d) (.+)', line)
            if not match:
                raise ValueError('unrecognized published source line')
            name, version, label, text = match.groups()
            key = name + ('/v' + version if version else '')
            sources.setdefault(key, dict(artifact_id=name,
                source_type='timestamped_text' if text.startswith('[') else 'manual_text', content=''))
            sources[key]['content'] += text + '\n'
            lines[(key, label)] = re.sub(r'^\[[^]]+\] [^:]+: ', '', text)
        if ident == 'H01':
            for source in sources.values():
                source['metadata'] = dict(document_date='2026-10-15')
        if ident == 'H03':
            sources['A.md']['metadata'] = dict(source_updated_at='2026-10-10T09:00:00+08:00')
            sources['B.md']['metadata'] = dict(source_updated_at='2026-10-15T09:00:00+08:00')
        observations = []
        keys = {'H01': [('A.md', 'A1', '10月14日'), ('B.md', 'B1', '10月18日')],
                'H02': [('B.md', 'B1', '2026年10月18日'), ('B.md', 'B2', '2026年10月18日'), ('B.md', 'B3', '2026年10月18日')],
                'H03': [('A.md', 'A1', '2026年10月14日'), ('B.md', 'B1', '2026年10月18日')],
                'H04': [('A.md/v1', 'A1', '2026年11月20日'), ('B.md/v1', 'B1', '2026年11月22日'), ('B.md/v2', 'B1', '2026年11月20日')]}[ident]
        for source, label, deadline in keys:
            raw = lines[(source, label)]
            if ident=='H03':
                # A2/B2 are continuation lines of the same trusted utterance,
                # not a new invented observation or an extra speaker.
                raw += '\n' + lines[(source,label[0]+'2')]
            observations.append(dict(label=source + ':' + label, source_index=list(sources).index(source),
                segment_index=int(label[1:])-1,
                task=dict(text=raw, description_parts=[raw], owner=None if ident=='H01' else '陈晨',
                          project='星河项目', vendor=None, deadline_text=deadline)))
        case = dict(case_id=ident, workspace_id='m2-final-' + ident.lower(),
                    sources=list(sources.values()), observations=observations)
        if ident == 'H02':
            case['change'] = dict(source_index=0, segment_index=0, project='星河项目', before='云桥科技', after='星帆通信')
            case['questions'] = [dict(question_id=ident+'-'+str(i), predicate='AFFECTED_BY', subject=o['label'])
                                 for i,o in enumerate(observations, 1)]
        else:
            pairs = [(observations[1]['label'], observations[0]['label'])]
            if ident == 'H04':
                pairs.append((observations[2]['label'], observations[0]['label']))
            case['questions'] = [dict(question_id=ident+'-'+str(i), predicate='REVISES_PLAN', subject=new, object=old)
                                 for i,(new,old) in enumerate(pairs,1)]
        cases.append(case)
    write(dest/'inputs/holdout.json', dict(schema='m2-final-input/v1', split='holdout', cases=cases))
    # Annotations transcribed from the original design; no system output exists.
    gold = dict(schema='m2-final-gold/v1', split='holdout', author_visible=True, cases=[
        dict(case_id='H01', questions=[dict(question_id='H01-1', same_task='SUPPORTED', verdict='SUPPORTED',
            comparison='NOT_COMPARABLE', delta=None, changed_fields=['deadline'], required_proof_sources=['A.md','B.md'])],
            observation_fields=dict(owner=None, deadline=None, deadline_precision='MONTH_DAY', deadline_role='TASK_DEADLINE')),
        dict(case_id='H02', questions=[dict(question_id='H02-'+str(i), verdict=v,
            required_proof_sources=['A.md','B.md'] if v=='SUPPORTED' else ['B.md'] if v=='REFUTED' else [])
            for i,v in enumerate(['SUPPORTED','REFUTED','UNKNOWN'],1)], authority='SOURCE_CLAIM_ONLY', formal_decisions=0),
        dict(case_id='H03', questions=[dict(question_id='H03-1', same_task='SUPPORTED', verdict='CONFLICT',
            comparison='BLOCKED', delta=None, changed_fields=[], required_proof_sources=['A.md','B.md'])]),
        dict(case_id='H04', questions=[
            dict(question_id='H04-1', same_task='SUPPORTED', verdict='SUPPORTED', comparison='COMPUTABLE', delta=2,
                 old='2026-11-20', new='2026-11-22', bound='BEFORE', changed_fields=['deadline'], required_proof_sources=['A.md','B.md']),
            dict(question_id='H04-2', same_task='SUPPORTED', verdict='REFUTED', comparison='NOT_APPLICABLE', delta=None,
                 changed_fields=[], required_proof_sources=['B.md'])],
            lifecycle=dict(s1_old_evidence='STALE', s2_old_revision='RETRACTED', historical_delta=2, current_delta=None))])
    decisive = {
        'H01-1': ['替代其原计划', '改为10月18日前完成'],
        'H02-1': ['由于A.md记载的供应商更换', '此任务必须补测签名兼容性'],
        'H02-2': ['AUTH-4无需因A.md的此次供应商更换做任何调整'],
        'H02-3': [],
        'H03-1': ['本条是执行计划，替代A.md的14日计划', 'B.md的18日版本是草稿，不替代本条14日计划'],
        'H04-1': ['本条替代原计划', '将2026年11月20日前完成改为2026年11月22日前完成'],
        'H04-2': ['任务计划未曾修订'],
    }
    for case, annotation in zip(cases,gold['cases']):
        for question in annotation['questions']:
            question['decisive_fragments'] = []
            for fragment in decisive[question['question_id']]:
                matches=[i for i,s in enumerate(case['sources']) if fragment in s['content']]
                if len(matches)!=1:
                    raise ValueError('decisive proof source is ambiguous')
                question['decisive_fragments'].append(dict(source_index=matches[0],quote=fragment))
            question['evaluation_snapshots'] = ['s1','s2'] if question['question_id']=='H04-2' else ['s0']
            public = next(q for q in case['questions'] if q['question_id']==question['question_id'])
            observations = {o['label']:o for o in case['observations']}
            refs = [public['subject']] + ([public['object']] if 'object' in public else [])
            question['required_evidence'] = [dict(source_index=observations[ref]['source_index'],
                quote=observations[ref]['task']['text']) for ref in refs]
            if case['case_id']=='H02' and question['verdict']=='SUPPORTED':
                statement=re.sub(r'^\[[^]]+\] [^:]+: ', '',case['sources'][0]['content'].strip())
                question['required_evidence'].append(dict(source_index=0,quote=statement))
    write(dest/'gold/holdout.json', gold)


if __name__ == '__main__':
    from argparse import ArgumentParser
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEST)
    main(parser.parse_args().output)
