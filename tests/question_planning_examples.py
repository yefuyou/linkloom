"""Explicit FakeModel scripts, NOT an implementation of language understanding.

These authored responses exercise valid paths for the public inputs in order.
Neither production planner nor live runner imports this module.
"""
from copy import deepcopy


def row(identity, kind, question, handles=(), *, predicate='', field='', target='',
        depends=(), missing=(), sources=()):
    return dict(id=identity, kind=kind, question=question, handles=list(handles),
                source_handles=list(sources), depends_on=list(depends), predicate=predicate,
                field=field, target_kind=target, missing_entities=list(missing))


def scripted_outputs():
    owner = row('owner', 'inspect', '联调工作的负责人是谁？', ('old',), field='owner')
    project = row('project', 'relation', '联调是否归属交付组项目？', ('old', 'delivery'), predicate='IN_PROJECT')
    impact = row('impact', 'relation', '这次切换是否直接影响联调？', ('old', 'change'), predicate='AFFECTED_BY')
    identity = row('identity', 'relation', '新旧记录是否同一任务？', ('new', 'old'), predicate='SAME_TASK')
    revision = row('revision', 'relation', '新记录是否替换原业务安排？', ('new', 'old'),
                   predicate='REVISES_PLAN', depends=('identity',))
    dates = row('dates', 'compare_dates', '核对新旧截止日期的精度、角色和边界。', ('new', 'old'),
                depends=('identity', 'revision'))
    clarify = row('clarify', 'clarify', '请说明要调查的业务对象和范围。', missing=('具体业务对象',))
    discover = row('discover', 'discover', '先找具体切换及遗漏的相关任务。', ('old', 'new'),
                   target='change', missing=('供应商切换实体',))
    compound_identity = deepcopy(identity)
    compound_identity['depends_on'] = ['impact']
    compound_owner = deepcopy(owner)
    compound_owner['depends_on'] = ['impact']
    conflict = deepcopy(impact)
    conflict['source_handles'] = ['s_change', 's_counter']
    ambiguity = row('clarify', 'clarify', '核对曙光所指项目的完整身份。',
                    ('delivery', 'purchasing'), missing=('同名项目的唯一范围',))
    membership = row('membership', 'relation', '核验任务是否归属采购组？', ('other', 'purchasing'),
                     predicate='IN_PROJECT', depends=('clarify',))
    step_sets = [[owner], [project], [impact], [identity], [identity, revision, dates],
                 [conflict], [ambiguity, membership], [identity],
                 [impact, compound_identity, compound_owner, revision, dates],
                 [clarify], [], [discover]]
    outputs = [dict(steps=deepcopy(steps), uncertainties=[], refusal_reasons=[]) for steps in step_sets]
    outputs[5]['uncertainties'] = ['正反证矛盾未解决，不能选定结论。']
    outputs[10]['refusal_reasons'] = ['Planner 无权授予审批或数据库写入权限。']
    outputs[11]['uncertainties'] = ['候选召回不完整不能证明无关系；发现后重新绑定端点规划。']
    return outputs
