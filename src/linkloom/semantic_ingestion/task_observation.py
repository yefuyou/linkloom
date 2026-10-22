"""Source-bound task observations, without business task identity or authority."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
import hashlib
import json
import re

from linkloom.semantic_ingestion.artifacts import EvidenceSpan, RawArtifact
from linkloom.semantic_ingestion.document_text import SourceSegment, parse_source_text
from linkloom.semantic_ingestion.timestamped_text import _HEADER_PATTERN, _source_lines
from linkloom.semantic_ingestion.task_field_binding import TaskFieldBinding, FIELD_ROLES, STATES, validate_bindings


_OPTIONAL_FIELDS = ("owner", "deadline", "project", "vendor")
_FIRST_PERSON = re.compile(r"(?:^|[。；;\n])\s*(?P<cue>我(?:继续|将|会)?负责|I (?:am responsible for|will handle|will own)\b)", re.I)
_NEGATED = re.compile(r"不是|不负责|不由|未分配|尚未|待定|未确定|建议|考虑|(?:无|非)\s*(?:截止|到期|负责人|供应商|项目)|[“”\"「」]|\b(?:not|unassigned|propose|consider)\b", re.I)
_DATE = r"(?:\d{4}-\d{2}-\d{2}|\d{4}年\d{1,2}月\d{1,2}[日号]|\d{1,2}月\d{1,2}[日号]|\d{4}年\d{1,2}月)"


@dataclass(frozen=True, slots=True)
class TaskProposal:
    """Model-proposed source excerpts; none of these values are trusted yet."""
    text: str
    description_parts: tuple[str, ...]
    owner: str | None = None
    deadline_text: str | None = None
    project: str | None = None
    vendor: str | None = None
    field_bindings: tuple[TaskFieldBinding, ...] | None = None
    work_state: str | None = None

    def __post_init__(self):
        if self.field_bindings is not None:
            validate_bindings(self.field_bindings)
            if self.work_state not in STATES:
                raise ValueError('explicit model work state required')
        elif self.work_state is not None:
            raise ValueError('work state requires the field-binding contract')
        if not isinstance(self.description_parts, tuple) or len(self.description_parts) > 8:
            raise ValueError("description_parts must be a bounded tuple")
        for name, value in [("text", self.text), *[("description_part", p) for p in self.description_parts],
                            *[(n, getattr(self, n)) for n in ("owner", "deadline_text", "project", "vendor")]]:
            if value is None and name not in {"text", "description_part"}:
                continue
            limit = 2048 if name in {"text", "description_part"} else 512
            if not isinstance(value, str) or not value.strip() or len(value) > limit or "\x00" in value:
                raise ValueError(f"invalid task {name}")

    def to_dict(self):
        result = {**asdict(self), "description_parts": list(self.description_parts)}
        if self.field_bindings is None:
            result.pop('field_bindings')
            result.pop('work_state')
        else:
            result['field_bindings'] = [asdict(b) for b in self.field_bindings]
        return result

    @classmethod
    def from_dict(cls, data):
        old = {f.name for f in fields(cls)} - {'field_bindings', 'work_state'}
        if not isinstance(data, dict) or set(data) not in (old, old | {'field_bindings', 'work_state'}):
            raise ValueError("task proposal has missing or unsupported fields")
        if not isinstance(data["description_parts"], list):
            raise ValueError("description_parts must be a list")
        values = {**data, 'description_parts': tuple(data['description_parts'])}
        if 'field_bindings' in data:
            if not isinstance(data['field_bindings'], list):
                raise ValueError('field bindings must be an array')
            values['field_bindings'] = tuple(TaskFieldBinding.from_dict(b) for b in data['field_bindings'])
        return cls(**values)


@dataclass(frozen=True, slots=True)
class TaskFieldEvidence:
    field: str
    quote: str
    char_start: int
    char_end: int
    basis: str = "SOURCE_TEXT"

    def __post_init__(self):
        if self.field not in {"raw_text", "description", *_OPTIONAL_FIELDS}:
            raise ValueError("unsupported task evidence field")
        if self.basis not in {"SOURCE_TEXT", "SPEAKER"}:
            raise ValueError("unsupported task evidence basis")
        if (not isinstance(self.quote, str) or not self.quote
            or type(self.char_start) is not int or type(self.char_end) is not int
            or self.char_start < 0 or self.char_end - self.char_start != len(self.quote)):
            raise ValueError("invalid task field evidence offsets")


def _observation_id(span, start, end):
    identity = [span.workspace_id, span.artifact_version_id, span.evidence_ref, start, end]
    return "task_observation_v1_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskObservation:
    observation_id: str
    provenance: EvidenceSpan
    raw_text: str
    description: str | None
    owner: str | None
    deadline: str | None
    deadline_text: str | None
    deadline_precision: str
    deadline_role: str
    project: str | None
    vendor: str | None
    extraction_status: str
    validation_status: str
    unknown_fields: tuple[str, ...]
    validation_issues: tuple[str, ...]
    field_evidence: tuple[TaskFieldEvidence, ...]
    recorded_at: datetime
    event_time: datetime | None
    relation_status: str = "UNCONFIRMED"
    authority: str = "SUPPORTING_ONLY"
    field_bindings: tuple[TaskFieldBinding, ...] | None = None
    work_state: str | None = None

    def __post_init__(self):
        if self.field_bindings is not None:
            validate_bindings(self.field_bindings)
            if self.work_state != 'ASSERTED':
                raise ValueError('only asserted model work observations can be stored')
        elif self.work_state is not None:
            raise ValueError('work state requires the field-binding contract')
        if not isinstance(self.provenance, EvidenceSpan):
            raise ValueError("task provenance must be EvidenceSpan")
        if self.authority != "SUPPORTING_ONLY" or self.relation_status != "UNCONFIRMED":
            raise ValueError("task observations cannot assert authority or business task identity")
        if self.extraction_status not in {"EXTRACTED", "NOT_PROVIDED"}:
            raise ValueError("invalid task extraction status")
        if self.validation_status not in {"VALID", "PARTIAL", "INVALID"}:
            raise ValueError("invalid task validation status")
        if self.deadline_precision not in {"DAY", "MONTH_DAY", "MONTH", "UNKNOWN"}:
            raise ValueError("invalid deadline precision")
        if self.deadline_role not in {"TASK_DEADLINE", "UNKNOWN"}:
            raise ValueError("invalid deadline role")
        if self.deadline is not None:
            date.fromisoformat(self.deadline)
            if self.deadline_precision != "DAY" or self.deadline_role != "TASK_DEADLINE":
                raise ValueError("resolved deadline needs a complete source-grounded day")
        for value in (self.recorded_at, self.event_time):
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("task timestamps must be timezone-aware")
        if not isinstance(self.recorded_at, datetime):
            raise ValueError("recorded_at is required")
        raw = [e for e in self.field_evidence if e.field == "raw_text"]
        if len(raw) != 1 or raw[0].quote != self.raw_text:
            raise ValueError("task text must have exact evidence")
        if self.observation_id != _observation_id(self.provenance, raw[0].char_start, raw[0].char_end):
            raise ValueError("task identity does not match its immutable source location")
        for evidence in self.field_evidence:
            if evidence.basis == "SOURCE_TEXT":
                start = evidence.char_start - self.provenance.char_start
                if start < 0 or self.provenance.quote[start:start + len(evidence.quote)] != evidence.quote:
                    raise ValueError("task field is outside its source evidence")

    def to_dict(self):
        result = asdict(self)
        result["recorded_at"] = self.recorded_at.isoformat()
        result["event_time"] = self.event_time.isoformat() if self.event_time else None
        for name in ("unknown_fields", "validation_issues"):
            result[name] = list(result[name])
        result["field_evidence"] = [asdict(e) for e in self.field_evidence]
        if self.field_bindings is None:
            result.pop('field_bindings')
            result.pop('work_state')
        else:
            result['field_bindings'] = [asdict(b) for b in self.field_bindings]
        return result

    @classmethod
    def from_dict(cls, data):
        old = {f.name for f in fields(cls)} - {'field_bindings', 'work_state'}
        if not isinstance(data, dict) or set(data) not in (old, old | {'field_bindings', 'work_state'}):
            raise ValueError("stored task observation has unsupported fields")
        values = dict(data)
        if 'field_bindings' in values:
            if not isinstance(values['field_bindings'], list):
                raise ValueError('stored field bindings must be an array')
            values['field_bindings'] = tuple(TaskFieldBinding.from_dict(b) for b in values['field_bindings'])
        values["provenance"] = EvidenceSpan(**values["provenance"])
        values["field_evidence"] = tuple(TaskFieldEvidence(**e) for e in values["field_evidence"])
        for name in ("unknown_fields", "validation_issues"):
            values[name] = tuple(values[name])
        values["recorded_at"] = datetime.fromisoformat(values["recorded_at"])
        values["event_time"] = datetime.fromisoformat(values["event_time"]) if values["event_time"] else None
        return cls(**values)

    def verify(self, artifact: RawArtifact) -> bool:
        """Replay source binding, not semantic truth or storage authenticity.

        Saved consumers must also check their external ObservationRef payload
        hash; a consistent rewrite of model declarations is not authenticated here.
        """
        if not self.provenance.verify(artifact):
            return False
        if any(artifact.content[e.char_start:e.char_end] != e.quote for e in self.field_evidence):
            return False
        segment = next((s for s in parse_source_text(artifact).segments
                        if s.evidence_span == self.provenance), None)
        if segment is None:
            return False
        proposed = ({b.field: b.value for b in self.field_bindings} if self.field_bindings is not None else
                    dict(owner=self.owner, deadline=self.deadline_text, project=self.project, vendor=self.vendor))
        proposal = TaskProposal(self.raw_text, tuple(e.quote for e in self.field_evidence if e.field == "description"),
                                proposed['owner'], proposed['deadline'], proposed['project'], proposed['vendor'], self.field_bindings, self.work_state)
        expected = build_task_observation(proposal, segment, artifact)
        if self.field_bindings is not None and any(getattr(self, name) != getattr(expected, name) for name in
                ('deadline_text', 'validation_status', 'validation_issues', 'unknown_fields', 'work_state')):
            return False
        return all(getattr(self, name) == getattr(expected, name) for name in
                   ("description", "owner", "deadline", "deadline_precision", "deadline_role", "project", "vendor",
                    "field_evidence", "event_time"))


def _affirmative_clause(text, position):
    start = max((m.end() for m in re.finditer(r"[，,。；;\n]", text[:position])), default=0)
    end_match = re.search(r"[，,。；;\n]", text[position:])
    end = position + end_match.start() if end_match else len(text)
    return _NEGATED.search(text[start:end]) is None


def _deadline_role_match(text, token):
    literal = re.escape(token)
    prefix = r"(?:截止(?:日期|时间)?|到期(?:日期|日)?|最迟|最晚|deadline|due(?: date)?|due by|by|before|no later than)\s*[:：为是]?\s*"
    suffix = r"\s*[之以]?前\s*(?:完成|提交|交付|上线|验收)"
    matches = [*re.finditer(prefix + literal, text, re.I), *re.finditer(literal + suffix, text)]
    return next((match for match in matches if _affirmative_clause(text, match.start())), None)


def _deadline(text, token):
    """Interpret only a role-bound explicit date; never fill year/day from metadata."""
    if _deadline_role_match(text, token) is None:
        return None, "UNKNOWN", "UNKNOWN", "DEADLINE_ROLE_UNRESOLVED"
    return _date_value(token)


def _date_value(token, *, month_only=False):
    """Calendar syntax only; role comes from the applicable extraction contract."""
    if month_only and re.fullmatch(r'\d{1,2}月', token):
        month = int(token[:-1])
        if 1 <= month <= 12:
            return None, 'MONTH', 'TASK_DEADLINE', 'DEADLINE_YEAR_AND_DAY_MISSING'
    if not re.fullmatch(_DATE, token):
        return None, "UNKNOWN", "UNKNOWN", "DEADLINE_INVALID"
    numbers = [int(v) for v in re.findall(r"\d+", token)]
    try:
        if len(numbers) == 3:
            return date(*numbers).isoformat(), "DAY", "TASK_DEADLINE", None
        if "年" in token:
            date(numbers[0], numbers[1], 1)
            return None, "MONTH", "TASK_DEADLINE", "DEADLINE_DAY_MISSING"
        date(2000, *numbers)  # Validate month/day without inventing an observed year.
        return None, "MONTH_DAY", "TASK_DEADLINE", "DEADLINE_YEAR_MISSING"
    except ValueError:
        return None, "UNKNOWN", "UNKNOWN", "DEADLINE_INVALID"


def _speaker_evidence(artifact, span, speaker):
    # Reuse the canonical parser's header grammar and Unicode line offsets.
    headers = [(line, match) for line in _source_lines(artifact.content)
               if line.start < span.char_start and (match := _HEADER_PATTERN.match(line.text))]
    line, match = headers[-1]
    raw_speaker = match.group("speaker")
    if raw_speaker.strip() != speaker:
        raise ValueError("speaker header does not match the canonical source message")
    start = line.start + match.start("speaker") + len(raw_speaker) - len(raw_speaker.lstrip())
    return TaskFieldEvidence("owner", speaker, start, start + len(speaker), "SPEAKER")


def _owner_role_match(text, owner):
    labelled = re.finditer(r"(?:负责人|\bowner\b|\bassigned to\b)\s*[:：]?\s*(?P<owner>[^，,。；;\n]+)", text, re.I)
    for match in labelled:
        if match.group("owner").strip(" .!?") == owner and _affirmative_clause(text, match.start()):
            return match
    # Require a full subject at the start of a clause, not a name suffix.
    pattern = r"(?:^|[，,。；;\n])\s*(?:由\s*)?" + re.escape(owner) + r"\s*(?:负责|is responsible for|will handle)"
    return next((match for match in re.finditer(pattern, text, re.I)
                 if _affirmative_clause(text, match.end() - 1)), None)


def _entity_role_match(text, value, field):
    markers = r"项目|工程|计划|project" if field == "project" else r"供应商|供方|vendor|supplier"
    for match in re.finditer(re.escape(value), text):
        if not _affirmative_clause(text, match.start()):
            continue
        prefix_role = re.search(rf"(?:{markers})\s*[:：]?\s*$", text[:match.start()], re.I)
        suffix_role = re.match(rf"\s*(?:{markers})", text[match.end():], re.I)
        internal_role = (re.search(r"(?:项目|工程|计划)$", value) if field == "project"
                         else re.match(r"(?:供应商|供方)", value))
        if prefix_role or suffix_role or internal_role:
            return match
    return None


def _model_fields(proposal, raw, segment, artifact, bind, issues):
    """Bind model-declared roles to one canonical message, without phrase rules.

    A name/date occurrence alone is insufficient: require the task-work witness,
    an explicit current role/state and an exact value or canonical speaker link.
    These are evidence-valid model interpretations, not independent proof.
    """
    values = dict(owner=None, deadline=None, deadline_text=None, project=None, vendor=None)
    precision = role = 'UNKNOWN'
    canonical = next(s for s in parse_source_text(artifact).segments if s.evidence_span == segment.evidence_span)
    speaker_proofs = []
    for declaration in proposal.field_bindings:
        field, value = declaration.field, declaration.value
        supplied = getattr(proposal, 'deadline_text' if field == 'deadline' else field)
        if declaration.state != 'ASSERTED' or declaration.role != FIELD_ROLES[field]:
            if value is not None:
                issues.append(field.upper() + '_ROLE_UNRESOLVED')
            continue
        quote, task = declaration.quote, declaration.task_quote
        valid = (value is not None and value == supplied
                 and declaration.message_ordinal == canonical.ordinal
                 and bool(quote) and raw.count(quote) == 1
                 and bool(task) and task in proposal.description_parts
                 and raw.count(task) == 1)
        if declaration.basis == 'SPEAKER':
            valid = valid and canonical.speaker == value and segment.speaker == canonical.speaker
        else:
            valid = valid and bool(value) and quote.count(value) == 1 and len(quote) > len(value)
        if not valid:
            issues.append(field.upper() + '_EVIDENCE_INVALID')
            continue
        if field == 'deadline':
            day, precision, role, issue = _date_value(value, month_only=True)
            if issue:
                issues.append(issue)
            if role != 'TASK_DEADLINE':
                continue
            values.update(deadline=day, deadline_text=value)
        else:
            values[field] = value
        bind(field, quote, raw.index(quote))
        if declaration.basis == 'SPEAKER':
            speaker_proofs.append(_speaker_evidence(artifact, segment.evidence_span, value))
    return values, precision, role, speaker_proofs


def build_task_observation(proposal: TaskProposal | None, segment: SourceSegment,
                           artifact: RawArtifact) -> TaskObservation:
    if not segment.evidence_span.verify(artifact):
        raise ValueError("task source does not match immutable evidence")
    span = segment.evidence_span
    issues = []
    proposal_supplied = proposal is not None
    raw = proposal.text if proposal else span.quote
    if raw not in span.quote or span.quote.count(raw) != 1:
        raw = span.quote
        issues.append("TASK_TEXT_UNGROUNDED_OR_AMBIGUOUS")
        proposal = None
    start = span.char_start + span.quote.index(raw)
    evidence = [TaskFieldEvidence("raw_text", raw, start, start + len(raw))]

    def bind(field, quote, offset=None):
        offset = raw.find(quote) if offset is None else offset
        if offset < 0:
            return False
        evidence.append(TaskFieldEvidence(field, quote, start + offset, start + offset + len(quote)))
        return True

    parts = proposal.description_parts if proposal else ()
    description = None
    if parts and all(part in raw for part in parts):
        for part in parts:
            bind("description", part)
        description = "；".join(parts)
    elif parts:
        issues.append("DESCRIPTION_UNGROUNDED")
    owner = project = vendor = deadline = deadline_text = None
    precision = role = "UNKNOWN"
    if proposal and proposal.field_bindings is not None:
        resolved, precision, role, speaker_proofs = _model_fields(proposal, raw, segment, artifact, bind, issues)
        owner, deadline, deadline_text, project, vendor = (resolved[n] for n in
            ('owner', 'deadline', 'deadline_text', 'project', 'vendor'))
        evidence.extend(speaker_proofs)
    elif proposal:
        if proposal.owner:
            named = _owner_role_match(raw, proposal.owner)
            cue = next((match for match in _FIRST_PERSON.finditer(span.quote)
                        if start <= span.char_start + match.start("cue") < start + len(raw)
                        and _affirmative_clause(span.quote, match.start("cue"))), None)
            canonical = next((s for s in parse_source_text(artifact).segments if s.evidence_span == span), None)
            speaker = getattr(canonical, "speaker", None)
            if named:
                owner = proposal.owner
                bind("owner", named.group(), named.start())
            elif cue and speaker == proposal.owner and getattr(segment, "speaker", None) == speaker:
                owner = speaker
                bind("owner", cue.group("cue"), span.char_start + cue.start("cue") - start)
                evidence.append(_speaker_evidence(artifact, span, speaker))
            else:
                issues.append("OWNER_UNGROUNDED")
        for name in ("project", "vendor"):
            value = getattr(proposal, name)
            if value is not None:
                match = _entity_role_match(raw, value, name)
                if match:
                    bind(name, value, match.start())
                    if name == "project":
                        project = value
                    else:
                        vendor = value
                else:
                    issues.append(name.upper() + "_UNGROUNDED")
        if proposal.deadline_text:
            if proposal.deadline_text not in raw:
                issues.append("DEADLINE_UNGROUNDED")
            else:
                deadline, precision, role, issue = _deadline(raw, proposal.deadline_text)
                if issue:
                    issues.append(issue)
                if role == "TASK_DEADLINE":
                    deadline_text = proposal.deadline_text
                    match = _deadline_role_match(raw, deadline_text)
                    bind("deadline", match.group(), match.start())
    values = {"description": description, "owner": owner, "deadline": deadline, "project": project, "vendor": vendor}
    unknown = tuple(name for name, value in values.items() if value is None)
    return TaskObservation(
        _observation_id(span, start, start + len(raw)), span, raw, description, owner,
        deadline, deadline_text, precision, role, project, vendor,
        "EXTRACTED" if proposal_supplied else "NOT_PROVIDED", "INVALID" if description is None else "PARTIAL" if unknown or issues else "VALID",
        unknown, tuple(issues), tuple(evidence), artifact.ingestion_time,
        getattr(next((s for s in parse_source_text(artifact).segments if s.evidence_span == span), None), "event_time", None),
        field_bindings=proposal.field_bindings if proposal else None,
        work_state=proposal.work_state if proposal else None,
    )
