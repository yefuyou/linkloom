"""Scorer fault probes use invented Dev records, never holdout inputs or Gold."""
from scripts.audit_m2_final import reason_check, exact_change_check, candidate_coverage


def test_correct_status_with_wrong_reason_fails():
    row=dict(assessment=dict(verdict='UNKNOWN',reason_codes=['UNRELATED'],missing_evidence=['missing direct bridge']),
             comparison=dict(status='BLOCKED',reason_codes=['UNRELATED'],delta_calendar_days=None))
    expected=dict(verdict='UNKNOWN',codes=['MISSING_DIRECT_BRIDGE'],comparison='BLOCKED',
                  comparison_codes=['MISSING_DIRECT_BRIDGE'])
    assert not all(reason_check(row,expected).values())


def test_change_endpoint_cannot_borrow_other_valid_source_version():
    evidence=dict(span=dict(artifact_version_id='v1',ordinal=0),quote='synthetic source')
    snapshot=dict(sources=[dict(artifact_version_id='v1')],evidence=[evidence])
    public=dict(workspace_id='dev',change=dict(source_index=0,segment_index=0,project='P',before='A',after='B'))
    endpoint=dict(workspace_id='dev',project='P',before_vendor='A',after_vendor='B',kind='SOURCE_CLAIM',
        decision_id=None,candidate_id=None,receipt_id=None,evidence=dict(span=dict(artifact_version_id='v2',ordinal=0),quote='synthetic source'))
    assessment=dict(object=endpoint,dependencies=[dict(span=evidence['span'],source_status='CURRENT',readback_ok=True,observation_valid=True)],
        authority_checks=['SOURCE_CLAIM_ONLY','FORMAL_AUTHORITY_NOT_ESTABLISHED'])
    assert not exact_change_check(assessment,snapshot,public)['exact_evidence']


def test_candidate_recall_uses_independent_public_endpoints():
    from tests.task_candidate_support import load_case, public_cases
    from linkloom.semantic_ingestion.task_relation_models import ObservationRef
    case=next(c for c in public_cases() if c['case_id']=='C02')
    _,observations,_,_,_=load_case(case)
    labels={'subject':observations[0],'target':observations[1],'wrong':observations[2]}
    public=dict(case_id='dev',questions=[dict(question_id='dev-q',subject='subject',object='target',predicate='RELATED_TO')])
    snapshot=dict(observations={k:o.to_dict() for k,o in labels.items()})
    bad=dict(subject=ObservationRef.from_observation(labels['subject']).to_dict(),
        object=ObservationRef.from_observation(labels['wrong']).to_dict(),predicate_hint='RELATED_TO')
    output=dict(predictions=[dict(question_id='dev-q',assessment=bad,discovery=dict(candidates=[bad]))])
    gold=dict(questions=[dict(question_id='dev-q',verdict='SUPPORTED',evaluation_snapshots=['s0'])])
    assert not candidate_coverage(public,gold,output,snapshot)[0]['predicate_hint_recalled']
    public['questions'].append(dict(public['questions'][0],question_id='dev-second'))
    gold['questions'].append(dict(gold['questions'][0],question_id='dev-second'))
    output['predictions'].append(dict(output['predictions'][0],question_id='dev-second'))
    assert len(candidate_coverage(public,gold,output,snapshot))==2
    gold['questions'][1]['evaluation_snapshots']=['s1','s2']
    assert len(candidate_coverage(public,gold,output,snapshot))==3
