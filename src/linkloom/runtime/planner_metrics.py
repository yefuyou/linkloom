"""Minimal Host counters only; no question/source text in metric labels."""
from collections import Counter
from threading import Lock

class PlannerMetrics:
    def __init__(self): self._counts=Counter();self._lock=Lock()
    def record(self,name,reason=None):
        if name not in {'graph_planning_attempt','graph_planning_success','graph_query_status','legacy_fallback'}:
            raise ValueError('UNSUPPORTED_GRAPH_METRIC')
        if reason is not None and (not isinstance(reason,str) or len(reason)>128): raise ValueError('INVALID_METRIC_REASON')
        with self._lock:self._counts[(name,reason or '')]+=1
    def snapshot(self):
        with self._lock:return [dict(name=name,reason=reason or None,count=count) for (name,reason),count in sorted(self._counts.items())]
