"""A/B/C mechanism experiment, not an end-to-end Agent or live-model score.

Run with PYTHONPATH=src and an optional locally cached tiktoken vocabulary:
python -m benchmarks.context_engineering.run --output docs/requirements/m3-context-engineering/experiment.json
No Gold is read until all strategy outputs have been produced.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from linkloom.context.assembler import ContextAssembler, ContextBudget, ContextSourceType
from linkloom.context.policy import ContextPolicy, RequestUsage, estimate_tokens
from linkloom.context.recovery import EvidenceRecovery, EvidenceRef, digest
from linkloom.context.working import WorkingContext, RecordRef, ToolState, CompactionProposal, compact_working_context

from .fixtures import Scenario, Passage, declared_groups, scenarios


class FakeModel:
    def compact(self, request):
        return CompactionProposal('Earlier tools inspected candidates. Unresolved work, failures and references remain in structured state.',
                                  tuple(r.evidence_id for r in request.state.evidence_refs))


@dataclass
class StrategyOutput:
    strategy: str
    selected: tuple[Passage, ...]
    context: str
    estimated_tokens: int
    actual_reference_tokens: int
    token_budget: int
    byte_budget: int
    compression_ratio: float | None = None
    restore_attempts: int = 0
    restore_successes: int = 0
    recovery_status: str | None = None
    recovery_failures: tuple[str, ...] = ()
    runtime_uncovered_goals: tuple[str, ...] = ()


def render(p: Passage) -> str:
    return f'Source evidence [{p.ref.evidence_id}] ({p.ref.logical_path}):\n{p.quote}'


def run_strategy(case: Scenario, strategy: str, tokenizer: Callable[[str], int]) -> StrategyOutput:
    """The strategy API takes no Gold or required-evidence set."""
    query = 'Question: 调查项目松林的' + '、'.join(case.subgoals)
    if strategy in {'A', 'B'}:
        cap, chars, tokens = (5, 12000, 3000) if strategy == 'A' else (10, 24000, 6000)
        chosen, parts = [], [query]
        # Frozen baseline behavior: cap before fit, len/4, drop oversized item.
        for passage in case.candidates[:cap]:
            text = '\n\n'.join((*parts, render(passage)))
            if len(text) <= chars and math.ceil(len(text) / 4) <= tokens:
                chosen.append(passage)
                parts.append(render(passage))
        # History uses a chronological suffix. Early original quotes are lost
        # under late irrelevant operations, as in bounded model history.
        history = [*(render(p) for p in case.archived), *case.operations]
        kept = []
        for operation in reversed(history):
            trial = '\n\n'.join((*parts, operation, *kept))
            if math.ceil(len(trial) / 4) > tokens or len(trial) > chars:
                break
            kept.insert(0, operation)
        chosen.extend(p for p in case.archived if render(p) in kept)
        text = '\n\n'.join((*parts, *kept))
        return StrategyOutput(strategy, tuple(chosen), text, math.ceil(len(text) / 4), tokenizer(text), tokens, 96 * 1024)

    recovery = EvidenceRecovery(lambda w, resource: case.sources.get(resource))
    candidates = list(case.candidates)
    hint, ratio, restore = '', None, None
    if case.operations or case.archived:
        failure = ToolState('previous-read', 'failed', 'SOURCE_NOT_FOUND')
        state = WorkingContext(workspace_id='synthetic', run_id=case.case_id,
            user_goal='Investigate ' + ', '.join(case.subgoals), unresolved_questions=case.subgoals,
            evidence_refs=tuple(p.ref for p in case.archived), last_tool=failure, failed_tools=(failure,),
            next_investigations=('Recover archived originals before answering.',), operations=case.operations,
            ledger_ref=RecordRef('synthetic', case.case_id, 'ledger', f'{case.case_id}/ledger.json', digest('raw ledger')),
            checkpoint_ref=RecordRef('synthetic', case.case_id, 'checkpoint', f'{case.case_id}/checkpoint.json', digest('raw checkpoint')))
        compacted = compact_working_context(state, compactor=FakeModel(), recovery=recovery, max_tokens=1800)
        # Explicit independent recovery AFTER a durable state round-trip.
        reloaded = WorkingContext.from_json(compacted.state.to_json())
        restore = recovery.restore(workspace_id='synthetic', refs=reloaded.evidence_refs,
            required_ids=tuple(p.ref.evidence_id for p in case.archived))
        by_ref = {p.ref.evidence_id: p for p in case.archived}
        candidates.extend(by_ref[e.ref.evidence_id] for e in restore.evidence)
        hint, ratio = reloaded.render(), compacted.compression_ratio
    policy = ContextPolicy(model_id='synthetic-small-context', context_window_tokens=4096, output_reserve_tokens=512)
    bundle = ContextAssembler(budget=ContextBudget(), policy=policy).assemble(
        workspace_id='synthetic', query=query.removeprefix('Question: '),
        retrieved_evidence=tuple(p.retrieved(i + 1, case.sources.get(p.ref.resource_id)) for i, p in enumerate(candidates)),
        task_subgoals=case.subgoals, evidence_groups=declared_groups(tuple(candidates), case.subgoals),
        request_usage=RequestUsage(tokens=estimate_tokens(hint) + 128, bytes=len(hint.encode()) + 512))
    selected_ids = {i.item_id for i in bundle.selected if i.source_type is ContextSourceType.EVIDENCE}
    text = '\n\n'.join(t for t in (bundle.rendered_text, hint) if t)
    return StrategyOutput(strategy, tuple(p for p in candidates if p.ref.evidence_id in selected_ids), text,
        estimate_tokens(text), tokenizer(text), 4096 - 512 - 128, 96 * 1024 - 512, ratio,
        len(case.archived) if restore else 0, len(restore.evidence) if restore else 0,
        restore.status if restore else None, tuple(f.reason for f in restore.failures) if restore else (),
        tuple(c.subgoal_id for c in bundle.selection.coverage if not c.complete))


def score(case: Scenario, result: StrategyOutput, gold: dict) -> dict:
    """Gold is used only by this post-run evaluator."""
    recovery = EvidenceRecovery(lambda w, r: case.sources.get(r))
    checked = recovery.restore(workspace_id='synthetic', refs=tuple(p.ref for p in result.selected))
    current = {e.ref.evidence_id for e in checked.evidence}
    visible = {p.label for p in result.selected if p.ref.evidence_id in current}
    declared_visible = {p.label for p in result.selected}
    required = {label for labels in gold['goals'].values() for label in labels}
    covered = required & visible
    counter = set(gold['counter'])
    # Deterministic failure probe, not an LLM accuracy metric: one-sided or
    # stale evidence permits an unsafe conclusion; missing chains force refusal.
    error = bool(checked.failures) or bool(counter and (required - counter) & visible and not counter.issubset(visible))
    refusal = not error and not required.issubset(visible)
    return {
        'case': case.case_id, 'strategy': result.strategy,
        'required_count': len(required), 'required_covered': len(covered),
        'required_evidence_coverage': len(covered) / len(required),
        'subgoals_count': len(gold['goals']),
        'complete_subgoals': sum(set(labels).issubset(visible) for labels in gold['goals'].values()),
        'complete_subgoal_coverage': sum(set(labels).issubset(visible) for labels in gold['goals'].values()) / len(gold['goals']),
        'counter_required': len(counter), 'counter_retained': len(counter & visible),
        'counter_retention': len(counter & visible) / len(counter) if counter else None,
        'actual_reference_tokens': result.actual_reference_tokens,
        'estimated_tokens': result.estimated_tokens,
        'token_difference': result.estimated_tokens - result.actual_reference_tokens,
        'absolute_token_error': abs(result.estimated_tokens - result.actual_reference_tokens),
        'context_chars': len(result.context), 'context_bytes': len(result.context.encode()),
        'token_budget': result.token_budget, 'estimated_utilization': result.estimated_tokens / result.token_budget,
        'actual_reference_utilization': result.actual_reference_tokens / result.token_budget,
        'byte_budget': result.byte_budget, 'byte_utilization': len(result.context.encode()) / result.byte_budget,
        'compression_ratio': result.compression_ratio,
        'restore_attempts': result.restore_attempts, 'restore_successes': result.restore_successes,
        'recovery_status': result.recovery_status, 'recovery_failures': result.recovery_failures,
        'context_loss_error_probe': error, 'context_loss_refusal_probe': refusal,
        'selected_labels': sorted(declared_visible), 'missing_required_labels': sorted(required - visible),
        'runtime_uncovered_goals': result.runtime_uncovered_goals,
    }


def experiment(tokenizer: Callable[[str], int], tokenizer_kind: str) -> dict:
    cases = scenarios()
    outputs = [(case, run_strategy(case, s, tokenizer)) for case in cases for s in ('A', 'B', 'C')]
    gold_path = Path(__file__).with_name('gold.json')
    gold = json.loads(gold_path.read_text(encoding='utf-8'))
    rows = [score(case, result, gold[case.case_id]) for case, result in outputs]
    totals = {}
    for strategy in ('A', 'B', 'C'):
        subset = [r for r in rows if r['strategy'] == strategy]
        def total(key): return sum(r[key] for r in subset)
        attempts = total('restore_attempts')
        ratios = [r['compression_ratio'] for r in subset if r['compression_ratio'] is not None]
        totals[strategy] = {
            'required_evidence_coverage': total('required_covered') / total('required_count'),
            'complete_subgoal_coverage': total('complete_subgoals') / total('subgoals_count'),
            'counter_retention': total('counter_retained') / total('counter_required'),
            'actual_reference_tokens': total('actual_reference_tokens'), 'estimated_tokens': total('estimated_tokens'),
            'mean_absolute_token_error': total('absolute_token_error') / len(subset),
            'context_bytes': total('context_bytes'),
            'mean_estimated_utilization': total('estimated_utilization') / len(subset),
            'mean_actual_reference_utilization': total('actual_reference_utilization') / len(subset),
            'mean_compression_ratio': sum(ratios) / len(ratios) if ratios else None,
            'restore_attempts': attempts, 'restore_successes': total('restore_successes'),
            'restore_success_rate': total('restore_successes') / attempts if attempts else None,
            'context_loss_error_probes': total('context_loss_error_probe'),
            'context_loss_refusal_probes': total('context_loss_refusal_probe'),
        }
    return {'schema_version': 1, 'baseline': '26392432dcef70692461e6f2f50bc1bd5528f69a',
        'experiment': 'synthetic mechanism comparison, not Agent or model reasoning accuracy',
        'tokenizer': tokenizer_kind, 'gold_sha256': digest(gold_path.read_text(encoding='utf-8')),
        'strategy_config': {'A': 'legacy cap5/12000chars/3000 len-div4 tokens',
                            'B': 'legacy cap10/24000chars/6000 len-div4 tokens',
                            'C': 'cap5/12000chars/3000 multilingual; model4096-output512-reservation128; guarded fake compaction and explicit recovery'},
        'summary': totals, 'cases': rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import tiktoken  # optional experiment dependency, never imported by production
    tokenizer = tiktoken.get_encoding('cl100k_base')
    report = experiment(lambda t: len(tokenizer.encode(t, disallowed_special=())), 'tiktoken 0.12.0 cl100k_base exact reference; not Gemini/DeepSeek billed tokens')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps(report['summary'], indent=2))


if __name__ == '__main__':
    main()
