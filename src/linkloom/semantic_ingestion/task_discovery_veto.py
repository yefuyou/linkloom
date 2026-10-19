"""F-only refusal scopes. Never discover, complete or merge task work here."""
import re

from linkloom.semantic_ingestion.timestamped_text import timestamped_message_body


_CONDITIONAL = re.compile(r"如果|假如|倘若|假设|若|只要|除非|只有|一旦|当[^。；;\n]{0,40}时|前提下|后再|才(?=安排|执行|进行|完成|继续)|\b(?:if|unless|suppose|hypothetical)\b", re.I)
_NON_COMMITTAL = re.compile(
    r"建议|提议|考虑|可能|据说|未采纳|未批准|引用|(?:讨论|探讨)(?:是否|能否|要不要|如何|完成|取消|终止|撤销|撤回)|"
    r"\b(?:might|perhaps|reportedly|unadopted|suggest\w*|propos\w*|consider\w*|quoted?)\b", re.I)
_NEGATED = re.compile(
    r"取消|撤销|撤回|作废|放弃|终止|停止|不再|无需|无须|不需要|不要|不会|不完成|不执行|"
    r"不负责|不打算|不安排|不计划|不应|禁止|不得|未安排|并非|不是|没有任务|"
    r"\b(?:not|never|cancel\w*|terminat\w*|withdrawn|abandoned)\b", re.I)
_NON_ASSERTED = re.compile('|'.join((
    _CONDITIONAL.pattern, _NON_COMMITTAL.pattern, _NEGATED.pattern)), re.I)
_REFERENT = r"(?:(?:上述|前述|该|此|这项|这个)\s*(?:任务|工作|计划)|(?:this|that|the above)\s+(?:task|work|plan)\b)"
_REFERENTIAL_CANCELLATION = re.compile(
    rf"{_REFERENT}[^。！？\n]{{0,24}}(?:{_NEGATED.pattern})|"
    rf"(?:{_NEGATED.pattern})[^。！？\n]{{0,24}}{_REFERENT}", re.I)
# A bare cancellation has no named object after it. "任务取消：整理旧截图"
# names a different work item and must not retract the preceding task.
_BARE_CANCELLATION = re.compile(
    rf"^\s*(?:任务|工作|计划|(?:the\s+)?(?:task|work|plan)\b)[^。！？\n]{{0,16}}(?:{_NEGATED.pattern})了?\s*$|"
    rf"^\s*(?:{_NEGATED.pattern})[^。！？\n]{{0,16}}(?:任务|工作|计划|(?:the\s+)?(?:task|work|plan))\s*$", re.I)
_ELLIPTICAL_CANCELLATION = re.compile(r"^\s*(?:无需|无须|不需要|不再)\s*(?:继续|执行|进行)", re.I)
_CONDITIONAL_TAIL = re.compile(r"再执行|才执行|再进行|才进行|再继续|才继续|\bproceed\b", re.I)
# Only a refusal-scope delimiter for candidates already supplied by the model.
_ASSIGNMENT_START = re.compile(
    r"^\s*(?:(?:同时|另外|但|并且)\s*)?(?:安排|我(?:继续|将|会)?负责|负责人\s*[:：]|"
    r"I (?:will handle|am responsible for)|Owner\s*:)", re.I)
_SENTENCE_END = r"[。！？\n]|[.!?](?:\s+|$)"


def _quotes(text):
    # Mask quotation/code ranges without changing any source offset. Apostrophes
    # inside words are not quote delimiters (e.g. don't or Alice's).
    ranges = [(m.start(), m.end()) for m in re.finditer(
        r"(?m)^[ \t]*>[^\n]*|```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)", text)]
    code_offsets = {index for left, right in ranges for index in range(left, right)}
    stack = []
    pairs = {'“': '”', '‘': '’', '「': '」', '『': '』', '"': '"', "'": "'"}
    for index, char in enumerate(text):
        if index in code_offsets:
            continue
        closing = bool(stack and char == stack[-1][1])
        if char == "'" and (text[index + 1:index + 2].isalnum() if closing
                            else index > 0 and text[index - 1].isalnum()):
            continue
        if closing:
            start, _ = stack.pop()
            ranges.append((start, index + 1))
        elif char in pairs:
            stack.append((index, pairs[char]))
    ranges.extend((start, len(text)) for start, _ in stack)
    return ranges


def _chunks(text, delimiter):
    start = 0
    for match in re.finditer(delimiter, text):
        yield start, match.start(), text[start:match.start()]
        start = match.end()
    yield start, len(text), text[start:]


def non_asserted(text, parts, start, end):
    quoted = _quotes(text)
    visible = list(text)
    for left, right in quoted:
        visible[left:right] = [c if c in '\n。！？.!?；;，,' else ' ' for c in text[left:right]]
    visible = ''.join(visible)
    positions = [(m.start() + start, m.end() + start) for part in parts
                 for m in re.finditer(re.escape(part), text[start:end])]
    if any(left < b and a < right for a, b in positions for left, right in quoted):
        return True
    previous = None
    for sentence_start, _, sentence in _chunks(visible, _SENTENCE_END):
        refusal = conditional = False
        for offset, limit, clause in _chunks(sentence, r"[；;，,]"):
            left, right = sentence_start + offset, sentence_start + limit
            if not clause.strip():
                continue
            body = timestamped_message_body(clause.strip()) or clause
            independent = bool(_ASSIGNMENT_START.search(body))
            if independent:
                refusal = False
            conditional = conditional or bool(_CONDITIONAL.search(clause))
            refusal = refusal or bool(_NON_ASSERTED.search(clause))
            local = any(left <= a < right for a, _ in positions)
            if local and (refusal or conditional):
                return True
            non_committal = _NON_COMMITTAL.search(clause) or _CONDITIONAL.search(clause)
            # Only an actual retraction repeating the work affects another
            # occurrence. A suggestion to cancel does not cancel an active task.
            if any(part in clause for part in parts) and _NEGATED.search(clause) and not non_committal:
                return True
            modifies_previous = (not non_committal and (
                _REFERENTIAL_CANCELLATION.search(clause) or _BARE_CANCELLATION.search(body)
                or _ELLIPTICAL_CANCELLATION.search(body)))
            modifies_previous = modifies_previous or (
                _CONDITIONAL.search(clause) and _CONDITIONAL_TAIL.search(clause))
            if modifies_previous and previous and any(previous[0] <= a < previous[1] for a, _ in positions):
                return True
            # Retain uncertain antecedents across commentary. An explicit new
            # assignment closes that negative scope; it grants no task identity.
            if (local or independent) and not non_committal:
                previous = (left, right)
    return False
