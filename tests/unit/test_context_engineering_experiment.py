from dataclasses import replace

from benchmarks.context_engineering.fixtures import scenarios
from benchmarks.context_engineering.run import experiment, run_strategy


def test_synthetic_comparison_exposes_both_gains_and_missing_sources():
    report = experiment(len, 'FAKE_CHAR_TOKENIZER_FOR_TEST_ONLY')
    a, b, c = (report['summary'][s] for s in ('A', 'B', 'C'))
    assert c['required_evidence_coverage'] > b['required_evidence_coverage'] > a['required_evidence_coverage']
    assert c['counter_retention'] == 1
    assert c['restore_successes'] == 2 and c['restore_attempts'] == 4
    assert c['context_loss_refusal_probes'] == 1
    missing = next(r for r in report['cases'] if r['case'] == 'source_update_and_invalidation' and r['strategy'] == 'C')
    assert missing['recovery_status'] == 'incomplete'
    assert set(missing['recovery_failures']) == {'SOURCE_VERSION_MISMATCH', 'SOURCE_NOT_FOUND'}
    assert missing['required_evidence_coverage'] == 0


def test_selection_cannot_read_gold_labels_or_evaluator_requirements():
    case = scenarios()[0]
    renamed = replace(case, candidates=tuple(replace(p, label='opaque-' + str(i)) for i, p in enumerate(case.candidates)))
    first = run_strategy(case, 'C', len)
    second = run_strategy(renamed, 'C', len)
    assert [p.ref for p in first.selected] == [p.ref for p in second.selected]
    assert first.context == second.context
