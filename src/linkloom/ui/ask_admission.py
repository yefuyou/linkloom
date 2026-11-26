"""Human-scoped semantic admission, without an intent model or execution loop."""
from __future__ import annotations

import os
import re

from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.product import ProductApplicationError
from linkloom.ui.task_investigation import ProductTaskSources

DOMAINS = ('tasks', 'source_relations')
_GREETING = re.compile(r'[\s!！?？,.，。]*(?:(?:你好|您好|谢谢|hello|hi|hey|thanks|thank you)[\s!！?？,.，。]*)+', re.I)
# No natural-language keyword rule decides authorization. Authority comes only from structured checks (Tool ACL, workspace
# id equality, the configured security model, structured reason codes) and output protection.
_PROTECTED_ENV = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY",
                  "LINKLOOM_JEV_API_KEY", "TYPESAFE_API_KEY")
_FRAGMENT_CHARS = 48
_SKIPPED_KEYS = frozenset({"query"})  # the user's own text is echoed back and is not a host secret


def _compact(text):
    return " ".join(text.split())


def _protected_prompts():
    from linkloom.agents.team_decision import team_decision_final_instruction
    from linkloom.ui import security_gate, structured_router
    return [_compact(text) for text in (security_gate.PROMPT, structured_router.PROMPT,
                                        team_decision_final_instruction())]


def _strings(value, key=None):
    if isinstance(value, str):
        if key not in _SKIPPED_KEYS:
            yield value
    elif isinstance(value, dict):
        for name, item in value.items():
            yield from _strings(item, name)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def leaks_protected_text(value):
    """Structural output protection: True when host-held text appears verbatim in a result.

    Protected text is configured API key values and long verbatim runs of the host system prompts.
    This is an exact-match comparison against values the host owns, not a wordlist over the question."""
    keys = [item for item in (os.environ.get(name, "").strip() for name in _PROTECTED_ENV) if len(item) >= 8]
    prompts = None
    for text in _strings(value):
        if any(key in text for key in keys):
            return True
        compact = _compact(text)
        if len(compact) < _FRAGMENT_CHARS:
            continue
        prompts = _protected_prompts() if prompts is None else prompts
        if any(compact[i:i + _FRAGMENT_CHARS] in prompt
               for i in range(len(compact) - _FRAGMENT_CHARS + 1) for prompt in prompts):
            return True
    return False


def admission_restriction(query):
    """Format check only: an empty greeting is not a question. Wording never denies a question."""
    if _GREETING.fullmatch(query):
        return ('INVESTIGATION_QUESTION_REQUIRED', 'Enter a question about the selected records before starting an investigation.')
    return None


def router_safety_reason(*, operation=None, reason=None, host_reason=None):
    """Union of structured host and model denial codes; a model can only remove authority.

    The question text is never inspected: write refusal is the model's WRITE_REQUEST operation plus the
    read-only Tool ACL, workspace refusal is the Host's blocked-entity result, and secret refusal is the
    model's reason code plus output protection (leaks_protected_text)."""
    signals = {value for value in (reason, host_reason) if isinstance(value, str)}
    if operation == "WRITE_REQUEST":
        signals.add("WRITE_NOT_ALLOWED")
    for code in ("WORKSPACE_DENIED", "SECRET_OR_POLICY_OVERRIDE", "WRITE_NOT_ALLOWED",
                 "PROVIDER_PERMISSION_REQUIRED", "EVIDENCE_INVALID", "POLICY_BLOCKED"):
        if code in signals:
            return code
    return None


def parse_context(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {'domain', 'snapshot_hash'}
            or value.get('domain') not in DOMAINS
            or not isinstance(value.get('snapshot_hash'), str)
            or re.fullmatch(r'[0-9a-f]{64}', value['snapshot_hash']) is None):
        raise ProductApplicationError('INVALID_INVESTIGATION_CONTEXT',
            'Select a supported investigation domain and the current source snapshot.')
    return dict(value)


def source_context(app, workspace_id):
    _, sources = app.snapshot_investigation_sources(workspace_id)
    return dict(snapshot_hash=canonical_hash(dict(workspace_id=workspace_id,
        sources=ProductTaskSources._identity(sources))), source_count=len(sources), domains=list(DOMAINS),
        sources=[dict(source_id=s['source_id'], name=s['name'], version=s['source_version']) for s in sources])


def validate_context(app, workspace_id, context):
    current = source_context(app, workspace_id)
    if context['snapshot_hash'] != current['snapshot_hash']:
        raise ProductApplicationError('INVESTIGATION_CONTEXT_CHANGED',
            'The selected workspace sources changed. Refresh the investigation scope and ask again.', status=409)
    if not current['source_count']:
        raise ProductApplicationError('EMPTY_WORKSPACE', 'Import sources before selecting an investigation domain.', status=409)
