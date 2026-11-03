"""Lossless JSON reference encoding inside the existing bounded Tool Ledger.

Only repeated dictionaries are shared; facts, evidence and records are not
truncated. Hashes bind expanded content, unused/missing/cyclic definitions fail
closed, and expansion has its own hard bound. Generic Ledger limits are unchanged.
"""
from collections import Counter
from copy import deepcopy
import hashlib,json,re
from linkloom.runtime.models import MAX_INLINE_TOOL_RESULT_BYTES,_assert_inline_checkpoint_size

WIRE_SCHEMA='graph-query-wire/v1'
REF='$graph_ref'
MAX_EXPANDED_BYTES=2*1024*1024
MAX_DEFINITIONS=2048


def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf8')
def key(value):return hashlib.sha256(canonical(value)).hexdigest()


def _check_depth(value):
    stack=[(value,0)];entries=0
    while stack:
        item,depth=stack.pop();entries+=1
        if depth>64:raise ValueError('GRAPH_REFERENCE_DEPTH_LIMIT')
        if entries>MAX_EXPANDED_BYTES:raise ValueError('GRAPH_RESULT_EXPANSION_LIMIT')
        if isinstance(item,dict):stack.extend((child,depth+1) for child in item.values())
        elif isinstance(item,list):stack.extend((child,depth+1) for child in item)


def pack_graph_result(value):
    _check_depth(value)
    raw=canonical(value)
    if len(raw)>MAX_EXPANDED_BYTES:raise ValueError('GRAPH_RESULT_EXPANSION_LIMIT')
    if len(raw)<96*1024:return value
    counts=Counter()
    def collect(item):
        if isinstance(item,dict):
            if REF in item:raise ValueError('RESERVED_GRAPH_REFERENCE_KEY')
            if len(canonical(item))>=384:counts[key(item)]+=1
            for child in item.values():collect(child)
        elif isinstance(item,list):
            for child in item:collect(child)
    collect(value);definitions={}
    def encode(item,inline=False):
        if isinstance(item,dict):
            identity=key(item)
            if not inline and counts[identity]>1:
                if identity not in definitions:
                    if len(definitions)>=MAX_DEFINITIONS:raise ValueError('GRAPH_REFERENCE_LIMIT')
                    definitions[identity]=None
                    definitions[identity]=encode(item,True)
                return {REF:identity}
            return {name:encode(child) for name,child in item.items()}
        if isinstance(item,list):return [encode(child) for child in item]
        return item
    packed=dict(schema_version=WIRE_SCHEMA,value=encode(value),definitions=definitions)
    if len(canonical(packed))>MAX_INLINE_TOOL_RESULT_BYTES-2048:raise ValueError('GRAPH_WIRE_LEDGER_LIMIT')
    _assert_inline_checkpoint_size(packed,'graph_query.wire')
    return packed


def unpack_graph_result(value):
    if not isinstance(value,dict):raise ValueError('GRAPH_RESULT_OBJECT_REQUIRED')
    if value.get('schema_version')!=WIRE_SCHEMA:
        if value.get('schema_version')!='graph-query-result/v1':raise ValueError('UNKNOWN_GRAPH_RESULT_SCHEMA')
        _check_depth(value)
        if len(canonical(value))>MAX_EXPANDED_BYTES:raise ValueError('GRAPH_RESULT_EXPANSION_LIMIT')
        return value
    if set(value)!={'schema_version','value','definitions'}:raise ValueError('INVALID_GRAPH_WIRE_FIELDS')
    _assert_inline_checkpoint_size(value,'graph_query.wire')
    definitions=value['definitions']
    if (not isinstance(definitions,dict) or len(definitions)>MAX_DEFINITIONS or
        any(not isinstance(name,str) or re.fullmatch('[0-9a-f]{64}',name) is None or not isinstance(body,dict) for name,body in definitions.items())):
        raise ValueError('INVALID_GRAPH_DEFINITIONS')
    used=set();active=set();cache={};expanded=[0]
    def charge(amount):
        expanded[0]+=amount
        if expanded[0]>MAX_EXPANDED_BYTES:raise ValueError('GRAPH_RESULT_EXPANSION_LIMIT')
    def decode(item,depth=0):
        if depth>64:raise ValueError('GRAPH_REFERENCE_DEPTH_LIMIT')
        if isinstance(item,dict):
            if REF in item:
                if set(item)!={REF} or not isinstance(item[REF],str) or item[REF] not in definitions:raise ValueError('MISSING_GRAPH_REFERENCE')
                identity=item[REF];used.add(identity)
                if identity in active:raise ValueError('CYCLIC_GRAPH_REFERENCE')
                if identity in cache:
                    charge(len(canonical(cache[identity])));return deepcopy(cache[identity])
                if len(active)>=64:raise ValueError('GRAPH_REFERENCE_DEPTH_LIMIT')
                active.add(identity);body=decode(definitions[identity],depth);active.remove(identity)
                if key(body)!=identity:raise ValueError('GRAPH_REFERENCE_HASH_MISMATCH')
                cache[identity]=body;return deepcopy(body)
            charge(2+sum(len(canonical(name))+1 for name in item))
            return {name:decode(child,depth+1) for name,child in item.items()}
        if isinstance(item,list):charge(2+len(item));return [decode(child,depth+1) for child in item]
        charge(len(canonical(item)));return item
    result=decode(value['value'])
    _check_depth(result)
    if used!=set(definitions):raise ValueError('UNUSED_GRAPH_DEFINITION')
    if not isinstance(result,dict) or result.get('schema_version')!='graph-query-result/v1':raise ValueError('INVALID_EXPANDED_GRAPH_SCHEMA')
    if len(canonical(result))>MAX_EXPANDED_BYTES:raise ValueError('GRAPH_RESULT_EXPANSION_LIMIT')
    return result
