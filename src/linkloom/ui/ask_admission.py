"""Human-scoped semantic admission, without an intent model or execution loop."""
from __future__ import annotations

import re

from linkloom.runtime.adaptive_planning import source_scope_violation
from linkloom.semantic_ingestion.task_relation_models import canonical_hash
from linkloom.ui.product import ProductApplicationError
from linkloom.ui.task_investigation import ProductTaskSources

DOMAINS = ('tasks', 'source_relations')
# A legacy shortcut must explicitly refer to records, not just contain a task
# word. Unfamiliar wording uses an explicit domain and never needs this grammar.
_RECORD_REFERENCE = re.compile(r'会议|消息|文档|原文|记录|\b(meetings?|messages?|documents?|sources?|records?)\b', re.I)
_GREETING = re.compile(r'[\s!！?？,.，。]*(?:(?:你好|您好|谢谢|hello|hi|hey|thanks|thank you)[\s!！?？,.，。]*)+', re.I)
# Explicit commands/polite requests, not historical mentions of a change.
# This is an operation boundary, never a positive semantic intent classifier.
_CLAUSE = re.compile(r'[.;!?。；！？]|\b(?:and|then)\s+', re.I)
_EN_POLITE = re.compile(r'^(?:(?:please|kindly|(?:can|could|would|will)\s+you|'
    r'i\s+(?:want|need)(?:\s+you)?\s+to|i[\x27’]d\s+like(?:\s+you)?\s+to)[,\s]+)+', re.I)
_EN_WRITE = re.compile(
    r'^(?:edit|modify|archive|update|complete|assign|reassign|cancel|close|reopen|remove|move|approve|reject)\s+|'
    r'^(?:change|set)\s+(?!in\b|of\b|between\b|over\b|to\b)|'
    r'^finish\s+(?:(?:the|this|that|these|those|my|our|all)\s+)?tasks?\b|'
    r'^mark\s+(?:(?:the|this|that|these|those|my|our|all)\s+)?(?:tasks?|it)\b|'
    r'^mark\s+(?:complete|completed|done)\b|'
    r'^mark\s+(?:(?:the|this|that|these|those|all|my|our)\s+|(?:tasks?|it)\b)'
    r'.{0,120}\b(?:complete|completed|done)\b', re.I)
_EN_REPORT = re.compile(r'^update\s+(?:me|us)\b', re.I)
_ZH_POLITE = re.compile(r'^(?:(?:请[你问]?|烦请|麻烦你?|劳驾|帮我|替我|帮忙|我想|我要|可否|能否|能不能|可以帮我|能帮我)[，,\s]*)+')
_ZH_ACTION = re.compile(r'编辑|修改|更新|归档|改成|改为|设置为?|设为|标记|标为|标成|完成|关闭|重开|取消|撤销|分配|指派|移动|移除|批准')
_ZH_HISTORY = re.compile(r'^(?:(?:为|成)?(?:已完成|完成))?(?:前|后|过|的|了)')
_ZH_NOMINAL = re.compile(r'^(?:记录|内容|历史|情况|状态|时间|原因|依据|证据|差异|次数|日期|日志)')
_ZH_READ_OPERATION = r'(?:列出来|列出|说明|比较|展示|解释|核对|查看|对比|查询)(?:一下|出来|给我|下)?[吧吗呢，,\s]*'
_ZH_READ_END = re.compile(_ZH_READ_OPERATION + r'$')
_ZH_READ_CHAIN = re.compile(_ZH_READ_OPERATION + r'(?:(?:并且|并|然后|再)' + _ZH_READ_OPERATION + r')*$')


def _chinese_write_request(text):
    text = _ZH_POLITE.sub('', text)
    if not (text.startswith(('把', '将')) or _ZH_ACTION.match(text)):
        return False
    for action in _ZH_ACTION.finditer(text):
        suffix = text[action.end():]
        if _ZH_HISTORY.match(suffix):
            continue
        # In a read request, 修改记录/更新内容/编辑历史 are noun phrases.
        # A conjunction can join read actions too. Later mutation verbs are
        # independently checked by this loop, never hidden by a read suffix.
        if ((_ZH_NOMINAL.match(suffix) or _ZH_READ_CHAIN.fullmatch(suffix.strip()))
                and _ZH_READ_END.search(text)):
            continue
        return True
    return False


def _write_request(query):
    for clause in _CLAUSE.split(query):
        text = clause.strip(' ,，')
        english = _EN_POLITE.sub('', text)
        if ((_EN_WRITE.search(english) and not _EN_REPORT.match(english)) or _chinese_write_request(text)):
            return True
    return False


def refers_to_records(query, *, relationships=False):
    return (_RECORD_REFERENCE.search(query) is not None or
            (relationships and re.search(r'项目|\bproject\b', query, re.I) is not None))


def admission_restriction(query):
    if source_scope_violation(query) or _write_request(query) or re.search(
        r'\b(?:ignore|override|bypass)\b.*\b(?:instructions?|rules?|policy|checks?)\b|'
        r'\b(?:system prompt|api[ _-]?key|password|credentials?|secrets?)\b|'
        r'忽略.*(?:指令|规则)|绕过.*(?:权限|校验)|系统提示词|密钥|口令|密码|发给我', query, re.I):
        return ('INVESTIGATION_SCOPE_DENIED',
                'Only read-only investigation of this workspace is supported; instructions cannot grant sources, tools or write access.')
    if _GREETING.fullmatch(query):
        return ('INVESTIGATION_QUESTION_REQUIRED', 'Enter a question about the selected records before starting an investigation.')
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
        sources=ProductTaskSources._identity(sources))), source_count=len(sources), domains=list(DOMAINS))


def validate_context(app, workspace_id, context):
    current = source_context(app, workspace_id)
    if context['snapshot_hash'] != current['snapshot_hash']:
        raise ProductApplicationError('INVESTIGATION_CONTEXT_CHANGED',
            'The selected workspace sources changed. Refresh the investigation scope and ask again.', status=409)
    if not current['source_count']:
        raise ProductApplicationError('EMPTY_WORKSPACE', 'Import sources before selecting an investigation domain.', status=409)
