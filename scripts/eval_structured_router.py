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
from linkloom.ui.router_context import admit_route, build_context
from linkloom.ui.router_provider import _DeepSeekJSONClient
from linkloom.ui.structured_router import CONFIG, SAFETY, RouterInvalid, request_for, route_with_adapter
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
           ROOT / 'src/linkloom/ui/question_routing.py', ROOT / 'scripts/eval_routing.py')


def hashes():
    return {p.relative_to(ROOT).as_posix():sha256(p.read_bytes()).hexdigest() for p in TRACKED}


class OldDevelopmentFixture:
    def __init__(self):
        self.workspace = legacy.FIXTURE['workspace_id']
        self.slots = [dict(s, workspace_id=self.workspace, reviewed=True) for s in legacy.FIXTURE['slots']]
        self.resolver = FrozenRelationResolver(tuple(s['relation'] for s in self.slots),
                                                 aliases=legacy.FIXTURE['relation_aliases'])
        self.by_scope = {(s['subject'],s['relation']):s for s in self.slots}

    def view(self, subject, relation, when):
        slot = self.by_scope[(subject,relation)]
        cutoff = _parse_as_of(when or NOW)
        history = [x for x in slot['valid_from'] if _parse_as_of(x) <= cutoff]
        return dict(status='FOUND' if history else 'NO_VALID_RECORD_AT_TIME',history=history)

    def context(self, query):
        return build_context(query,self.workspace,self.slots,self.resolver,view=self.view,query_time=NOW)

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


def predict_rule(fixture, query):
    result = legacy.predict(query,fixture.slots,fixture.resolver,embedder=None)
    return _old_projection('ROUTE' if result['route'] in legacy.INTENTS else 'CLARIFY',
        result['intent'],result['slot'],'UNRESOLVED' if result['date_error'] else 'AS_OF' if result['as_of'] else 'NONE')


def predict_model(fixture, query, adapter):
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
        return predict_rule(fixture,query),str(error),attempted,True


def summarize(rows, arm):
    chosen=[r for r in rows if r['arm']==arm]
    if not chosen:return None
    deltas=sorted(r['added_seconds'] for r in chosen if 'added_seconds' in r)
    attempts=sum(r['attempted'] for r in chosen)
    return dict(n=len(chosen),exact=sum(r['correct'] for r in chosen)/len(chosen),
        wrong_bindings=sum(r['wrong_binding'] for r in chosen),
        safety_code_correctness=sum(r['reason'] not in SAFETY for r in chosen)/len(chosen),
        attempted=attempts,invalid_output_fallback=sum(r['fallback'] and r['attempted'] for r in chosen)/attempts if attempts else None,
        added_latency_p95_seconds=deltas[math.ceil(.95*len(deltas))-1] if deltas else None)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--run-dir',type=Path,required=True)
    parser.add_argument('--max-cost-usd',type=float)
    parser.add_argument('--repetitions',type=int,choices=(1,3),default=3)
    args=parser.parse_args()
    run_dir=args.run_dir.resolve()
    if not run_dir.is_relative_to(ROOT/'work'):
        parser.error('run-dir must be a new subdirectory of ignored work/')
    fixture=OldDevelopmentFixture()
    cases=legacy.load_cases(legacy.DEFAULT_CASES)
    if len(cases)!=60:raise ValueError('ORIGINAL_60_REQUIRED')
    locked=hashes()
    requests=[request_for(fixture.context(c['query'])) for c in cases]
    count=len(requests)*args.repetitions
    sizes=[len(json.dumps(r.to_dict(),ensure_ascii=False).encode('utf8'))+4096 for r in requests]
    upper=sum(sizes)*args.repetitions*INPUT_RATE/1e6 + count*CONFIG['max_output_tokens']*OUTPUT_RATE/1e6
    print(f'Preflight: {len(cases)} development cases; {count} maximum router calls; peak reservation ${upper:.6f}',flush=True)
    key=None
    if args.live:
        if args.max_cost_usd is None or not math.isfinite(args.max_cost_usd) or not upper<=args.max_cost_usd<=1:
            parser.error('explicit --max-cost-usd must cover the full reservation and be <= 1')
        from dotenv import dotenv_values
        env_path=ROOT/'.env'
        local=dotenv_values(env_path,interpolate=False) if env_path.is_file() else {}
        key=(local.get('DEEPSEEK_API_KEY') or os.environ.get('DEEPSEEK_API_KEY') or '').strip()
        if not key:parser.error('local DeepSeek credentials are not configured')
    run_dir.mkdir(parents=True,exist_ok=False)
    def save(name, data):
        (run_dir/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    save('freeze.json',dict(hashes=locked,model=CONFIG,repetitions=args.repetitions,
                           safety_note='Old 60 contains no explicit safety denial labels; only false positives can be measured here.',
                           split='old-development-60',input_rate=INPUT_RATE,output_rate=OUTPUT_RATE,
                           upper_reservation_usd=upper,query_time=NOW))
    def validate():
        if hashes()!=locked:raise ValueError('FROZEN_ROUTER_INPUT_CHANGED')
    client_adapter = DeepSeekProviderAdapter(_DeepSeekJSONClient(key),model_id=CONFIG['model_id'],json_output=True) if args.live else None
    spent, calls, failure, attempts = 0., 0, None, []
    rows=[]
    def record(case,repeat,arm,prediction,seconds,*,reason='NONE',attempted=False,fallback=False,added=None):
        expected=expected_old(case)
        row=dict(id=case['id'],category=case['category'],repeat=repeat,arm=arm,prediction=prediction,
            correct=prediction==expected,wrong_binding=bool(prediction['entities'] and prediction['entities']!=expected['entities']),
            reason=reason,seconds=seconds,attempted=attempted,fallback=fallback)
        if added is not None:row['added_seconds']=added
        rows.append(row)
    for repeat in range(1,args.repetitions+1):
        for ordinal,case in enumerate(cases):
            validate()
            start=perf_counter();prediction=predict_rule(fixture,case['query']);elapsed=perf_counter()-start
            record(case,repeat,'rule',prediction,elapsed)
            if client_adapter:
                # Every question has one bounded send. A known failed JSON
                # response is a scored fallback, not a retry or a campaign reset.
                remaining=args.max_cost_usd-spent
                reserve=sizes[ordinal]*INPUT_RATE/1e6+CONFIG['max_output_tokens']*OUTPUT_RATE/1e6
                if remaining+1e-10 < reserve:
                    failure='CAMPAIGN_BUDGET_EXHAUSTED';break
                limits=TaskProviderLimits(1,sizes[ordinal],CONFIG['max_output_tokens'],remaining,
                    CONFIG['timeout_seconds'],CONFIG['timeout_seconds'],.000000001,INPUT_RATE,OUTPUT_RATE)
                session=TaskProviderSession(run_dir/'provider'/f'{repeat}-{ordinal+1:02d}',limits,validate)
                try:
                    start=perf_counter();p,reason,attempted,fallback=predict_model(fixture,case['query'],session.bind(client_adapter,'old-dev-router'));duration=perf_counter()-start
                    record(case,repeat,'llm',p,duration,reason=reason,attempted=attempted,fallback=fallback,added=duration-elapsed)
                finally:
                    spent+=session.cost;calls+=session.requests;attempts.extend(session.attempts)
                    session.close()
                if reason=='PROVIDER_MODEL_MISMATCH':failure=reason
                if session.failure in {'PROVIDER_TIMEOUT_UNCERTAIN','PROVIDER_UNCERTAIN','TASK_PROVIDER_BUDGET_EXHAUSTED',
                                       'PROVIDER_USAGE_EXCEEDED','TASK_SOURCE_AUTHORIZATION_CHANGED'}:
                    failure=session.failure
                save('partial.json',dict(rows=rows,provider_failure=failure,request_count=calls,reserved_usd=spent))
                if failure:break
        print(f'Repetition {repeat}: {len(rows)} arm rows',flush=True)
        if failure:break
    usage_upper=sum((a['usage'].get('input_tokens') or 0)*INPUT_RATE/1e6 +
                    (a['usage'].get('output_tokens') or 0)*OUTPUT_RATE/1e6
                    for a in attempts if a.get('usage') and a['usage'].get('input_tokens') is not None
                    and a['usage'].get('output_tokens') is not None)
    unknown_usage=sum(not a.get('usage') or a['usage'].get('input_tokens') is None or
                      a['usage'].get('output_tokens') is None for a in attempts)
    report=dict(split='old-development-60',repetitions=args.repetitions,metrics={arm:summarize(rows,arm) for arm in ('rule','llm')},
                rows=rows,provider_failure=failure,request_count=calls,reserved_usd=spent,
                usage_estimate_peak_usd=usage_upper,unknown_usage_calls=unknown_usage,
                complete=failure is None and len(rows)==count*(2 if client_adapter else 1))
    save('report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k!='rows'},ensure_ascii=False),flush=True)
    if not report['complete']:raise SystemExit(2)


if __name__=='__main__':
    main()
