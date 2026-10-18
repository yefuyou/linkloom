from evals.m3_task_impact.reporting import summarize


def test_aggregate_uses_counts_excludes_unrun_and_keeps_scripted_separate():
    def scored(scope,n,d):
        return dict(status='SCORED',scope=scope,failure_stage=None,
                    metrics=dict(task_discovery_recall=dict(numerator=n,denominator=d,value=n/d)),
                    counts={})
    report = summarize([scored('SCRIPTED_WIRING_ONLY',1,1),
        scored('OFFLINE_ADAPTER_OUTPUT',1,2),scored('OFFLINE_ADAPTER_OUTPUT',2,3),
        dict(status='NOT_EVALUATED',metrics=None,failure_stage='capability_gap')])
    assert report['statuses']=={'SCORED':3,'NOT_EVALUATED':1}
    assert report['by_scope']['SCRIPTED_WIRING_ONLY']['metrics']['task_discovery_recall']['value']==1
    actual = report['by_scope']['OFFLINE_ADAPTER_OUTPUT']['metrics']['task_discovery_recall']
    assert actual==dict(numerator=3,denominator=5,value=.6)


def test_all_unrun_has_no_business_scores():
    report = summarize([dict(status='NOT_EVALUATED',metrics=None,failure_stage='capability_gap')])
    assert report['by_scope']=={}
    assert report['evaluated_cases']==0
