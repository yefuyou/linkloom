"""Fixed excerpt/offset scoring for M3-O; never used to prepare a request."""
from __future__ import annotations

from linkloom.semantic_ingestion.extraction import _parse_claim, _extract_tool_arguments
from linkloom.semantic_ingestion.document_text import parse_source_text


FIELDS = ('owner', 'project', 'deadline_text', 'vendor')
MAX_RAW_CLAIMS = 64


def _raw(response):
    try:
        payload = _extract_tool_arguments(response)
        if not isinstance(payload,dict) or not isinstance(payload.get('claims'),list):
            raise ValueError('invalid claims')
        claims = payload['claims'][:MAX_RAW_CLAIMS]
        schema = set(payload)=={'claims'} and len(payload['claims'])<=20
        for claim in claims:
            try:
                _parse_claim(claim)
                if 'task' not in claim:
                    schema = False
            except (ValueError, TypeError, KeyError):
                schema = False
        # Preserve even schema-invalid raw task objects for the raw diagnostic.
        return [c['task'] for c in claims if isinstance(c, dict)
                and c.get('claim_type') in ('PLAN', 'ACTION') and isinstance(c.get('task'), dict)], schema, len(payload['claims'])>MAX_RAW_CLAIMS
    except (ValueError, TypeError, KeyError, AttributeError):
        return [], False, False


def _matches(task, expected, source):
    text, parts = task.get('text'), task.get('description_parts')
    if not isinstance(text, str) or not isinstance(parts, list) or not all(isinstance(p, str) for p in parts):
        return False
    # Require work at the labelled occurrence. Identical task names elsewhere
    # cannot satisfy this label by string membership alone.
    if source.count(expected['text']) != 1 or source.count(text) != 1:
        return False
    base, actual = source.index(expected['text']), source.index(text)
    return all(any(p in part and actual + text.find(part) <= base + expected['text'].find(p)
                   and base + expected['text'].find(p) + len(p) <= actual + text.find(part) + len(part)
                   for part in parts if isinstance(part, str) and part in text)
               for p in expected['description_parts'])


def _score(tasks, labels, source):
    edges = [[i for i, label in enumerate(labels) if _matches(task, label, source)] for task in tasks]
    # Maximum bipartite matching; duplicates cannot inflate recall.
    assigned = {}
    def augment(candidate, seen):
        for label in edges[candidate]:
            if label in seen:
                continue
            seen.add(label)
            if label not in assigned or augment(assigned[label], seen):
                assigned[label] = candidate
                return True
        return False
    for i in range(len(tasks)):
        augment(i, set())
    fields = {f: {'correct': sum(tasks[c].get(f) == labels[l][f] for l,c in assigned.items()),
                  'total': len(assigned)} for f in FIELDS}
    return dict(expected=len(labels), predicted=len(tasks), matched=len(assigned),
        recall=len(assigned)/len(labels) if labels else None,
        precision=len(assigned)/len(tasks) if tasks else None,
        erroneous_merges=sum(len(edge)>1 for edge in edges), fields=fields,
        covered_labels=sorted({i for edge in edges for i in edge}),
        description_coverage=sum(any(i in edge for edge in edges) for i in range(len(labels))))


def _raw_work_units(tasks, artifact):
    """Structural source-message units BEFORE Host veto or field validation.

    A legal cross-message proposal must get credit for every supplied work
    excerpt. Keep unprojectable proposals too, so invalid output is not hidden.
    """
    units = []
    segments = parse_source_text(artifact).segments
    for task in tasks:
        text,parts = task.get('text'),task.get('description_parts')
        if (not isinstance(text,str) or artifact.content.count(text)!=1 or not isinstance(parts,list)
                or not parts or any(not isinstance(p,str) for p in parts)):
            units.append(task)
            continue
        start = artifact.content.index(text)
        bound = set()
        for segment in segments:
            left,right = max(start,segment.evidence_span.char_start),min(start+len(text),segment.evidence_span.char_end)
            if left >= right:
                continue
            fragment = artifact.content[left:right]
            local = [p for p in parts if p in fragment]
            if local:
                units.append(dict(task,text=fragment,description_parts=local))
                bound.update(local)
        if bound != set(parts):
            units.append(dict(task,description_parts=[p for p in parts if p not in bound]))
    return units


def _group_error(indices, allowed_groups):
    indices = set(indices)
    return len(indices)>1 and not any(indices <= set(group) for group in allowed_groups)


def score_case(artifact, labels, response, result, *, allowed_groups=()):
    source = artifact.content
    raw, schema, raw_truncated = _raw(response)
    observations = result.valid_observations + result.partial_observations
    host = [dict(text=o.raw_text,
                 description_parts=[e.quote for e in o.field_evidence if e.field == 'description'],
                 **{f:getattr(o,f) for f in FIELDS}) for o in observations]
    before, after = _score(_raw_work_units(raw,artifact), labels, source), _score(host, labels, source)
    before['erroneous_merges'] = sum(_group_error(
        [i for i,label in enumerate(labels) if _matches(task,label,source)],allowed_groups) for task in raw)
    by_id = {o.observation_id:t for o,t in zip(observations,host)}
    after['erroneous_merges'] = sum(_group_error(
        [i for oid in group.observation_ids if oid in by_id for i,label in enumerate(labels)
         if _matches(by_id[oid],label,source)],allowed_groups) for group in result.groups)
    raw_covered, host_covered = set(before['covered_labels']), set(after['covered_labels'])
    extraction_failure = result.extractions[0].failure_code if result.extractions else None
    failed = extraction_failure.value if extraction_failure else None
    return dict(raw=before, host=after, raw_proposals=len(raw), schema_valid=schema,
        raw_scan_limit=MAX_RAW_CLAIMS,
        raw_scan_truncated=raw_truncated,
        host_filtered_units=len(raw_covered-host_covered),
        false_rejections=len(raw_covered-host_covered) if schema else 0,
        false_acceptances=after['predicted']-after['matched'],
        evidence=dict(valid=sum(o.verify(artifact) for o in observations), total=len(observations)),
        raw_evidence=dict(valid=sum(isinstance(t.get('text'), str) and source.count(t['text']) == 1
            and isinstance(t.get('description_parts'), list)
            and all(isinstance(p,str) and p in t['text'] for p in t['description_parts']) for t in raw), total=len(raw)),
        failures=[f.reason for f in result.failures],
        attribution=dict(model_omission=[] if failed or not schema else sorted(set(range(len(labels)))-raw_covered),
            host_rejection=sorted(raw_covered-host_covered),
            format_failure=not schema and not response.error,
            field_evidence_insufficient=[list(g.unbound_fields) for g in result.groups if g.unbound_fields],
            provider_infrastructure_failure=response.error.code if response.error else
                failed if failed and failed.startswith(('PROVIDER_', 'EXTRACTION_PROVIDER_')) else None,
            extraction_failure=failed))
