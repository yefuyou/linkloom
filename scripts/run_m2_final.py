"""Input-only fixed-candidate M2 acceptance. No evaluator/Gold imports.

materialize: M1 and exact source snapshots only, for independent pre-run review.
predict: check the committed freeze, then execute frozen P1/P2/P3/P4 once.
"""
from argparse import ArgumentParser
from datetime import UTC, datetime, timedelta
from pathlib import Path
import hashlib
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from linkloom.decision_memory.sources import SourceReferenceRegistry
from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text
from linkloom.semantic_ingestion.task_observation import TaskProposal, build_task_observation
from linkloom.semantic_ingestion.task_relation_context import source_context_quote, assess_relation
from linkloom.semantic_ingestion.task_relation_models import ChangeRef, EvidenceQuote
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_revision_comparison import compare_revision
from linkloom.semantic_ingestion.task_relation_candidates import discover_candidates
from linkloom.semantic_ingestion.task_relation_store import TaskRelationStore
from linkloom.semantic_ingestion.task_relation_query import RelationQuery
from linkloom.semantic_ingestion.task_relation_lifecycle import RelationEvent

INPUT = ROOT/'tests/fixtures/m2_final/inputs/holdout.json'
FREEZE = ROOT/'docs/requirements/m2-integration-acceptance/FREEZE.json'
NOW = datetime(2026,10,18,14,tzinfo=UTC)


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def build(case, current=None):
    artifacts = tuple(RawArtifact(workspace_id=case['workspace_id'], **s, ingestion_time=NOW) for s in case['sources'])
    parsed = [parse_source_text(a) for a in artifacts]
    registry = SourceReferenceRegistry()
    current = list(range(len(artifacts))) if current is None else current
    evidence = []
    for index in current:
        artifact = artifacts[index]
        context = source_context_quote(artifact)
        for segment in parsed[index].segments:
            span = segment.evidence_span
            evidence.append(EvidenceQuote(span,span.char_start,span.char_end,span.quote,'RELATION_PROPOSAL'))
        for span in [s.evidence_span for s in parsed[index].segments] + [context.span]:
            registry.register_evidence(case['workspace_id'], span.evidence_ref,
                content_hash=artifact.content_hash, source_version=artifact.artifact_version_id)
    # A retained old locator explicitly points at the current version, while
    # the old immutable bytes remain available for historical readback.
    for index in range(max(current)+1):
        if index in current:
            continue
        old = artifacts[index]
        replacement = next((artifacts[i] for i in current if artifacts[i].artifact_id==old.artifact_id), None)
        if replacement:
            for span in [s.evidence_span for s in parsed[index].segments] + [source_context_quote(old).span]:
                registry.register_evidence(case['workspace_id'], span.evidence_ref,
                    content_hash=replacement.content_hash, source_version=replacement.artifact_version_id)
    observations = {}
    for row in case['observations']:
        index = row['source_index']
        if index > max(current):
            continue
        observation = build_task_observation(TaskProposal.from_dict(row['task']),
            parsed[index].segments[row['segment_index']], artifacts[index])
        if not observation.verify(artifacts[index]):
            raise ValueError('M1 evidence readback failed')
        observations[row['label']] = observation
    inventory = tuple(observations[r['label']] for r in case['observations'] if r['source_index'] in current)
    return artifacts[:max(current)+1], observations, registry, tuple(evidence), inventory


def materialize(cases):
    rows = []
    for case in cases:
        artifacts, observations, _, evidence, _ = build(case)
        rows.append(dict(case_id=case['case_id'], sources=[dict(artifact_id=a.artifact_id,
            artifact_version_id=a.artifact_version_id, content_hash=a.content_hash, content=a.content,
            metadata=dict(a.metadata)) for a in artifacts], observations={k:o.to_dict() for k,o in observations.items()},
            evidence=[q.to_dict() for q in evidence]))
    return rows


def revision(case, question, current):
    artifacts, observations, registry, evidence, inventory = build(case, current)
    new, old = (observations[question[k]] for k in ('subject','object'))
    args = dict(artifacts=artifacts, source_registry=registry, context_observations=inventory)
    same = assess_same_task(new, old, assessed_at=NOW, **args)
    rev = assess_plan_revision(new, old, same_task=same, evidence=evidence, assessed_at=NOW, **args)
    comp = compare_revision(new, old, same_task=same, revision=rev, evidence=evidence, **args)
    discovery = discover_candidates(case['workspace_id'], observations=inventory,
        artifacts=tuple(artifacts[i] for i in current), source_registry=registry)
    return (same, rev, comp, evidence, artifacts, observations, registry), dict(
        question_id=question['question_id'], same_task=same.to_dict(), assessment=rev.to_dict(),
        comparison=comp.to_dict(), discovery=discovery.to_dict())


def run_case(case, folder):
    rows = []
    if case['case_id']=='H02':
        artifacts, observations, registry, evidence, inventory = build(case)
        change = case['change']
        source = artifacts[change['source_index']]
        proof = next(q for q in evidence if q.span.artifact_version_id==source.artifact_version_id
                     and q.span.ordinal==change['segment_index'])
        endpoint = ChangeRef(case['workspace_id'], change['project'], change['before'], change['after'], proof)
        discovery = discover_candidates(case['workspace_id'], observations=inventory, artifacts=artifacts, source_registry=registry)
        for question in case['questions']:
            result = assess_relation('AFFECTED_BY', observations[question['subject']], endpoint, evidence=evidence,
                artifacts=artifacts, source_registry=registry, context_observations=inventory, assessed_at=NOW)
            rows.append(dict(question_id=question['question_id'], assessment=result.to_dict()))
        from linkloom.semantic_ingestion.task_change_authority import verify_change_authority
        authority_errors, authority_checks, _ = verify_change_authority(endpoint, None, registry, artifacts)
        # No decision is fabricated. P4 must preserve SOURCE_CLAIM authority.
        return dict(case_id=case['case_id'], predictions=rows, discovery=discovery.to_dict(),
                    decision_store_supplied=False, formal_decisions_created=0,
                    approved_change_query=dict(errors=sorted(authority_errors),checks=list(authority_checks)))
    objects = []
    for index, question in enumerate(case['questions']):
        current = ([0,1] if index==0 else [0,2]) if case['case_id']=='H04' else list(range(len(case['sources'])))
        result, row = revision(case, question, current)
        objects.append(result)
        rows.append(row)
    if case['case_id']!='H04':
        return dict(case_id=case['case_id'], predictions=rows)
    path = folder/'h04.sqlite'
    workspace = case['workspace_id']
    lifecycle = {}
    for stage, objects_row in zip(('s0','s1'), objects):
        same, rev, comp, evidence, artifacts, observations, registry = objects_row
        with TaskRelationStore(path) as store:
            store.put_assessment(same)
            store.put_assessment(rev, evidence=evidence)
            store.put_comparison(comp)
            duplicate = store.put_assessment(same)
        with RelationQuery(path, artifacts=artifacts, observations=tuple(observations.values()), source_registry=registry) as query:
            lifecycle[stage] = dict(current_identity=query.get(workspace,same.assessment_id).to_dict(),
                current_revision=query.get(workspace,rev.assessment_id).to_dict(),
                old_identity=query.get(workspace,objects[0][0].assessment_id).to_dict(),
                old_revision=query.get(workspace,objects[0][1].assessment_id).to_dict(),
                old_comparison=query.comparison(workspace,objects[0][2].comparison_id).to_dict(),
                comparison=query.comparison(workspace,comp.comparison_id).to_dict(), duplicate_created=duplicate,
                confirmed=[r.assessment.assessment_id for r in query.confirmed(workspace)])
    same, rev, comp, evidence, artifacts, observations, registry = objects[1]
    with TaskRelationStore(path) as store:
        old = objects[0][1]
        previous = store.last_event(workspace, old.assessment_id)
        event = RelationEvent.create(workspace_id=workspace, assessment_id=old.assessment_id,
            event_type='RETRACTED', reason='B/v2 source correction; retract the old revision claim',
            actor='synthetic-acceptance-reviewer', previous_event_id=previous.event_id,
            recorded_at=previous.recorded_at+timedelta(seconds=1), evidence=(evidence[-1],))
        store.append_event(event)
        duplicate_event = store.append_event(event)
    with RelationQuery(path, artifacts=artifacts, observations=tuple(observations.values()), source_registry=registry) as query:
        lifecycle['s2'] = dict(old_revision=query.get(workspace,old.assessment_id).to_dict(),
            current_identity=query.get(workspace,same.assessment_id).to_dict(),
            old_comparison=query.comparison(workspace,objects[0][2].comparison_id).to_dict(),
            current_revision=query.get(workspace,rev.assessment_id).to_dict(),
            comparison=query.comparison(workspace,comp.comparison_id).to_dict(), duplicate_event_created=duplicate_event,
            confirmed=[r.assessment.assessment_id for r in query.confirmed(workspace)])
    return dict(case_id=case['case_id'], predictions=rows, lifecycle=lifecycle)


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['materialize','predict'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def deny_gold(event, arguments):
        if event=='open' and isinstance(arguments[0],(str,bytes)):
            name=str(arguments[0]).replace('\\','/').lower()
            if '/gold/' in name or '/expected/' in name:
                raise PermissionError('prediction process cannot open evaluator annotations')
    sys.addaudithook(deny_gold)
    cases = json.loads(INPUT.read_text(encoding='utf8'))['cases']
    if args.mode=='materialize':
        write(args.output, materialize(cases))
        return
    freeze = json.loads(FREEZE.read_text(encoding='utf8'))
    head = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    if subprocess.call(['git','merge-base','--is-ancestor',freeze['implementation_commit'],head],cwd=ROOT):
        raise ValueError('frozen implementation commit is not an ancestor')
    for name, expected in freeze['protected_hashes'].items():
        if digest(ROOT/name)!=expected:
            raise ValueError('frozen file changed: '+name)
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'run.json', dict(implementation_commit=freeze['implementation_commit'],
        execution_commit=head, freeze_sha256=digest(FREEZE), input_sha256=digest(INPUT),
        protocol='AUTHOR_VISIBLE_HOLDOUT; FIXED_CANDIDATES; NO_PROVIDER; NOT_EXTERNAL_BLIND'))
    for case in cases:
        folder = args.output/case['case_id']
        folder.mkdir()
        try:
            result = run_case(case, folder)
        except Exception as error:
            import traceback
            result = dict(case_id=case['case_id'], failure_stage='harness_or_contract', error_type=type(error).__name__,
                          error=str(error), traceback=traceback.format_exc())
        write(folder/'prediction.json', result)
        print(case['case_id'], 'written', flush=True)


if __name__=='__main__':
    main()
