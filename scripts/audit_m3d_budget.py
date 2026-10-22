"""Read current sibling campaign journals; never send requests or edit them."""
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

DOC = Path('docs/requirements/m3-d-combined-acceptance')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def audit():
    sibling = Path.cwd().parent
    known = [*(sibling/'m3s/work').glob('m3s-live*/global-provider/budget.json'),
             *(sibling/'m3s1/work').glob('m3s1-live*/global-provider/budget.json'),
             *(sibling/'m3s2/work').glob('m3s2*/provider/budget.json'),
             sibling/'m3s2/work/m3s2-product/campaign-provider/budget.json',
             sibling/'m3s2/work/m3s2-followup/f-provider/budget.json',
             sibling/'m3s2/work/m3s2-followup/v3-unattempted/f-provider/budget.json',
             sibling/'m3s2/work/m3s2-followup/product/campaign-provider/budget.json']
    assert len(known) == 13 and all(p.exists() for p in known), 'MISSING_CURRENT_JOURNAL'
    rows = []
    for path in sorted(known):
        data = read(path)
        assert len(data['attempts']) == data['requests_reserved']
        rows.append(dict(path=str(path.resolve()), sha256=digest(path),
            requests=data['requests_reserved'], reserved_usd=data['cost_reserved_usd'],
            allocation_usd=data['limits']['max_cost_usd'], started_at=data.get('started_at'),
            outcomes=[a['outcome'] for a in data['attempts']], failure=data['failure']))
    # The predecessor checkpoint already includes 47 requests/$13.93. The
    # thirteen current journals are S (22), S1 (10), S2 (16+7), counted once.
    current_requests = 47 + sum(r['requests'] for r in rows)
    current_cost = round(13.93 + sum(r['reserved_usd'] for r in rows), 8)
    assert current_requests == 102 and current_cost == 27.68, 'CHECKPOINT_CHANGED_RECONCILE_BEFORE_PAYING'
    # Retain even unspent historical allocations conservatively. Do not release
    # failed/unknown costs, or count Product's nested per-question copy twice.
    # Later phases inherit the remaining campaign budget; summing those limits
    # would count the same allocation repeatedly. Hold each original envelope.
    groups = [dict(name='M3-S', max_usd=8,
                  rows=[r for r in rows if '/m3s/' in Path(r['path']).as_posix()]),
               dict(name='M3-S1', max_usd=3,
                    rows=[r for r in rows if '/m3s1/' in Path(r['path']).as_posix()]),
               dict(name='M3-S2 initial', max_usd=5,
                    rows=[r for r in rows if '/m3s2/' in Path(r['path']).as_posix()
                          and 'm3s2-followup' not in r['path']]),
               dict(name='M3-S2 followup', max_usd=2,
                    rows=[r for r in rows if 'm3s2-followup' in r['path']])]
    envelopes = [dict(name=g['name'], max_usd=g['max_usd'],
        reserved_usd=sum(r['reserved_usd'] for r in g['rows'])) for g in groups]
    unused = round(sum(max(0, g['max_usd']-g['reserved_usd']) for g in envelopes), 8)
    roots = {sibling.parent, *sibling.iterdir()}
    listing = subprocess.check_output(['git', 'worktree', 'list', '--porcelain'], text=True, encoding='utf8')
    roots.update(Path(line[9:]) for line in listing.splitlines() if line.startswith('worktree '))
    candidates = set()
    patterns = ('work/*/global-provider/budget.json', 'work/*/campaign-provider/budget.json',
                'work/*/provider/budget.json', 'work/*/*/f-provider/budget.json',
                'work/*/*/campaign-provider/budget.json',
                'work/*/work/investigations/*/provider/budget.json')
    for root in sorted(roots):
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in root.glob(pattern):
                relative = path.relative_to(root).parts
                if any(p.startswith(('pytest', 'test_', 'review')) or 'replay' in p for p in relative):
                    continue
                candidates.add(path.resolve())
    known_paths = {p.resolve() for p in known}
    known_requests = {a['request_sha256'] for path in known for a in read(path)['attempts']}
    other = []
    latest_known = max(r['started_at'] for r in rows if r['started_at'])
    for path in sorted(candidates-known_paths):
        data = read(path)
        # Current Integration D is separately capped, so exclude its own ledger.
        own = Path.cwd() in path.parents
        after = bool(data.get('started_at') and data['started_at'] > latest_known)
        nested_copy = bool(data.get('attempts')) and all(a['request_sha256'] in known_requests for a in data['attempts'])
        other.append(dict(path=str(path), sha256=digest(path), requests=data.get('requests_reserved'),
            reserved_usd=data.get('cost_reserved_usd'), allocation_usd=data.get('limits', {}).get('max_cost_usd'),
            started_at=data.get('started_at'), own_integration_d=own,
            newer_than_checkpoint=after, nested_copy_of_known_campaign=nested_copy, failure=data.get('failure')))
    unknown_new = [r for r in other if r['newer_than_checkpoint'] and not r['own_integration_d']]
    # Unknown concurrent campaigns must be reconciled, not silently called free.
    assert not unknown_new, 'NEW_CONCURRENT_CAMPAIGNS_REQUIRE_RECONCILIATION: '+str(unknown_new)
    # Pre-checkpoint M3Q/M3R Product journals are terminated per-question caps,
    # including documented scripted runs. Their max_cost_usd is not a standing
    # allocation to send new requests. Actual/unknown reservations remain in the
    # canonical 47/$13.93 checkpoint. Only new campaigns require new capacity.
    for row in other:
        row['capacity_policy'] = ('NESTED_COPY_COUNTED_ONCE' if row['nested_copy_of_known_campaign'] else
            'OWN_D_SEPARATELY_CAPPED' if row['own_integration_d'] else
            'PRE_CHECKPOINT_TERMINATED_QUESTION; RESERVED_COST_RETAINED_IN_CANONICAL_CHECKPOINT')
    report = dict(checked_at=datetime.now(UTC).isoformat(), predecessor_checkpoint=dict(requests=47, reserved_usd=13.93),
        current_journals=rows, current_project_requests=current_requests, current_project_reserved_usd=current_cost,
        historical_campaign_envelopes=envelopes,
        historical_unspent_allocations_still_held_usd=unused, other_campaign_inventory=other,
        project_capacity_with_all_old_allocations_and_new_3_usd=round(current_cost+unused+3, 8),
        new_max_requests=12, new_max_reserved_usd=3, project_cap_usd=50,
        nested_product_journals_counted_again=False, actual_bill_usd=None)
    assert report['project_capacity_with_all_old_allocations_and_new_3_usd'] <= 50, str([
        r for r in other if not r['own_integration_d'] and not r['nested_copy_of_known_campaign']])
    return report


if __name__ == '__main__':
    report = audit()
    target = Path(sys.argv[1]) if len(sys.argv)>1 else DOC/'evidence/prelive-budget-audit.json'
    save(target, report)
    print(json.dumps({k:report[k] for k in ('current_project_requests', 'current_project_reserved_usd',
        'historical_unspent_allocations_still_held_usd', 'project_capacity_with_all_old_allocations_and_new_3_usd')}))
