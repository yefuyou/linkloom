"""Read-only call chain of a parallel run: Planner, branches, Synthesizer, cost and sibling overlap.

Every number comes from files the run already wrote (pool.json, parallel.json, branches/<name>/budget.json
and the branch Runtime trace) and from the Host snapshot. Nothing here calls a model or writes a file.

Interval meaning: start is the creation of the child Provider session (its launch), end is start plus the
elapsed_seconds of the last receipt write. It covers the whole session, not only model requests. A single
request has no timestamp, so a request-level timeline is N/A. Runtime trace events carry no timestamps
either; they are listed in recorded order only.

Session overlap is computed between sibling branches of the same pool, and only when both have a complete interval.
Trace events nested inside a branch never take part. Every value that cannot be established is None and its
reason is recorded in the owning object's "na" mapping.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from pathlib import Path
import re

from linkloom.observability.reader import TraceReader
from linkloom.ui import parallel_resume

SCHEMA = 'branch-trace/v1'
MAX_EVENTS = 200
WITHHELD_BY_STOP = 'WITHHELD_BY_STOP_STATE'
_NAME = re.compile(r'[A-Za-z0-9_-]{1,64}')
_NOTES = {'UNCERTAIN': 'OUTCOME_UNCERTAIN_MAY_BE_BILLED', 'PENDING': 'OUTCOME_PENDING_NOT_VERIFIED',
          'WITHHELD': 'RESULT_WITHHELD', 'FAILED': 'REQUEST_FAILED'}


def _plain(root, path):
    """The path stays inside root and neither it nor any parent below root is a symbolic link."""
    root, current = Path(root), Path(path)
    try:
        if not current.resolve().is_relative_to(root.resolve()):
            return False
        while current != root and current != current.parent:
            if current.is_symlink():
                return False
            current = current.parent
    except OSError:
        return False
    return True


def _read_json(root, path):
    if not _plain(root, path):
        raise ValueError('PATH_NOT_PLAIN')
    return json.loads(Path(path).read_text(encoding='utf8'))


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _number(value):
    return float(value) if (isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and value >= 0) else None


def _total(values, digits=None):
    """Sum of the recorded values. An unrecorded value (None) is left out, never counted as 0; if every value
    is unrecorded the total is None. No value at all sums to 0."""
    values = list(values)
    known = [value for value in values if value is not None]
    if values and not known:
        return None
    return round(sum(known), digits) if digits is not None else sum(known)


def _interval(budget, pool_status, pool_reason):
    """(interval dict, reason, (start, end)); an unsettled or unreadable slot has no complete interval."""
    if budget is None:
        return None, 'BUDGET_RECORD_UNAVAILABLE', None
    if pool_status is None:
        return None, pool_reason, None
    if pool_status == 'RUNNING':
        return None, 'BRANCH_NOT_SETTLED', None
    try:
        start = datetime.fromisoformat(budget['started_at'])
        elapsed = _number(budget['elapsed_seconds'])
        if start.tzinfo is None or elapsed is None:
            raise ValueError
        end = start + timedelta(seconds=elapsed)
    except Exception:
        return None, 'INTERVAL_FIELDS_INVALID', None
    return dict(start=start.isoformat(), end=end.isoformat(), duration_seconds=round(elapsed, 3)), None, (start, end)


def _attempts(budget, withheld):
    """Per request rows. Estimated cost is usage times the session rates; reserved cost is only a ceiling."""
    limits = budget.get('limits') if isinstance(budget.get('limits'), dict) else {}
    rate_in, rate_out = _number(limits.get('input_usd_per_million')), _number(limits.get('output_usd_per_million'))
    rows = []
    for row in budget['attempts']:
        outcome = str(row['outcome'])
        usage = row.get('usage') if isinstance(row.get('usage'), dict) else {}
        tokens_in, tokens_out = _count(usage.get('input_tokens')), _count(usage.get('output_tokens'))
        usable = outcome == 'SUCCEEDED' and tokens_in is not None and tokens_out is not None
        known = usable and rate_in is not None and rate_out is not None
        usage_reason = None if usable else 'OUTCOME_NOT_SUCCEEDED' if outcome != 'SUCCEEDED' else 'USAGE_NOT_REPORTED'
        reason = usage_reason or (None if known else 'RATE_NOT_RECORDED')
        item = dict(sequence=_count(row.get('sequence')), stage=str(row.get('stage')), outcome=outcome,
            verified=outcome == 'SUCCEEDED', note=_NOTES.get(outcome),
            input_tokens_reserved=_count(row.get('input_reserved')), output_tokens_reserved=_count(row.get('output_reserved')),
            reserved_cost_usd=_number(row.get('cost_reserved_usd')),
            input_tokens=tokens_in if usable else None, output_tokens=tokens_out if usable else None,
            estimated_cost_usd=round((tokens_in * rate_in + tokens_out * rate_out) / 1e6, 8) if known else None,
            interval=None, na=dict(interval='REQUEST_HAS_NO_TIMESTAMP'))
        for key in ('input_tokens_reserved', 'output_tokens_reserved', 'reserved_cost_usd'):
            if item[key] is None:
                item['na'][key] = 'RESERVATION_NOT_RECORDED'
        if not usable:
            item['na']['tokens'] = usage_reason
        if not known:
            item['na']['estimated_cost_usd'] = reason
        if not withheld:
            item.update(request_sha256=row.get('request_sha256'), response_sha256=row.get('response_sha256'),
                        provider_request_id=row.get('provider_request_id'),
                        provider_response_id=row.get('provider_response_id'))
        rows.append(item)
    return rows


def _events(root, base, name):
    """(events, summary, reason): the branch Runtime trace read through the validated TraceReader."""
    traces = Path(base) / 'branches' / name / 'traces'
    try:
        if not _plain(root, traces) or not traces.is_dir():
            return None, None, 'TRACE_NOT_RECORDED'
        runs = sorted(item for item in traces.iterdir() if item.is_dir())
        if len(runs) != 1 or not _plain(root, runs[0]) or not _plain(root, runs[0] / 'events.jsonl'):
            return None, None, 'TRACE_NOT_RECORDED'
        reader = TraceReader(traces)
        if not reader.read_manifest(runs[0].name).complete:
            return None, None, 'TRACE_INCOMPLETE'
        recorded = reader.read_events(runs[0].name)
    except Exception:
        return None, None, 'TRACE_INVALID'
    events = [dict(seq=event.seq, event_type=event.event_type, actor=event.actor, status=event.status,
                   tool=event.attributes.get('tool') if isinstance(event.attributes.get('tool'), str) else None,
                   started_at=event.started_at, finished_at=event.finished_at, duration_ms=event.duration_ms)
              for event in recorded[:MAX_EVENTS]]
    timed = any(event['started_at'] or event['finished_at'] or event['duration_ms'] is not None for event in events)
    return events, dict(count=len(recorded), truncated=len(recorded) > MAX_EVENTS, timed=timed,
                        timing_na=None if timed else 'TRACE_EVENTS_HAVE_NO_TIMESTAMPS'), None


def _slot(root, base, name, entry, pool_reason, withheld):
    """Budget facts of one child session (a branch or the Planner)."""
    try:
        budget = _read_json(root, Path(base) / 'branches' / name / 'budget.json')
        attempts = _attempts(budget, withheld)
    except Exception:
        budget, attempts = None, []
    status = entry.get('status') if isinstance(entry, dict) else None
    interval, reason, span = _interval(budget, status, pool_reason if status is None else None)
    slot = dict(name=name, pool_status=status, interval=interval, na={})
    if reason:
        slot['na']['interval'] = reason
    if budget is None:
        slot.update(requests=None, attempts=[], input_tokens_reserved=None, output_tokens_reserved=None,
                    reserved_cost_usd=None, estimated_cost_usd=None, unestimated_requests=None,
                    reservation_unrecorded_requests=None, failure=None)
        slot['na'].update({key: 'BUDGET_RECORD_UNAVAILABLE' for key in
                           ('requests', 'tokens', 'reserved_cost_usd', 'estimated_cost_usd')})
        return slot, span
    estimates = [row['estimated_cost_usd'] for row in attempts if row['estimated_cost_usd'] is not None]
    slot.update(requests=len(attempts), attempts=attempts,
        input_tokens_reserved=_total(row['input_tokens_reserved'] for row in attempts),
        output_tokens_reserved=_total(row['output_tokens_reserved'] for row in attempts),
        reserved_cost_usd=_total((row['reserved_cost_usd'] for row in attempts), 8),
        reservation_unrecorded_requests=sum(1 for row in attempts if None in (
            row['input_tokens_reserved'], row['output_tokens_reserved'], row['reserved_cost_usd'])),
        estimated_cost_usd=round(sum(estimates), 8) if estimates else None,
        unestimated_requests=len(attempts) - len(estimates),
        failure=budget['failure'] if isinstance(budget.get('failure'), str) else None)
    if not estimates:
        slot['na']['estimated_cost_usd'] = 'NO_REQUEST_WITH_USAGE'
    for key in ('input_tokens_reserved', 'output_tokens_reserved', 'reserved_cost_usd'):
        if slot[key] is None:
            slot['na'][key] = 'RESERVATION_NOT_RECORDED'
    return slot, span


def compute_overlap(items):
    """Overlap of sibling intervals. items: dict(name, pool, start, end); only the same pool are siblings."""
    groups = {}
    for item in items:
        groups.setdefault(item['pool'], []).append(item)
    session_pairs, seconds, peak, compared = [], 0.0, 0, 0
    for group in groups.values():
        if len(group) < 2:
            continue
        compared += len(group)
        for index, first in enumerate(group):
            for second in group[index + 1:]:
                shared = (min(first['end'], second['end']) - max(first['start'], second['start'])).total_seconds()
                if shared > 0:
                    session_pairs.append(dict(branches=sorted((first['name'], second['name'])), seconds=round(shared, 3)))
        marks = sorted([(item['start'], 1) for item in group] + [(item['end'], -1) for item in group],
                       key=lambda mark: (mark[0], mark[1]))  # an interval ending where another starts is not overlap
        active, previous = 0, None
        for moment, change in marks:
            if previous is not None and active >= 2:
                seconds += (moment - previous).total_seconds()
            active += change
            peak = max(peak, active)
            previous = moment
    if not compared:
        return dict(session_overlap_seconds=None, max_concurrent_sessions=None, session_pairs=[],
                    na=dict(session_overlap_seconds='FEWER_THAN_TWO_COMPLETE_SIBLING_INTERVALS',
                            max_concurrent_sessions='FEWER_THAN_TWO_COMPLETE_SIBLING_INTERVALS'))
    return dict(session_overlap_seconds=round(seconds, 3), max_concurrent_sessions=peak, session_pairs=sorted(session_pairs, key=lambda p: p['branches']),
                na={})


def _pool(root, run_id, directory):
    """(pool.json branches mapping, reason). A pool of another run is never trusted."""
    try:
        journal = _read_json(root, Path(directory) / 'pool.json')
        if journal['run_id'] != run_id:
            return None, 'POOL_RUN_MISMATCH'
        branches = journal['branches']
        if not isinstance(branches, dict):
            raise ValueError
        return branches, None
    except Exception:
        return None, 'POOL_RECORD_UNAVAILABLE'


def build_branch_trace(root, run_id, snapshot, max_steps):
    """Return the call chain of the saved run in root. Raises ValueError when parallel.json is unreadable."""
    root = Path(root)
    record = parallel_resume.load_record(root)
    outer = snapshot['parallel_investigation']
    withheld = outer.get('status') in ('ABORTED', 'CANCELLED')
    running = outer.get('status') == 'RUNNING'
    published = {row['branch']: row for row in outer.get('branches', ()) if isinstance(row, dict)}
    gaps = outer.get('gaps') if isinstance(outer.get('gaps'), dict) else {}
    names = [entry['name'] for entry in record['branches']]
    if not all(_NAME.fullmatch(name) for name in names):
        raise ValueError('PARALLEL_RECORD_INVALID')
    resumed = parallel_resume.resumed_names(root, names)
    pools = {'original': _pool(root, run_id, root)}
    if resumed:
        pools['resume'] = _pool(root, run_id, root / parallel_resume.RESUME_DIR)
    resume_states, resume_reason = {}, None
    if withheld:
        resume_reason = WITHHELD_BY_STOP
    elif running:
        resume_reason = 'RUN_STILL_RUNNING'
    else:
        try:
            resume_states = {row['branch']: row for row in
                             parallel_resume.classify_run(root, record, run_id, max_steps)['states']}
        except Exception:
            resume_reason = 'RESUME_STATE_UNAVAILABLE'

    planner_record = record.get('planner') if isinstance(record.get('planner'), dict) else None
    entries, reason = pools['original']
    planner, planner_span = _slot(root, root, 'planner', (entries or {}).get('planner'), reason, withheld)
    planner.update(source=planner_record['source'] if planner_record else 'NOT_RUN',
                   reason_code=planner_record.get('reason') if planner_record else None)
    if planner['pool_status'] is None and planner['requests'] is None:
        planner['na']['planner'] = 'PLANNER_SLOT_NOT_RECORDED'

    branches, spans, excluded = [], [], []
    for entry in record['branches']:
        name = entry['name']
        again = name in resumed
        pool_name = 'resume' if again else 'original'
        base = root / parallel_resume.RESUME_DIR if again else root
        pool_entries, pool_reason = pools[pool_name]
        branch, span = _slot(root, base, name, (pool_entries or {}).get(name), pool_reason, withheld)
        branch.update(continued=again, verified=name in published, gap=gaps.get(name),
                      question=None, resume_state=None, reviewer=None, events=None, events_summary=None)
        if withheld:
            branch['na'].update(question=WITHHELD_BY_STOP, resume_state=WITHHELD_BY_STOP,
                                reviewer=WITHHELD_BY_STOP, events=WITHHELD_BY_STOP)
        else:
            branch['question'] = entry['question']
            state = resume_states.get(name)
            if state:
                branch['resume_state'] = dict(state=state['state'], reason=state.get('reason'))
            else:
                branch['na']['resume_state'] = resume_reason or 'RESUME_STATE_UNAVAILABLE'
            if name in published:
                branch['reviewer'] = dict(decision='evidence_sufficient', review_id=published[name].get('review_id'))
            else:
                branch['na']['reviewer'] = 'BRANCH_NOT_PUBLISHED'
            events, summary, why = _events(root, base, name)
            branch.update(events=events, events_summary=summary)
            if why:
                branch['na']['events'] = why
            elif not summary['timed']:
                branch['na']['event_timing'] = summary['timing_na']
        branches.append(branch)
        if span:
            spans.append(dict(name=name, pool=pool_name, start=span[0], end=span[1]))
        else:
            excluded.append(dict(branch=name, reason=branch['na'].get('interval', 'INTERVAL_UNAVAILABLE')))

    overlap = compute_overlap(spans)
    overlap['excluded'] = excluded
    overlap['basis'] = 'SESSION_INTERVALS_NOT_REQUEST_CONCURRENCY'
    overlap['meaning'] = ('Overlap of whole child sessions (creation to last receipt write). It does not show '
                          'that model requests were sent in parallel; a request has no timestamp.')
    complete = [planner_span] * bool(planner_span) + [(item['start'], item['end']) for item in spans]
    axis = None
    if complete:
        origin = min(start for start, _ in complete)
        axis = dict(start=origin.isoformat(), end=max(end for _, end in complete).isoformat(),
                    duration_seconds=round((max(end for _, end in complete) - origin).total_seconds(), 3))
        by_name = {item['name']: (item['start'], item['end']) for item in spans}
        for slot, span in [(planner, planner_span)] + [(item, by_name.get(item['name'])) for item in branches]:
            if span and slot['interval']:
                slot['interval']['offset_seconds'] = round((span[0] - origin).total_seconds(), 3)

    slots = [planner, *branches]
    known = [slot for slot in slots if slot['requests'] is not None]
    estimates = [slot['estimated_cost_usd'] for slot in known if slot['estimated_cost_usd'] is not None]
    totals = dict(requests=sum(slot['requests'] for slot in known),
        input_tokens_reserved=_total(slot['input_tokens_reserved'] for slot in known),
        output_tokens_reserved=_total(slot['output_tokens_reserved'] for slot in known),
        reserved_cost_usd=_total((slot['reserved_cost_usd'] for slot in known), 8),
        reservation_unrecorded_requests=sum(slot['reservation_unrecorded_requests'] for slot in known),
        estimated_cost_usd=round(sum(estimates), 8) if estimates else None,
        unestimated_requests=sum(slot['unestimated_requests'] for slot in known),
        slots_without_record=len(slots) - len(known),
        wall_seconds=axis['duration_seconds'] if axis else None, overlap=overlap, na={})
    if not estimates:
        totals['na']['estimated_cost_usd'] = 'NO_REQUEST_WITH_USAGE'
    for key in ('input_tokens_reserved', 'output_tokens_reserved', 'reserved_cost_usd'):
        if totals[key] is None:
            totals['na'][key] = 'RESERVATION_NOT_RECORDED'
    if axis is None:
        totals['na']['wall_seconds'] = 'NO_COMPLETE_INTERVAL'

    answer = outer.get('answer') if isinstance(outer.get('answer'), dict) else None
    if withheld:
        synthesizer = dict(status='WITHHELD', published=False, reason_code=outer.get('reason'),
                           cross_branch_compared=None, na=dict(cross_branch_compared=WITHHELD_BY_STOP))
    elif answer is None:
        synthesizer = dict(status='NOT_RUN', published=False, reason_code=None, cross_branch_compared=None,
                           na=dict(cross_branch_compared='SYNTHESIZER_NOT_RUN'))
    else:
        check = answer.get('conflict_check') if isinstance(answer.get('conflict_check'), dict) else {}
        compared = check.get('cross_branch_compared')
        synthesizer = dict(status=str(answer.get('status')), published=answer.get('published') is True,
            reason_code=answer.get('fallback_reason'),
            cross_branch_compared=compared if isinstance(compared, bool) else None,
            answers=len(answer.get('answers') or ()), unknowns=len(answer.get('unknowns') or ()),
            conflicts=len(answer.get('conflicts') or ()),
            na={} if isinstance(compared, bool) else dict(cross_branch_compared='CONFLICT_CHECK_NOT_RECORDED'))
    return dict(schema=SCHEMA, run_id=run_id, status=outer.get('status'), reason_code=outer.get('reason'),
        withheld=withheld, axis=axis,
        interval_basis=dict(start='SESSION_CREATED', end='LAST_RECEIPT_WRITE',
                            scope='WHOLE_SESSION_NOT_ONLY_MODEL_REQUESTS'),
        cost_basis=dict(estimated='SESSION_USAGE_TOKENS_TIMES_SESSION_RATES_NOT_A_BILL',
                        reserved='REQUEST_COST_CEILING_NOT_A_BILL',
                        input_usd_per_million=_rate(root, planner, branches, 'input_usd_per_million'),
                        output_usd_per_million=_rate(root, planner, branches, 'output_usd_per_million')),
        planner=planner, branches=branches, synthesizer=synthesizer, totals=totals)


def _rate(root, planner, branches, key):
    """The session rate when every recorded session used the same one, otherwise None."""
    found = set()
    for slot in [planner, *branches]:
        base = root / parallel_resume.RESUME_DIR if slot.get('continued') else root
        try:
            found.add(_read_json(root, base / 'branches' / slot['name'] / 'budget.json')['limits'][key])
        except Exception:
            continue
    return found.pop() if len(found) == 1 else None
