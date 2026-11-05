#!/usr/bin/env python3
"""Old 60 routing development cases, comparing the same read-only fixture.

Default is offline baseline/preflight. --live is an explicit one-run opt-in;
no new, held-out or externally supplied dataset is accepted by this command.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))
from scripts import eval_routing as legacy
from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.product import _parse_as_of
from linkloom.ui.ask_admission import admission_restriction, legacy_denial_reason
from linkloom.ui.router_context import admit_route, blocked_entity_reason, build_context
from linkloom.ui.router_provider import _DeepSeekJSONClient
from linkloom.ui.structured_router import CONFIG, SAFETY, RouterInvalid, request_for, route_with_adapter, rule_date_blocker, rule_needs_router
from linkloom.ui.task_provider import TaskProviderLimits, TaskProviderSession
from linkloom.ui.scope_discovery import resolve_query_date

CONFIG_PATH = ROOT / 'src/linkloom/ui/router_config.json'
ACCEPTANCE = ROOT / 'tests/routing/router_acceptance.json'
NOW = '2026-11-05T00:00:00+00:00'
# Peak, cache-miss valuation; verified against official Models & Pricing
# before this development run. Report as a reservation, not a provider invoice.
INPUT_RATE, OUTPUT_RATE = .30, 1.20
TRACKED = (CONFIG_PATH, ACCEPTANCE, legacy.DEFAULT_CASES, Path(__file__),
           ROOT / 'src/linkloom/ui/structured_router.py', ROOT / 'src/linkloom/ui/router_context.py',
           ROOT / 'src/linkloom/ui/router_provider.py', ROOT / 'src/linkloom/ui/scope_discovery.py',
           ROOT / 'src/linkloom/ui/question_routing.py', ROOT / 'scripts/eval_routing.py',
           ROOT / 'src/linkloom/ui/ask_admission.py', ROOT / 'src/linkloom/ui/unified_ask.py')


def hashes():
    return {p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in TRACKED}


class OldDevelopmentFixture:
    def __init__(self):
        self.workspace = legacy.FIXTURE['workspace_id']
        self.slots = [dict(s, workspace_id=self.workspace, reviewed=True) for s in legacy.FIXTURE['slots']]
        self.resolver = FrozenRelationResolver(tuple(s['relation'] for s in self.slots),
                                                 aliases=legacy.FIXTURE['relation_aliases'])
        self.by_scope = {(s['subject'],s['relation']):s for s in self.slots}

        # Existing synthetic fixture carries these exclusion facts separately
        # from reviewed slots. No case labels or expected answers enter routing.
        self.blocked = tuple((label, 'WORKSPACE_DENIED' if 'another workspace' in description
                              else 'NO_CONFIRMED_RECORD')
                             for label, description in legacy.FIXTURE['excluded'].items()
                             if 'another workspace' in description or 'pending' in description)

    def blocker(self, query, rule):
        if admission_restriction(query):
            return legacy_denial_reason(query)
        return blocked_entity_reason(query, self.blocked) or rule_date_blocker(
            rule['date_error'], has_scope=bool(rule['slot']))

    def view(self, subject, relation, when):
        slot = self.by_scope[(subject,relation)]
        cutoff = _parse_as_of(when or NOW)
        history = [x for x in slot['valid_from'] if _parse_as_of(x) <= cutoff]
        return dict(status='FOUND' if history else 'NO_VALID_RECORD_AT_TIME',history=history)

    def context(self, query):
        return build_context(query,self.workspace,self.slots,self.resolver,view=self.view,query_time=NOW,
                             blocked_entities=self.blocked)

    def slot_id(self, candidate):
        return self.by_scope[candidate.identity]['slot_id'] if candidate else None


def _old_projection(decision, intent, slot, role):
    # Existing 60 cases are Decision-only. Domain is derived from their
    # bound slot; no per-item labels are authored, and date clarification
    # keeps a known slot. The original format does not distinguish ranges.
    return dict(decision=decision,intent=intent,domain='DECISION' if slot else 'NONE',
                entities=[slot] if slot else [],time_role=role)


def expected_old(case):
    _, error = resolve_query_date(case['query'], None)
    return _old_projection('ROUTE' if case['expected_route'] in legacy.INTENTS else 'CLARIFY',
        case['expected_intent'],case['expected_slot'],'AS_OF' if case['expected_as_of'] else 'UNRESOLVED' if error else 'NONE')


def project_rule(result):
    return _old_projection('ROUTE' if result['route'] in legacy.INTENTS else 'CLARIFY',
        result['intent'],result['slot'],'UNRESOLVED' if result['date_error'] else 'AS_OF' if result['as_of'] else 'NONE')


def score_old_route(prediction, expected):
    """Compare the frozen old labels at their original intent granularity."""
    coarse = {"LIST": "INVESTIGATION", "COMPARE": "INVESTIGATION", "HISTORY": "INVESTIGATION"}
    def normalize(value):
        out = dict(value)
        if out["decision"] in {"CLARIFY", "REFUSE"}:
            out.pop("intent")
        else:
            out["intent"] = coarse.get(out["intent"], out["intent"])
        return out
    return normalize(prediction) == normalize(expected)


def predict_model(fixture, query, adapter, rule):
    attempted = False
    try:
        context = fixture.context(query)
        request = request_for(context)
        attempted = True
        route = route_with_adapter(adapter,request,context)
        admission = admit_route(context,route)
        role = 'UNRESOLVED' if route.time_role in ('RANGE','UNRESOLVED') else route.time_role
        if admission.executor:
            decision = 'ROUTE'
        else:
            decision = 'REFUSE' if route.decision=='REFUSE' else 'CLARIFY'
        scoreable = _old_projection(decision,route.intent,fixture.slot_id(admission.candidate),role)
        return scoreable, admission.reason, attempted, False
    except RouterInvalid as error:
        return rule,str(error),attempted,True


def special_reason(case):
    # Side audit only: independent of routing and the frozen exact score.
    category = case['category']
    if category == 'other_workspace':
        return 'WORKSPACE_DENIED'
    if category == 'proposal_only':
        return 'NO_CONFIRMED_RECORD'
    if category == 'date_clarify':
        _, error = resolve_query_date(case['query'], None)
        return 'INVALID_AS_OF' if error and error['code'] == 'INVALID_AS_OF' else 'TIME_SCOPE_UNRESOLVED'
    if category in {'write', 'write_request'}:
        return 'WRITE_NOT_ALLOWED'
    return None


def summarize(rows, arm):
    chosen=[r for r in rows if r['arm']==arm]
    if not chosen:return None
    deltas=sorted(r['added_seconds'] for r in chosen if 'added_seconds' in r)
    attempts=sum(r['attempted'] for r in chosen)
    invalid=sum(r['fallback'] and r['attempted'] for r in chosen)
    safety=[r for r in chosen if r['expected_reason'] in SAFETY or r['reason'] in SAFETY]
    return dict(n=len(chosen),exact=sum(r['correct'] for r in chosen)/len(chosen),
        wrong_bindings=sum(r['wrong_binding'] for r in chosen),
        safety_code_correctness=sum(r['expected_reason']==r['reason'] for r in safety)/len(safety) if safety else None,
        safety_samples=len(safety), attempted=attempts,
        invalid_output_fallback_all=invalid/len(chosen),
        invalid_output_fallback=invalid/attempts if attempts else None,
        added_latency_p95_seconds=deltas[math.ceil(.95*len(deltas))-1] if deltas else None)


def priced_usage(attempt):
    usage = attempt.get('usage') or {}
    input_tokens, output_tokens = usage.get('input_tokens'), usage.get('output_tokens')
    if input_tokens is None or output_tokens is None:
        return None
    if input_tokens > attempt['input_reserved'] or output_tokens > attempt['output_reserved']:
        return None
    return (input_tokens*INPUT_RATE + output_tokens*OUTPUT_RATE)/1e6


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--max-cost-usd',type=float)
    parser.add_argument('--repetitions',type=int,choices=(1,3),default=3)
    parser.add_argument('--prior-run', action='append', type=Path, default=[])
    args=parser.parse_args()
    run_dir=args.run_dir.resolve()
    if not run_dir.is_relative_to(ROOT/'work'):
        parser.error('run-dir must be a new subdirectory of ignored work/')
    fixture=OldDevelopmentFixture()
    cases=legacy.load_cases(legacy.DEFAULT_CASES)
    if len(cases)!=60:raise ValueError('ORIGINAL_60_REQUIRED')
    locked=hashes()
    rule_results=[legacy.predict(c['query'],fixture.slots,fixture.resolver,embedder=None) for c in cases]
    blockers=[fixture.blocker(c['query'], r) for c,r in zip(cases,rule_results)]
    cascade_calls=[rule_needs_router(r['route'], blocker=b) for r,b in zip(rule_results,blockers)]
    sizes=[len(json.dumps(request_for(fixture.context(c['query'])).to_dict(),ensure_ascii=False).encode('utf8'))+4096
           if needed else 0 for c,needed in zip(cases,cascade_calls)]
    reserves=[size*INPUT_RATE/1e6+CONFIG['max_output_tokens']*OUTPUT_RATE/1e6 if size else 0. for size in sizes]
    upper=sum(reserves)*args.repetitions
    count=sum(cascade_calls)*args.repetitions
    prior_spend=0.
    prior_reports=set()
    for prior in args.prior_run:
        path=prior.resolve()
        if not path.is_relative_to(ROOT/'work') or path in prior_reports:
            parser.error('prior-run must uniquely identify a saved work/ report')
        prior_reports.add(path)
        saved=json.loads(path.read_text(encoding='utf8'))
        if saved['split'] not in {'old-development-60', 'external-v1-dev'}:
            parser.error('prior-run must be a saved routing development report')
        if 'charged_upper_usd' in saved:
            prior_spend+=saved['charged_upper_usd']
        else:
            prior_spend+=saved['reserved_usd'] if saved['unknown_usage_calls'] else saved['usage_estimate_peak_usd']
    print(f'Preflight: {len(cases)} development cases; {count} maximum router calls; '
          f'peak reservation ${upper:.6f}; prior charges ${prior_spend:.6f}',flush=True)
    key=None
    if args.live:
        if not prior_reports:
            parser.error('live repeat requires the saved reports from earlier rounds')
        if args.max_cost_usd is None or not math.isfinite(args.max_cost_usd) or not prior_spend+max(reserves)<=args.max_cost_usd<=.8:
            parser.error('cumulative cap must cover prior charges and a request reservation and be <= 0.80')
        from dotenv import dotenv_values
        env_path=ROOT/'.env'
        local=dotenv_values(env_path,interpolate=False) if env_path.is_file() else {}
        key=(local.get('DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY') or '').strip()
        if not key:parser.error('local DeepSeek credentials are not configured')
    run_dir.mkdir(parents=True,exist_ok=False)
    def save(name, data):
        (run_dir/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save('freeze.json',dict(hashes=locked,model=CONFIG,repetitions=args.repetitions,
                           safety_note='Side audit uses existing case categories, independently of routing. No write cases in the old 60.',
                           split='old-development-60',input_rate=INPUT_RATE,output_rate=OUTPUT_RATE,
                           upper_reservation_usd=upper,prior_spend_usd=prior_spend,query_time=NOW))
    def validate():
        if hashes()!=locked:raise ValueError('FROZEN_ROUTER_INPUT_CHANGED')
    client_adapter = DeepSeekProviderAdapter(_DeepSeekJSONClient(key),model_id=CONFIG['model_id'],json_output=True) if args.live else None
    spent, charged, calls, failure, attempts = 0., 0., 0, None, []
    rows=[]
    def record(case,repeat,arm,prediction,seconds,*,reason='NONE',attempted=False,fallback=False,added=None):
        expected=expected_old(case)
        row=dict(id=case['id'],category=case['category'],repeat=repeat,arm=arm,prediction=prediction,
            correct=score_old_route(prediction,expected),wrong_binding=bool(prediction['entities'] and prediction['entities']!=expected['entities']),
            reason=reason,expected_reason=special_reason(case),seconds=seconds,attempted=attempted,fallback=fallback)
        if added is not None:row['added_seconds']=added
        rows.append(row)
    for repeat in range(1,args.repetitions+1):
        for ordinal,case in enumerate(cases):
            validate()
            start=perf_counter()
            raw_rule=legacy.predict(case['query'],fixture.slots,fixture.resolver,embedder=None)
            elapsed=perf_counter()-start
            prediction=project_rule(raw_rule)
            date_error=raw_rule['date_error']
            legacy_reason=(date_error or {}).get('code') if raw_rule['slot'] else None
            if not legacy_reason and raw_rule['route'] not in legacy.INTENTS:
                legacy_reason='DECISION_SCOPE_'+raw_rule['discovery_status']
            record(case,repeat,'rule',prediction,elapsed,reason=legacy_reason or 'NONE')
            if not client_adapter:
                continue
            for arm in ('cascade',):
                if not cascade_calls[ordinal]:
                    record(case,repeat,arm,prediction,elapsed,reason=blockers[ordinal] or legacy_reason or 'NONE',added=0.)
                    continue
                remaining=args.max_cost_usd-prior_spend-charged
                if remaining+1e-10 < reserves[ordinal]:
                    failure='CAMPAIGN_BUDGET_EXHAUSTED'
                    break
                limits=TaskProviderLimits(1,sizes[ordinal],CONFIG['max_output_tokens'],remaining,
                    CONFIG['timeout_seconds'],CONFIG['timeout_seconds'],.000000001,INPUT_RATE,OUTPUT_RATE)
                session=TaskProviderSession(run_dir/'provider'/f'{arm}-{repeat}-{ordinal+1:02d}',limits,validate)
                try:
                    start=perf_counter()
                    adapter=session.bind(client_adapter,'old-dev-router')
                    p,reason,attempted,fallback=predict_model(fixture,case['query'],adapter,prediction)
                    duration=perf_counter()-start
                    total=duration+elapsed if arm=='cascade' or fallback else duration
                    record(case,repeat,arm,p,total,reason=reason,attempted=attempted,fallback=fallback,added=total-elapsed)
                finally:
                    spent+=session.cost
                    for attempt in session.attempts:
                        estimate=priced_usage(attempt)
                        charged+=attempt['cost_reserved_usd'] if estimate is None else estimate
                    calls+=session.requests
                    attempts.extend(session.attempts)
                    session.close()
                if reason=='PROVIDER_MODEL_MISMATCH':
                    failure=reason
                if session.failure in {'PROVIDER_TIMEOUT_UNCERTAIN','PROVIDER_UNCERTAIN','TASK_PROVIDER_BUDGET_EXHAUSTED',
                                       'PROVIDER_USAGE_EXCEEDED','TASK_SOURCE_AUTHORIZATION_CHANGED'}:
                    failure=session.failure
                save('partial.json',dict(rows=rows,provider_failure=failure,request_count=calls,reserved_usd=spent,charged_upper_usd=charged))
                if failure:
                    break
            if failure:
                break
        print(f'Repetition {repeat}: {len(rows)} arm rows',flush=True)
        if failure:
            break
    estimates=[priced_usage(attempt) for attempt in attempts]
    usage_upper=sum(estimate for estimate in estimates if estimate is not None)
    unknown_usage=estimates.count(None)
    report=dict(split='old-development-60',repetitions=args.repetitions,metrics={arm:summarize(rows,arm) for arm in ('rule','cascade')},
                rows=rows,provider_failure=failure,request_count=calls,reserved_usd=spent,
                usage_estimate_peak_usd=usage_upper,charged_upper_usd=charged,unknown_usage_calls=unknown_usage,
                complete=failure is None and len(rows)==len(cases)*args.repetitions*(2 if client_adapter else 1))
    save('report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False),flush=True)
    if not report['complete']:raise SystemExit(2)


if __name__=='__main__':
    main()
