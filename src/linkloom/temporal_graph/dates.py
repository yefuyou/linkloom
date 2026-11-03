"""Date data types only; never classify question sentences or relationship intent."""
from datetime import date
import re

def parse_date_literal(value):
    if not isinstance(value,str) or not 1<=len(value)<=40:raise ValueError('INVALID_MEETING_DATE_LITERAL')
    value=value.strip();year=None
    numeric=re.fullmatch(r'(\d{4})([-/.])(\d{1,2})\2(\d{1,2})',value)
    if numeric:year,month,day=int(numeric[1]),int(numeric[3]),int(numeric[4])
    elif re.fullmatch(r'\d{1,2}/\d{1,2}',value):month,day=map(int,value.split('/'))
    else:
        match=re.fullmatch(r'(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日',value)
        if match is None:raise ValueError('INVALID_MEETING_DATE_LITERAL')
        year=int(match[1]) if match[1] else None;month=int(match[2]);day=int(match[3])
    date(year if year is not None else 2000,month,day)
    return year,month,day


def date_matches(value,literal):
    actual=date.fromisoformat(value);year,month,day=parse_date_literal(literal)
    return actual.month==month and actual.day==day and (year is None or actual.year==year)



_DATE_TOKEN=re.compile(r'(?<![\d/年.\-])(?:\d{4}[-/.]\d{1,2}[-/.]\d{1,2}|(?:\d{4}年)?\d{1,2}月\d{1,2}日|\d{1,2}/\d{1,2})(?![\d/年月日.\-])')


def date_literal_in_text(text,literal):
    """Lex date values, preserving explicit year precision and token boundaries."""
    wanted=parse_date_literal(literal)
    for token in _DATE_TOKEN.finditer(text):
        try:parsed=parse_date_literal(token[0])
        except ValueError:continue
        if parsed==wanted:return True
    return False


def date_tokens(text):
    """Valid complete date tokens with source positions, without sentence parsing."""
    result=[]
    for match in _DATE_TOKEN.finditer(text):
        try:parse_date_literal(match[0])
        except ValueError:continue
        result.append((match[0],match.start(),match.end()))
    return result


def validate_date_mention(text,start,end):
    """Re-lex dates in the complete source, never trust a shortened local token.

    Applies to every mention kind: renaming a dated Meeting as another kind
    cannot hide a year/boundary truncation. This lexes scalar dates only.
    """
    complete={(left,right):literal for literal,left,right in date_tokens(text)}
    for (left,right),literal in complete.items():
        if max(left,start)<min(right,end) and not start<=left<right<=end:
            raise ValueError('DATE_MENTION_TOKEN_TRUNCATED')
    for literal,left,right in date_tokens(text[start:end]):
        full=complete.get((start+left,start+right))
        if full is None or parse_date_literal(full)!=parse_date_literal(literal):
            raise ValueError('DATE_MENTION_TOKEN_TRUNCATED')
