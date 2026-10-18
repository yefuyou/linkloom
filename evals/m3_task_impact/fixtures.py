"""Public synthetic sources only. This module never loads annotations."""
from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path

from linkloom.semantic_ingestion.artifacts import EvidenceSpan, RawArtifact
from linkloom.semantic_ingestion.document_text import parse_source_text

ROOT = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False).encode('utf8')).hexdigest()


def load_inputs():
    """Only Dev is executable in this preparation round."""
    data = json.loads((ROOT / 'data/dev/inputs.json').read_text(encoding='utf8'))
    if data['schema'] != 'm3-input/v1' or data['split'] != 'dev':
        raise ValueError('unsupported input dataset')
    return data['cases']


def source_index(case):
    artifacts, spans, current = {}, {}, {}
    for source in case['sources']:
        doc = source['doc_id']
        if doc in artifacts:
            raise ValueError('duplicate document')
        artifact = RawArtifact(workspace_id=case['workspace_id'], artifact_id=source['artifact_id'],
            source_type='timestamped_text', content=source['content'],
            ingestion_time=datetime(2026,10,18,tzinfo=UTC))
        artifacts[doc] = artifact
        current[doc] = source['current']
        parsed = parse_source_text(artifact)
        if not parsed.segments:
            raise ValueError('fixture contains no parseable messages')
        for i, segment in enumerate(parsed.segments,1):
            spans[f'{doc}:{i}'] = segment.evidence_span
    return artifacts, spans, current


def proof(case, ref):
    """Deterministic source locator; contains no task extraction or Gold."""
    _, spans, _ = source_index(case)
    return dict(ref=ref, span=asdict(spans[ref]))


def verify_proof(case, evidence, *, require_current=False):
    try:
        artifacts, spans, current = source_index(case)
        ref = evidence['ref']
        doc = ref.rsplit(':',1)[0]
        span = EvidenceSpan(**evidence['span'])
        return (span == spans[ref] and span.verify(artifacts[doc])
                and (not require_current or current[doc]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
