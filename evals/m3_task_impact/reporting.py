"""Count-weighted summaries, with Scripted and other adapter outputs separated."""
from collections import Counter, defaultdict

from .scorer import fraction


def _metrics(rows):
    names = set().union(*(row.keys() for row in rows)) if rows else set()
    result = {}
    for name in sorted(names):
        values = [r[name] for r in rows if name in r]
        if all(isinstance(v,dict) and 'numerator' in v and 'denominator' in v for v in values):
            result[name] = fraction(sum(v['numerator'] for v in values),sum(v['denominator'] for v in values))
        else:
            result[name] = _metrics(values)
    return result


def summarize(results):
    groups = defaultdict(list)
    for row in results:
        if row['status']=='SCORED':
            groups[row['scope']].append(row)
    summaries = {}
    for scope,rows in sorted(groups.items()):
        counts = Counter()
        for row in rows:
            counts.update(row['counts'])
        summaries[scope] = dict(cases=len(rows),metrics=_metrics([r['metrics'] for r in rows]),counts=dict(counts))
    return dict(cases=len(results),evaluated_cases=sum(len(rows) for rows in groups.values()),
                statuses=dict(Counter(r['status'] for r in results)),
                failure_stages=dict(Counter(r['failure_stage'] for r in results if r.get('failure_stage'))),
                by_scope=summaries)
