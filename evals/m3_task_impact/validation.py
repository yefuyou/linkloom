"""Static annotation audit only; does not execute or score either split."""
from collections import Counter
import json

from .fixtures import ROOT, digest, source_index, verify_proof


def validate_dataset():
    ids,workspaces,source_hashes,project_names,source_identities = set(),set(),set(),set(),set()
    result = dict(validation='STATIC_ONLY',splits={},holdout_executed=False,model_calls=0)
    for split,expected_count in (('dev',12),('holdout',4)):
        data = json.loads((ROOT/'data'/split/'inputs.json').read_text(encoding='utf8'))
        annotated = json.loads((ROOT/'gold'/f'{split}.json').read_text(encoding='utf8'))
        assert data['schema']=='m3-input/v1' and annotated['schema']=='m3-gold/v1'
        assert data['split']==annotated['split']==split
        cases,gold = data['cases'],annotated['cases']
        assert len(cases)==len(gold)==expected_count
        assert {c['case_id'] for c in cases}=={c['case_id'] for c in gold}
        by_id = {c['case_id']:c for c in gold}
        split_hashes,split_projects,split_sources = set(),set(),set()
        totals = Counter()
        for case in cases:
            assert set(case)=={'case_id','workspace_id','snapshot','question','sources'}
            assert case['case_id'] not in ids and case['workspace_id'] not in workspaces
            ids.add(case['case_id']); workspaces.add(case['workspace_id'])
            current = Counter()
            for source in case['sources']:
                assert set(source)=={'doc_id','artifact_id','content','current'}
                assert type(source['current']) is bool
                if source['current']:
                    current[source['artifact_id']] += 1
                split_hashes.add(digest(source['content']))
                split_sources.add((case['workspace_id'],source['artifact_id']))
            assert all(n==1 for n in current.values())
            _,spans,current_sources = source_index(case)
            annotation = by_id[case['case_id']]
            assert annotation['rationale'] and annotation['tags']
            assert annotation['discovery_scope']=='CURRENT_OBSERVATIONS'
            all_text = ''.join(s['content'] for s in case['sources'])
            assert all(name in all_text for name in annotation['project_names'])
            split_projects.update(annotation['project_names'])
            required = set()
            for section in ('tasks','relations','revisions'):
                seen = set()
                for row in annotation[section]:
                    key = row['ref'] if section=='tasks' else (row.get('predicate'),row['subject'],row['object'])
                    assert key not in seen
                    seen.add(key)
                    assert row['evidence'] and all(ref in spans for ref in row['evidence'])
                    if section=='relations' and row['predicate']=='AFFECTED_BY':
                        assert row['object'].startswith('change:')
                        assert row['object'].removeprefix('change:') in row['evidence']
                    required.update(row['evidence'])
                totals[section] += len(annotation[section])
            assert set(annotation['evidence_catalog'])==required
            assert all(verify_proof(case,proof) for proof in annotation['evidence_catalog'].values())
            for row in annotation['tasks']:
                assert row['ref'] in row['evidence']
                assert current_sources[row['ref'].rsplit(':',1)[0]]
                assert row['task'] in spans[row['ref']].quote
                if row['owner']:
                    assert row['owner'] in next(s['content'] for s in case['sources'] if s['doc_id']==row['ref'].rsplit(':',1)[0])
            for ref in annotation['discovery_exclusions']:
                assert ref in spans and not current_sources[ref.rsplit(':',1)[0]]
                assert ref not in {t['ref'] for t in annotation['tasks']}
            relation_keys = {(r['predicate'],r['subject'],r['object']) for r in annotation['relations']}
            for row in annotation['revisions']:
                assert all((p,row['subject'],row['object']) in relation_keys for p in ('SAME_TASK','REVISES_PLAN'))
        assert not split_hashes & source_hashes
        assert not split_projects & project_names
        assert not split_sources & source_identities
        source_hashes.update(split_hashes)
        project_names.update(split_projects)
        source_identities.update(split_sources)
        result['splits'][split] = dict(cases=len(cases),**totals,input_sha256=digest(data),gold_sha256=digest(annotated))
    return result
