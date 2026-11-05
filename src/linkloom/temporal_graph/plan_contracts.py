"""Shared traversal cardinality/types; plans never establish facts or permissions."""
from linkloom.temporal_graph.models import EDGE_TYPES

LIST_RELATIONS=frozenset({('decided_in','in'),('assigned_in','in'),('assigned_in','out'),
    ('derived_from','in'),('owner_of','out'),('reports_to','in'),('alias_of','in'),('same_task','in')})
ENDPOINTS={
    'owner_of':({'person'},{'task','project','plan'}), 'reports_to':({'person'},{'person'}),
    'alias_of':({'alias'},{'task','project','plan'}), 'same_task':({'task_observation'},{'task'}),
    'decided_in':({'decision_record'},{'meeting'}), 'assigned_in':({'task'},{'meeting'}),
    'derived_from':({'task'},{'decision_record'})}


def result_kinds(step,incoming):
    if step['operation']!='traverse':return incoming
    if step['edge_type']=='supersedes':kinds=set(incoming)
    else:
        pair=ENDPOINTS[step['edge_type']]
        kinds=set(pair[1 if step['direction']=='out' else 0])
    if 'target_kind' in step:
        kinds&={step['target_kind']}
        if not kinds:raise ValueError('TARGET_KIND_NOT_VALID_FOR_RELATION')
    return kinds


def traversal_constraints():
    """Compact enums for every legal edge/direction/cardinality/selection tuple."""
    groups={}
    for direction in ('in','out'):
        for edge in sorted(EDGE_TYPES):
            cards=('single','list') if (edge,direction) in LIST_RELATIONS else ('single',)
            # The only latest operation retains its existing temporal semantics.
            if (edge,direction)==('assigned_in','out'):
                profiles=[(('single',),('all','latest')),(('list',),('all',))]
            else:profiles=[(cards,('all',))]
            for cardinalities,selections in profiles:
                groups.setdefault((direction,cardinalities,selections),[]).append(edge)
    return dict(anyOf=[dict(properties=dict(edge_type=dict(enum=edges),direction=dict(enum=[direction]),
        cardinality=dict(enum=list(cards)),selection=dict(enum=list(selections))))
        for (direction,cards,selections),edges in groups.items()])
