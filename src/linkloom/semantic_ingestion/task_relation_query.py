"""Read-only, fail-closed views over a relation sidecar and explicit M1 sources.

No persisted ACTIVE/evidence-validity flag is trusted. Each public read takes
a SQLite read snapshot and replays the existing P1/P2/P3 proof functions.
There is no cross-call validity cache or implicit cross-database transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
import sqlite3

from linkloom.semantic_ingestion.task_observation import TaskObservation
from linkloom.semantic_ingestion.task_relation_lifecycle import (
    SCHEMA_VERSION, nonempty, read_recipe, validate_event_chain,
)
from linkloom.semantic_ingestion.task_relation_models import (
    EvidenceQuote, ObservationRef, RelationAssessment, canonical_hash,
)
from linkloom.semantic_ingestion.task_relation_store import decode_assessment, decode_comparison, decode_event
from linkloom.semantic_ingestion.task_relation_validation import assess_same_task, verify_evidence_quote, verify_relation_inputs
from linkloom.semantic_ingestion.task_relation_context import assess_relation, source_context_quote
from linkloom.semantic_ingestion.task_plan_revision import assess_plan_revision
from linkloom.semantic_ingestion.task_revision_comparison import compare_revision


@dataclass(frozen=True, slots=True)
class RelationRead:
    assessment: RelationAssessment | None
    persistent_lifecycle: str
    evidence_state: str
    current_supported: bool
    category: str
    blocking_reasons: tuple[str,...]
    premise_states: tuple[dict,...] = ()
    source_checks: tuple[dict,...] = ()
    events: tuple = ()
    context_recheck: dict | None = None

    def to_dict(self):
        return dict(assessment=self.assessment.to_dict() if self.assessment else None,
            persistent_lifecycle=self.persistent_lifecycle,evidence_state=self.evidence_state,
            current_supported=self.current_supported,category=self.category,
            blocking_reasons=list(self.blocking_reasons),premise_states=list(self.premise_states),
            source_checks=list(self.source_checks),events=[e.to_dict() for e in self.events],
            context_recheck=self.context_recheck,
            historical_scope='CAPTURED_RECORD_WITH_CURRENT_VALIDATION; NOT_AN_AS_OF_DATABASE_RECONSTRUCTION')


@dataclass(frozen=True, slots=True)
class ComparisonRead:
    historical_comparison: object | None
    current_comparison: object | None
    current_valid: bool
    blocking_reasons: tuple[str,...]
    premise_states: tuple[dict,...]

    def to_dict(self):
        return dict(historical_comparison=self.historical_comparison.to_dict() if self.historical_comparison else None,
            current_comparison=self.current_comparison.to_dict() if self.current_comparison else None,
            current_valid=self.current_valid,blocking_reasons=list(self.blocking_reasons),
            premise_states=list(self.premise_states),historical_scope='CAPTURED_COMPARISON; NOT_CURRENT_UNLESS_REVALIDATED')


def _premise_state(identity,read):
    return dict(assessment_id=identity,persistent_lifecycle=read.persistent_lifecycle,
        verdict=read.assessment.verdict if read.assessment else None,evidence_state=read.evidence_state,
        current_supported=read.current_supported,blocking_reasons=list(read.blocking_reasons))


class _ReadSnapshot:
    """One bounded read, with a per-call dependency cache only."""
    def __init__(self,owner,workspace,rows,events,comparisons,observations,m1_blocks):
        self.owner,self.workspace=owner,workspace
        self.rows={r['assessment_id']:r for r in rows}
        self.events={}
        for row in events:
            self.events.setdefault(row['assessment_id'],[]).append(row)
        self.comparisons={r['comparison_id']:r for r in comparisons}
        self.observations=observations
        self.m1_blocks=m1_blocks
        self._current_observations=None
        self.cache,self.visiting={},set()

    def current_observations(self):
        if self._current_observations is None:
            valid={}
            for observation in self.observations:
                if not isinstance(observation,TaskObservation) or observation.provenance.workspace_id!=self.workspace:
                    continue
                ref=ObservationRef.from_observation(observation)
                if self.m1_blocks.get(ref):
                    continue
                errors,_,_,_=verify_relation_inputs((observation,),self.owner.artifacts,self.owner.source_registry)
                if not errors:
                    valid[ref]=observation
            self._current_observations=tuple(valid.values())
        return self._current_observations

    def replay(self,assessment,endpoints,inventory,proposals,premises,*,current_context=False):
        args=dict(artifacts=tuple(a for a in self.owner.artifacts if a.workspace_id==self.workspace),
                  source_registry=self.owner.source_registry,context_observations=tuple(inventory))
        if assessment.predicate=='SAME_TASK':
            return assess_same_task(*endpoints,**args)
        if assessment.predicate=='REVISES_PLAN':
            if len(premises)!=1 or premises[0].assessment is None:
                return None
            identity=(assess_same_task(*endpoints,**args) if current_context else premises[0].assessment)
            return assess_plan_revision(*endpoints,same_task=identity,evidence=proposals,**args)
        return assess_relation(assessment.predicate,*endpoints,evidence=proposals,
            alias_assessments=tuple(p.assessment for p in premises if p.assessment),
            decision_store=self.owner.decision_store,**args)

    def read(self,identity):
        if identity in self.cache:
            return self.cache[identity]
        if identity in self.visiting or len(self.visiting)>=64:
            return RelationRead(None,'INVALIDATED','UNVERIFIABLE',False,'HISTORICAL',('DEPENDENCY_CYCLE_OR_DEPTH_LIMIT',))
        row=self.rows.get(identity)
        if row is None:
            return RelationRead(None,'INVALIDATED','UNVERIFIABLE',False,'HISTORICAL',('DEPENDENCY_MISSING',))
        self.visiting.add(identity)
        try:
            read=self._verify(row)
        except (ValueError,TypeError,KeyError,AttributeError,IndexError,sqlite3.DatabaseError):
            read=RelationRead(None,'INVALIDATED','UNVERIFIABLE',False,'HISTORICAL',('STORED_RECORD_INTEGRITY_FAILED',))
        finally:
            self.visiting.remove(identity)
        self.cache[identity]=read
        return read

    def _verify(self,row):
        assessment,recipe=decode_assessment(row)
        identity=assessment.assessment_id
        events=tuple(decode_event(r) for r in self.events.get(identity,()))
        lifecycle=validate_event_chain(events,self.workspace,identity)
        refs,proposals,premise_ids=read_recipe(assessment,recipe)
        errors,source_checks,inventory=set(),[],[]
        for ref in refs:
            errors.update(self.m1_blocks.get(ref,()))
            candidates=[o for o in self.observations if isinstance(o,TaskObservation)
                        and o.provenance.workspace_id==self.workspace and o.observation_id==ref.observation_id]
            if len({ObservationRef.from_observation(o).payload_hash for o in candidates})>1:
                errors.add('M1_EXTRACTION_CONFLICT')
            matching=[o for o in candidates if ObservationRef.from_observation(o)==ref]
            if not matching:
                errors.add('M1_OBSERVATION_MISSING_OR_CHANGED')
            else:
                inventory.append(matching[0])
        # Embedded snapshots are immutable audit captures, never authoritative
        # substitutes for a fresh M1 read with the same semantic payload hash.
        resolved={ObservationRef.from_observation(o):o for o in inventory}
        endpoints=[]
        for endpoint in (assessment.subject,assessment.object):
            endpoints.append(resolved.get(endpoint) if isinstance(endpoint,ObservationRef) else endpoint)
            if isinstance(endpoint,ObservationRef) and endpoint not in resolved:
                errors.add('M1_OBSERVATION_MISSING_OR_CHANGED')
        artifacts=tuple(a for a in self.owner.artifacts if a.workspace_id==self.workspace)
        for dependency in assessment.dependencies:
            span=dependency.span
            quote=EvidenceQuote(span,span.char_start,span.char_end,span.quote,'RELATION_PROPOSAL')
            codes,current=verify_evidence_quote(quote,self.workspace,artifacts,self.owner.source_registry)
            errors.update(codes)
            if current:
                source_checks.append(current.to_dict())
        # Never resolve an ambiguous directory by modification/import time.
        for dependency in assessment.dependencies:
            span=dependency.span
            current_versions=set()
            for artifact in artifacts:
                if artifact.artifact_id!=span.artifact_id:
                    continue
                from linkloom.semantic_ingestion.document_text import parse_source_text
                spans=[s.evidence_span for s in parse_source_text(artifact).segments]
                spans.append(source_context_quote(artifact).span)
                for candidate in spans:
                    registry=self.owner.source_registry
                    if registry is not None and (registry.has_evidence(self.workspace,candidate.evidence_ref)
                        and registry.evidence_hash(self.workspace,candidate.evidence_ref)==artifact.content_hash
                        and registry.evidence_version(self.workspace,candidate.evidence_ref)==artifact.artifact_version_id):
                        current_versions.add(artifact.artifact_version_id)
            if len(current_versions)>1:
                errors.add('AMBIGUOUS_CURRENT_SOURCE_VERSION')
        premises=[self.read(p) for p in premise_ids]
        states=tuple(_premise_state(p,r) for p,r in zip(premise_ids,premises))
        for premise in premises:
            if premise.persistent_lifecycle!='ACTIVE' or premise.evidence_state!='VERIFIED':
                errors.add('DEPENDENCY_NOT_CURRENT')
            if assessment.verdict=='SUPPORTED' and not premise.current_supported:
                errors.add('PREMISE_NOT_SUPPORTED')
        if assessment.predicate=='REVISES_PLAN':
            if len(premises)!=1 or premises[0].assessment is None or premises[0].assessment.predicate!='SAME_TASK':
                errors.add('INVALID_IDENTITY_PREMISE')
        elif assessment.predicate=='IN_PROJECT':
            if any(p.assessment is None or p.assessment.predicate!='PROJECT_ALIAS' for p in premises):
                errors.add('INVALID_ALIAS_PREMISE')
        elif premises:
            errors.add('UNEXPECTED_PREMISE')
        replay=None
        if all(e is not None for e in endpoints) and len(inventory)==len(refs):
            replay=self.replay(assessment,endpoints,inventory,proposals,premises)
        if replay is None:
            errors.add('REPLAY_CONTEXT_INCOMPLETE')
        else:
            if replay.assessment_id!=assessment.assessment_id:
                errors.add('ASSESSMENT_REPLAY_MISMATCH')
            if replay.verdict=='NOT_ASSESSABLE':
                errors.update(replay.reason_codes)
        context_recheck=None
        extra=[o for o in self.current_observations() if ObservationRef.from_observation(o) not in refs]
        if replay is not None and extra and all(e is not None for e in endpoints):
            guard=self.replay(assessment,endpoints,(*inventory,*extra),proposals,premises,current_context=True)
            # New unrelated observations add dependencies and change a replay
            # ID. Only a changed semantic conclusion invalidates the original
            # exact proof. New positive guard results are never published.
            semantic=('verdict','reason_codes','relation_detail','changed_fields','asserted_effective_at')
            if guard is None or any(getattr(guard,k)!=getattr(replay,k) for k in semantic):
                errors.add('CURRENT_CONTEXT_CHANGED_REASSESSMENT_REQUIRED')
                if guard:
                    errors.update(guard.reason_codes)
                    context_recheck={'authority':'DIAGNOSTIC_ONLY','assessment':guard.to_dict()}
        state='STALE' if {'STALE_EVIDENCE','M1_SOURCE_NOT_CURRENT'}.intersection(errors) else 'UNVERIFIABLE' if errors else 'VERIFIED'
        if lifecycle!='ACTIVE':
            errors.add('LIFECYCLE_'+lifecycle)
        supported=not errors and assessment.verdict=='SUPPORTED'
        category='CURRENT_CONFIRMED' if supported else 'CURRENT_ASSESSMENT' if not errors else 'HISTORICAL'
        return RelationRead(assessment,lifecycle,state,supported,category,tuple(sorted(errors)),states,tuple(source_checks),events,context_recheck)

    def comparison(self,identity,as_of=None):
        row=self.comparisons.get(identity)
        if row is None:
            return None
        try:
            historical=decode_comparison(row)
        except (ValueError,TypeError,KeyError,AttributeError):
            return ComparisonRead(None,None,False,('STORED_COMPARISON_INTEGRITY_FAILED',),())
        identity_read=self.read(historical.same_task_id)
        revision_read=self.read(historical.revision_id)
        states=tuple(_premise_state(i,r) for i,r in ((historical.same_task_id,identity_read),(historical.revision_id,revision_read)))
        errors=set()
        for read in (identity_read,revision_read):
            if read.persistent_lifecycle!='ACTIVE' or read.evidence_state!='VERIFIED':
                errors.add('COMPARISON_PREMISE_NOT_CURRENT')
                errors.update(read.blocking_reasons)
        current=None
        revision=revision_read.assessment
        if (revision is not None and revision.predicate=='REVISES_PLAN'
            and identity_read.assessment is not None and identity_read.assessment.predicate=='SAME_TASK'):
            _,recipe=decode_assessment(self.rows[revision.assessment_id])
            refs,proposals,_=read_recipe(revision,recipe)
            inventory=[]
            for ref in refs:
                match=next((o for o in self.observations if isinstance(o,TaskObservation)
                            and ObservationRef.from_observation(o)==ref),None)
                if match is not None:
                    inventory.append(match)
            # Use the original exact endpoint snapshots only after fresh M1
            # hash/field verification above; missing context remains blocked.
            reference_date=date.fromisoformat(historical.as_of) if historical.as_of else None
            comparison_args=dict(same_task=identity_read.assessment,revision=revision,
                artifacts=tuple(a for a in self.owner.artifacts if a.workspace_id==self.workspace),
                source_registry=self.owner.source_registry,evidence=proposals,
                context_observations=tuple(inventory))
            current=compare_revision(*revision.observations,as_of=reference_date,**comparison_args)
            if historical.subject!=revision.subject or historical.object!=revision.object or historical.same_task_id not in revision.premise_ids:
                errors.add('COMPARISON_ENDPOINT_OR_PREMISE_MISMATCH')
            if current.comparison_id!=historical.comparison_id:
                errors.add('COMPARISON_REPLAY_MISMATCH')
            if not errors and as_of is not None and as_of!=reference_date:
                current=compare_revision(*revision.observations,as_of=as_of,**comparison_args)
        else:
            errors.add('COMPARISON_REPLAY_CONTEXT_INCOMPLETE')
        if errors:
            current=replace(current or historical,status='BLOCKED',delta_calendar_days=None,reason_codes=tuple(sorted(errors)),
                            plan_currentness='NOT_ESTABLISHED',asserted_effective_at=None)
        valid=not errors and current.status=='COMPUTABLE'
        reasons=tuple(sorted(errors)) if errors else current.reason_codes if current.status=='BLOCKED' else ()
        return ComparisonRead(historical,current,valid,reasons,states)


class RelationQuery:
    """A missing sidecar is an error, never an invitation to create a database.

    Pass external M1 observations or an existing TaskObservationQuery. Sources
    and their current SourceReferenceRegistry must be supplied for publication.
    Querying retained records is not an arbitrary past-state reconstruction.
    """
    def __init__(self,path,*,artifacts=(),source_registry=None,observations=(),observation_query=None,decision_store=None):
        self._owns_connection=not isinstance(path,sqlite3.Connection)
        if self._owns_connection:
            source=Path(path).resolve()
            if not source.is_file():
                raise FileNotFoundError(source)
            self._connection=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        else:
            self._connection=path
        self._connection.row_factory=sqlite3.Row
        self._connection.execute('PRAGMA query_only=ON')
        versions=[r[0] for r in self._connection.execute('SELECT schema_version FROM task_relation_metadata')]
        if versions!=[SCHEMA_VERSION]:
            if self._owns_connection:
                self._connection.close()
            raise ValueError('unsupported sidecar schema')
        self.artifacts=tuple(artifacts)
        self.source_registry=source_registry
        self.observations=tuple(observations)
        self.observation_query=observation_query
        self.decision_store=decision_store

    def close(self):
        if self._owns_connection:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self,*args):
        self.close()

    def _snapshot(self,workspace):
        if not nonempty(workspace):
            raise ValueError('workspace_id required')
        self._connection.execute('SAVEPOINT relation_read')
        try:
            rows=self._connection.execute('SELECT * FROM task_relation_assessment WHERE workspace_id=?',(workspace,)).fetchall()
            events=self._connection.execute('SELECT * FROM task_relation_event WHERE workspace_id=? ORDER BY sequence',(workspace,)).fetchall()
            comparisons=self._connection.execute('SELECT * FROM task_revision_comparison WHERE workspace_id=?',(workspace,)).fetchall()
        finally:
            self._connection.execute('RELEASE relation_read')
        observations=self.observations
        m1_blocks={}
        if self.observation_query is not None:
            m1_rows=self.observation_query.list(workspace,include_history=True)
            observations=(*observations,*(r.observation for r in m1_rows))
            for row in m1_rows:
                reasons=set()
                if row.source_status!='CURRENT':
                    reasons.add('M1_SOURCE_NOT_CURRENT')
                if row.extraction_conflict:
                    reasons.add('M1_EXTRACTION_CONFLICT')
                if reasons:
                    m1_blocks.setdefault(ObservationRef.from_observation(row.observation),set()).update(reasons)
        return _ReadSnapshot(self,workspace,rows,events,comparisons,observations,m1_blocks)

    def get(self,workspace_id,assessment_id,*,include_history=False):
        # ID reads always return retained diagnostics, even when unpublished.
        snapshot=self._snapshot(workspace_id)
        return snapshot.read(assessment_id) if assessment_id in snapshot.rows else None

    def list(self,workspace_id,*,source_id=None,project=None,observation_id=None,include_history=False):
        if type(include_history) is not bool or any(v is not None and not nonempty(v) for v in (source_id,project,observation_id)):
            raise ValueError('invalid relation query filter')
        snapshot=self._snapshot(workspace_id)
        reads=tuple(snapshot.read(i) for i in sorted(snapshot.rows))
        names={project} if project else set()
        if project:
            names.update(r.assessment.object.name for r in reads if r.current_supported
                and r.assessment.predicate=='PROJECT_ALIAS' and r.assessment.subject.name==project)
        members={r.assessment.subject.observation_id for r in reads if r.current_supported
                 and r.assessment.predicate=='IN_PROJECT' and r.assessment.object.name in names}
        results=[]
        for read in reads:
            if not include_history and read.category=='HISTORICAL':
                continue
            assessment=read.assessment
            if assessment is None:
                if source_id is None and project is None and observation_id is None:
                    results.append(read)
                continue
            if source_id and not any(d.span.artifact_id==source_id for d in assessment.dependencies):
                continue
            endpoints=[ref.observation_id for ref in (assessment.subject,assessment.object) if isinstance(ref,ObservationRef)]
            if observation_id and observation_id not in endpoints:
                continue
            if project:
                explicit=any(o and o.project in names for o in assessment.observations)
                typed_project=(assessment.predicate in {'IN_PROJECT','PROJECT_ALIAS'} and assessment.object.name in names)
                if not (explicit or typed_project or members.intersection(endpoints)):
                    continue
            results.append(read)
        return tuple(results)

    def confirmed(self,workspace_id,**filters):
        return tuple(r for r in self.list(workspace_id,**filters) if r.current_supported)

    def comparison(self,workspace_id,comparison_id,*,as_of=None):
        if as_of is not None and type(as_of) is not date:
            raise ValueError('as_of must be an explicit calendar date')
        return self._snapshot(workspace_id).comparison(comparison_id,as_of)

    def plan(self,workspace_id,observation_id,*,as_of=None):
        if as_of is not None and type(as_of) is not date:
            raise ValueError('as_of must be an explicit calendar date')
        snapshot=self._snapshot(workspace_id)
        reads=[snapshot.read(i) for i in sorted(snapshot.rows)]
        revisions=[r for r in reads if r.assessment and r.assessment.predicate=='REVISES_PLAN'
                   and isinstance(r.assessment.subject,ObservationRef) and isinstance(r.assessment.object,ObservationRef)]
        # Only verified edges expand the current chain. Historical edges stay
        # visible but cannot revive a revoked plan or poison a separately
        # revalidated replacement source version.
        active_revisions=[r for r in revisions if r.current_supported]
        component={observation_id}
        while True:
            added={ref.observation_id for r in active_revisions for ref in (r.assessment.subject,r.assessment.object)
                   if component.intersection((r.assessment.subject.observation_id,r.assessment.object.observation_id))}
            if added.issubset(component):
                break
            component.update(added)
        relevant=[r for r in revisions if component.intersection(
            (r.assessment.subject.observation_id,r.assessment.object.observation_id))]
        supported=[r.assessment for r in relevant if r.current_supported]
        comparisons=[]
        for identity in snapshot.comparisons:
            read=snapshot.comparison(identity,as_of)
            historical=read.historical_comparison
            if historical is None or any(isinstance(ref,ObservationRef) and ref.observation_id in component
                                          for ref in (historical.subject,historical.object)):
                comparisons.append(read.to_dict())
        result=dict(status='NO_VERIFIED_REVISION',effective_plan=None,declared_terminal=None,future_plans=[],
                    unknown_effective_plans=[],as_of=as_of.isoformat() if as_of else None,
                    revisions=[r.to_dict() for r in relevant],blocking_reasons=[],
                    comparisons=comparisons,
                    scope='BOUNDED_VERIFIED_REVISION_CHAIN; NO_GLOBAL_CURRENT_PLAN_CLAIM')
        if any(r.category=='CURRENT_ASSESSMENT' and r.assessment.verdict=='CONFLICT' for r in relevant):
            result.update(status='CONFLICT',blocking_reasons=['COMPETING_PLAN_EVIDENCE'])
            return result
        if not supported and any(r.category=='HISTORICAL' for r in relevant):
            result.update(status='EVIDENCE_INVALID',blocking_reasons=['REVISION_CHAIN_DEPENDENCY_NOT_CURRENT'])
            return result
        groups={}
        for relation in supported:
            groups.setdefault((relation.subject,relation.object),[]).append(relation)
        supported=[]
        for claims in groups.values():
            effective_dates={r.asserted_effective_at for r in claims if r.asserted_effective_at}
            if len(effective_dates)>1:
                result.update(status='CONFLICT',blocking_reasons=['COMPETING_EFFECTIVE_DATE_CLAIMS'])
                return result
            # One verified dated claim can complete an otherwise undated
            # proof of the same endpoint pair. Multiple dated claims cannot
            # be resolved by assessment ID or storage order.
            supported.append(next((r for r in claims if r.asserted_effective_at),claims[0]))
        successors,predecessors={},{}
        for relation in supported:
            successors.setdefault(relation.object.observation_id,set()).add(relation.subject.observation_id)
            predecessors.setdefault(relation.subject.observation_id,set()).add(relation.object.observation_id)
        if any(len(values)>1 for mapping in (successors,predecessors) for values in mapping.values()):
            result.update(status='CONFLICT',blocking_reasons=['REVISION_CHAIN_BRANCH'])
            return result
        if not supported:
            return result
        nodes=set(successors)|set(predecessors)
        roots=nodes-set(predecessors)
        if len(roots)!=1:
            result.update(status='CONFLICT',blocking_reasons=['REVISION_CHAIN_CYCLE_OR_DISCONNECTED'])
            return result
        current=next(iter(roots))
        chain,seen=[],set()
        while current in successors:
            if current in seen:
                result.update(status='CONFLICT',blocking_reasons=['REVISION_CHAIN_CYCLE'])
                return result
            seen.add(current)
            child=next(iter(successors[current]))
            edge=next(r for r in supported if r.object.observation_id==current and r.subject.observation_id==child)
            chain.append(edge)
            current=child
        result['declared_terminal']=chain[-1].subject.to_dict()
        known=[r.asserted_effective_at for r in chain if r.asserted_effective_at]
        if known!=sorted(known):
            result.update(status='CONFLICT',blocking_reasons=['EFFECTIVE_DATE_DIRECTION_CONFLICT'])
            return result
        for relation in chain:
            if relation.asserted_effective_at is None or as_of is None:
                result['unknown_effective_plans'].append(relation.subject.to_dict())
            elif date.fromisoformat(relation.asserted_effective_at)>as_of:
                result['future_plans'].append(relation.subject.to_dict())
            else:
                result['effective_plan']=relation.subject.to_dict()
        if result['unknown_effective_plans']:
            result.update(status='EFFECTIVE_TIME_UNKNOWN',effective_plan=None)
        elif result['future_plans']:
            result['status']='FUTURE_EFFECTIVE'
        else:
            result['status']='EFFECTIVE'
        return result
