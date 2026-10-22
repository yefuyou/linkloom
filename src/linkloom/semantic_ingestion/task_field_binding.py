"""Bounded model role declarations. Source verification does not prove semantics."""
from dataclasses import dataclass, fields


FIELD_ROLES = {'owner': 'CURRENT_OWNER', 'deadline': 'TASK_DEADLINE',
               'project': 'TASK_PROJECT', 'vendor': 'TASK_VENDOR'}
ROLES = {*FIELD_ROLES.values(), 'HISTORICAL_OWNER', 'CANDIDATE_OWNER',
         'MEETING_DATE', 'TEST_WINDOW', 'OTHER', 'UNKNOWN'}
STATES = {'ASSERTED', 'AMBIGUOUS', 'CONDITIONAL', 'NEGATED', 'HISTORICAL',
          'REPORTED', 'WITHDRAWN', 'NOT_STATED'}


@dataclass(frozen=True, slots=True)
class TaskFieldBinding:
    field: str
    value: str | None
    role: str
    state: str
    quote: str
    task_quote: str
    basis: str
    message_ordinal: int

    def __post_init__(self):
        if self.field not in FIELD_ROLES or self.role not in ROLES or self.state not in STATES:
            raise ValueError('invalid task field role declaration')
        if self.basis not in {'SOURCE_TEXT', 'SPEAKER'} or (self.basis == 'SPEAKER' and self.field != 'owner'):
            raise ValueError('invalid task field basis')
        for name, limit in (('value', 512), ('quote', 2048), ('task_quote', 2048)):
            value = getattr(self, name)
            if value is None and name == 'value':
                continue
            if not isinstance(value, str) or len(value) > limit or '\x00' in value:
                raise ValueError('invalid bounded field text')
        if self.value is not None and not self.value.strip():
            raise ValueError('empty field value')
        # Discovery admits at most 16000 characters; the exact ordinal must also
        # match a canonical parsed message during source binding.
        if type(self.message_ordinal) is not int or not 0 <= self.message_ordinal < 16000:
            raise ValueError('invalid canonical message ordinal')

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or set(data) != {f.name for f in fields(cls)}:
            raise ValueError('invalid task field binding shape')
        return cls(**data)


def validate_bindings(bindings):
    if (not isinstance(bindings, tuple) or len(bindings) != 4
            or any(not isinstance(b, TaskFieldBinding) for b in bindings)
            or {b.field for b in bindings} != set(FIELD_ROLES)):
        raise ValueError('one binding per task field required')


def binding_schema():
    properties = dict(field=dict(type='string', enum=list(FIELD_ROLES)),
        value=dict(type=['string', 'null'], maxLength=512),
        role=dict(type='string', enum=sorted(ROLES)), state=dict(type='string', enum=sorted(STATES)),
        quote=dict(type='string', maxLength=2048), task_quote=dict(type='string', maxLength=2048),
        basis=dict(type='string', enum=['SOURCE_TEXT', 'SPEAKER']),
        message_ordinal=dict(type='integer', minimum=0, maximum=15999))
    return dict(type='array', minItems=4, maxItems=4,
        items=dict(type='object', properties=properties, required=list(properties), additionalProperties=False))
