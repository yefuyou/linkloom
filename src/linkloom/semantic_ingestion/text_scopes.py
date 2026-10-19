"""Structural quote/code ranges, with original Unicode offsets preserved."""
import re


def quoted_ranges(text):
    ranges = [(m.start(),m.end()) for m in re.finditer(
        r'(?m)^[ \t]*>[^\n]*|```[\s\S]*?(?:```|$)|`[^`\n]*(?:`|$)',text)]
    code_offsets = {i for left,right in ranges for i in range(left,right)}
    stack = []
    pairs = {'“':'”','‘':'’','「':'」','『':'』','"':'"',"'":"'"}
    for i,char in enumerate(text):
        if i in code_offsets:
            continue
        closing = bool(stack and char == stack[-1][1])
        if char == "'" and (text[i+1:i+2].isalnum() if closing else i>0 and text[i-1].isalnum()):
            continue
        if closing:
            start,_ = stack.pop()
            ranges.append((start,i+1))
        elif char in pairs:
            stack.append((i,pairs[char]))
    ranges.extend((start,len(text)) for start,_ in stack)
    return ranges


def unquoted_text(text):
    visible = list(text)
    for left,right in quoted_ranges(text):
        visible[left:right] = ['\n' if c=='\n' else ' ' for c in text[left:right]]
    return ''.join(visible)
