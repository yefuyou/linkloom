"""Literal root witnesses and typed answer contracts; no question templates."""
from copy import deepcopy
import unicodedata
from linkloom.temporal_graph.models import required

KINDS=('task','decision_record','meeting','person','project','plan','alias','task_observation')
MODES=('lookup','list','history','compare')


def folded(value):return ''.join(unicodedata.normalize('NFKC',value).casefold().split())


def schema():
    text=lambda maximum:dict(type='string',maxLength=maximum)
    entity=dict(type='object',additionalProperties=False,required=['text','kind','match','candidates'],properties=dict(
        text=dict(text(160),description='Minimal literal entity name or topic nucleus, without grammar or field labels. An explicitly named Task keeps its complete title.'),kind=dict(type='string',enum=list(KINDS)),match=dict(type='string',enum=['exact','broad','previous']),
        candidates=dict(type='array',maxItems=4,items=text(128))))
    return dict(type='object',additionalProperties=False,required=['mode','result_kinds','entities'],properties=dict(
        mode=dict(type='string',enum=list(MODES)),result_kinds=dict(type='array',maxItems=3,items=dict(type='string',enum=list(KINDS))),
        entities=dict(type='array',maxItems=8,items=entity)))


INSTRUCTIONS="""
Emit intent as well as the plan. intent.mode is lookup/list/history/compare;
result_kinds names exactly the requested answer node types, not intermediate
roots. Task scalar fields (including owner) have result kind task. Decisions-only lists cannot include Task outputs; work/task lists cannot
include Decision outputs. Meeting comparisons always report both decisions and tasks, each in its own
comparison groups. This does not broaden a non-comparison membership list.
For every direct root, including both compare meetings, supply an entity witness:
text is the minimal literal name/topic nucleus copied from the question (no invented words or grammar). A named artifact keeps its full title,
kind is its directory type, candidates contains matching supplied IDs only.
Meeting witnesses separate complete date tokens from name fragments, never drop an explicit year. For an implicit comparison baseline use match=previous, text naming the current anchor from the question, and candidates containing its immediately preceding Meeting. This is an explicit workspace chronology policy, not a project/series inference, and the tool revalidates it. If the requested scope is not available, do not infer it.
An explicit Task title uses exact and its complete requested title. A broad action
or topic description uses broad and includes ALL matching confirmed candidates.
Broad match may use the supplied grounding_labels (a Task's confirmed source
Decision is context, never an inferred identity). Do not shorten an absent named
artifact into a related topic to force a match. No matching confirmed entity means
empty candidates, never a similar entity. Pure numeric meeting dates may instead
use resolve meeting_date with the exact date literal from the question.
Task owner collections use a person root; don't list every Task as a separate root.
For changes over time use intent.mode=history and inspect_history on a Decision;
this reads the confirmed bounded lineage through the query as_of, not just current
validity. Do not first resolve an old Decision using current validity. An unchanged
Decision still has a valid history. Task history uses inspect plus history output.
Meeting membership tasks already carry the meeting-time assignment; inspecting a
membership result inherits that historical snapshot unless an explicit step as_of
requests another time. Never mix later values into meeting-assignment fields.
For compare output each requested domain's comparison separately, no extra lists.
Keep output compact: complete record lists or comparison; avoid duplicate scalar
outputs already present in records. Missing objects remain applicable queries.
"""


def validate_intent(raw,context):
    if not isinstance(raw,dict) or set(raw)!={'mode','result_kinds','entities'}:raise ValueError('INVALID_GRAPH_INTENT')
    if raw['mode'] not in MODES or not isinstance(raw['result_kinds'],list) or not raw['result_kinds'] or len(raw['result_kinds'])>3 or len(set(raw['result_kinds']))!=len(raw['result_kinds']) or any(k not in KINDS for k in raw['result_kinds']):raise ValueError('INVALID_RESULT_KINDS')
    if not isinstance(raw['entities'],list) or len(raw['entities'])>8:raise ValueError('INVALID_ENTITY_WITNESSES')
    nodes={n.node_id:n for n in context.nodes};metadata={m['node_id']:m for m in context.node_metadata}
    groups=[]
    for entry in raw['entities']:
        if not isinstance(entry,dict) or set(entry)!={'text','kind','match','candidates'}:raise ValueError('INVALID_ENTITY_WITNESS')
        phrase=required(entry['text'],'entity_text',160);kind=entry['kind'];mode=entry['match'];given=entry['candidates']
        if phrase not in context.question or kind not in KINDS or mode not in {'exact','broad','previous'}:raise ValueError('ENTITY_WITNESS_NOT_IN_QUESTION')
        if not isinstance(given,list) or len(given)>context.limits.max_fanout or len(set(given))!=len(given) or any(n not in nodes for n in given):raise ValueError('INVALID_GROUNDED_CANDIDATES')
        def matches(node):
            if node.kind!=kind:return False
            info=metadata.get(node.node_id,{})
            label=info.get('entity_label') or node.label
            if kind=='meeting':
                from linkloom.temporal_graph.dates import date_tokens,date_matches
                tokens=date_tokens(phrase)
                if len(tokens)>1:return False
                if tokens:
                    literal,start,end=tokens[0];name=folded(phrase[:start]+phrase[end:])
                    return bool(info.get('meeting_date') and date_matches(info['meeting_date'],literal) and (not name or name in folded(label)))
            aliases=info.get('grounding_labels',[]) if mode=='broad' else []
            labels=[label]+aliases
            if mode=='exact' and kind in {'task','person','project','plan','alias'}:return folded(phrase)==folded(label)
            return any(folded(phrase) in folded(value) for value in labels) and (len(folded(phrase))>=2 or folded(phrase)==folded(label))
        matched=[n.node_id for n in context.nodes if matches(n)]
        previous_anchor=None
        if mode=='previous':
            if kind!='meeting' or raw['mode']!='compare' or len(matched)!=1:raise ValueError('UNRESOLVED_PREVIOUS_MEETING_ANCHOR')
            from linkloom.temporal_graph.meeting_query import nearest_previous_meeting
            previous_anchor=matched[0]
            records={n.node_id:dict(metadata.get(n.node_id,{}),kind=n.kind) for n in context.nodes if n.kind=='meeting'}
            prior=nearest_previous_meeting(records,previous_anchor)
            if prior['status']=='NOT_FOUND':raise ValueError('PREVIOUS_MEETING_NOT_FOUND')
            matched=prior['candidates']
        if any(n not in matched for n in given):
            # A guessed existing ID never creates a fact for an unmatched object.
            if not matched:groups.append(dict(entry,matched=[]));continue
            raise ValueError('ENTITY_DOES_NOT_MATCH_DIRECTORY')
        if mode=='broad' and set(given)!=set(matched):
            if not given and not matched:pass
            else:groups.append(dict(entry,matched=matched));continue
        groups.append(dict(entry,matched=matched,previous_anchor=previous_anchor))
    return dict(raw,entities=groups)


def constrain_plan(plan,intent,context):
    """Narrow only under witnessed entity/type constraints; never infer a relation."""
    from linkloom.temporal_graph.meeting_query import MeetingGraphPlan
    nodes={n.node_id:n for n in context.nodes};groups=intent['entities'];steps=list(plan.steps);outputs=list(plan.outputs)
    from linkloom.temporal_graph.dates import date_literal_in_text
    for step in steps:
        if 'meeting_date' in step['input'] and not date_literal_in_text(context.question,step['input']['meeting_date']):raise ValueError('MEETING_DATE_NOT_IN_QUESTION')
    roots={s['input']['node'] for s in steps if 'node' in s['input']}|{s['other'] for s in steps if s['operation']=='compare' and 'other' in s}
    roots|={n for s in steps if 'candidates' in s['input'] for n in s['input']['candidates']}
    witnessed={n for g in groups for n in g['candidates']}
    if roots-witnessed:raise ValueError('ROOT_WITHOUT_QUESTION_WITNESS')
    if any(not g['matched'] and g['match']=='broad' for g in groups):raise ValueError('ROOT_REFERENCE_UNRESOLVED')
    missing=[dict(text=g['text'],kind=g['kind']) for g in groups if not g['matched']]
    if missing:
        resolution=dict(id='grounded_root',operation='resolve',input={'entity':missing[0]},as_of=None)
        value=MeetingGraphPlan.from_dict(dict(steps=[resolution],outputs=[dict(step='grounded_root',field='record')],as_of=plan.as_of),nodes=context.nodes,limits=context.limits)
        return value,missing
    # Resolve broad roots before running their original dependent operations.
    prefixes=[]
    for index,g in enumerate(groups):
        ids=g['matched']
        if len(ids)<=1 or g['match']=='previous':continue
        if len(ids)>context.limits.max_fanout:raise ValueError('FANOUT_LIMIT_EXCEEDED')
        if any(s['operation']=='compare' and (s['input'].get('node') in g['candidates'] or s['other'] in g['candidates']) for s in steps):raise ValueError('AMBIGUOUS_MEETING_REFERENCE')
        resolutions={}
        for step in steps:
            inp=step['input']
            if step['operation']=='resolve' and 'candidates' in inp and set(inp['candidates'])&set(g['candidates']):step['input']={'candidates':ids}
            elif inp.get('node') in g['candidates']:
                when=step['as_of']
                if when not in resolutions:
                    key='grounded_root_'+str(index)+'_'+str(len(resolutions))
                    if any(s['id']==key for s in steps):raise ValueError('DUPLICATE_STEP')
                    resolutions[when]=key;prefixes.append(dict(id=key,operation='resolve',input={'candidates':ids},as_of=when))
                step['input']={'step':resolutions[when]}
    steps=prefixes+steps
    if intent['mode']=='history':
        kinds={}
        for step in steps:
            inp=step['input']
            kind=nodes[inp['node']].kind if 'node' in inp else inp['entity_mention']['kind'] if 'entity_mention' in inp else kinds.get(inp.get('step'))
            if step['operation']=='resolve' and 'candidates' in inp and len(inp['candidates'])==1:
                node=nodes[inp['candidates'][0]]
                if node.kind=='decision_record':step['input']={'node':node.node_id};kind=node.kind
            if step['operation']=='traverse':
                endpoints={'owner_of':({'person'},{'task','project','plan'}),'reports_to':({'person'},{'person'}),'alias_of':({'alias'},{'task','project','plan'}),'same_task':({'task_observation'},{'task'}),'decided_in':({'decision_record'},{'meeting'}),'assigned_in':({'task'},{'meeting'}),'derived_from':({'task'},{'decision_record'})}
                if step['edge_type'] in endpoints:
                    target=endpoints[step['edge_type']][1 if step['direction']=='out' else 0]
                    target=target&{n.kind for n in context.nodes} or target
                    kind=next(iter(target)) if len(target)==1 else None
            if kind=='decision_record' and (step['operation']=='inspect' or step['operation']=='resolve' and 'entity_mention' not in inp):step['operation']='inspect_history'
            kinds[step['id']]=kind
    for group in groups:
        anchor=group.get('previous_anchor')
        if anchor is None:continue
        for step in steps:
            if step['operation']=='compare' and 'other' in step and anchor in {step['input'].get('node'),step['other']} and set(group['candidates'])&{step['input'].get('node'),step['other']}:
                step['previous_anchor']=anchor;step['baseline_scope']='workspace'
    # Complete a requested multi-domain comparison using the same explicit pair.
    comparisons=[s for s in steps if s['operation']=='compare']
    if intent['mode']=='compare' and comparisons:
        from linkloom.temporal_graph.models import digest
        pair={digest([s['input'],s.get('other'),s.get('other_step'),s['as_of']]) for s in comparisons}
        if len(pair)!=1:raise ValueError('ONE_COMPARISON_PAIR_REQUIRED')
        present={s['domain'] for s in comparisons}
        for kind,domain in [('decision_record','decisions'),('task','tasks')]:
            if domain not in present:
                key='compare_'+domain
                if any(s['id']==key for s in steps):raise ValueError('DUPLICATE_STEP')
                new=deepcopy(comparisons[0]);new.update(id=key,domain=domain)
                steps.append(new);outputs.append(dict(step=key,field='comparison'))
    inferred={};available={n.kind for n in context.nodes}
    shapes={'owner_of':({'person'},{'task','project','plan'}),'reports_to':({'person'},{'person'}),
        'alias_of':({'alias'},{'task','project','plan'}),'same_task':({'task_observation'},{'task'}),
        'decided_in':({'decision_record'},{'meeting'}),'assigned_in':({'task'},{'meeting'}),
        'derived_from':({'task'},{'decision_record'})}
    requested=set(intent['result_kinds'])
    selected_steps={o['step'] for o in outputs if o['field'] in {'node','record'}}
    for step in steps:
        inp=step['input'];kinds={nodes[inp['node']].kind} if 'node' in inp else inferred.get(inp.get('step'),set())
        if 'candidates' in inp:kinds={nodes[n].kind for n in inp['candidates']}
        if 'entity_mention' in inp:kinds={inp['entity_mention']['kind']}
        if 'meeting_date' in inp:kinds={'meeting'}
        if step['operation']=='traverse':
            if step['edge_type'] in shapes:
                kinds=shapes[step['edge_type']][1 if step['direction']=='out' else 0]
                kinds=(kinds&available) or kinds
            if len(kinds)>1 and step['id'] in selected_steps and len(kinds&requested)==1:
                step['target_kind']=next(iter(kinds&requested));kinds&=requested
        if step['operation']=='compare':kinds={'decision_record' if step['domain']=='decisions' else 'task'}
        inferred[step['id']]=kinds
    kept=[]
    for selector in outputs:
        kinds=inferred[selector['step']]
        if selector['field']=='comparison' and kinds<={'task','decision_record'}:kept.append(selector);continue
        if selector['field'] in {'node','record','owner','deadline','history','comparison'} and not kinds<=requested:
            if kinds&requested:raise ValueError('UNREQUESTED_OUTPUT_NODE_TYPE')
            continue
        kept.append(selector)
    if not kept:raise ValueError('UNREQUESTED_OUTPUT_NODE_TYPE')
    needed={o['step'] for o in kept};prior={s['id']:s for s in steps};pending=list(needed)
    while pending:
        step=prior[pending.pop()]
        for parent in (step['input'].get('step'),step.get('other_step')):
            if parent and parent not in needed:needed.add(parent);pending.append(parent)
    steps=[s for s in steps if s['id'] in needed];outputs=kept
    return MeetingGraphPlan.from_dict(dict(steps=steps,outputs=outputs,as_of=plan.as_of),nodes=context.nodes,limits=context.limits),[]
