"""Author fresh public Dev variants and separate evaluator-only annotations.

Run before implementation/model execution. Never import into a model process.
"""
from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'tests/fixtures/task_semantic'


def main():
    public,gold=[],[]
    def add(ident,sources,tasks,queries,answers,*,projects=(),changes=()):
        public.append(dict(case_id=ident,workspace_id='semantic-dev-'+ident,
            question='阅读全部授权原文，逐项判断给定关系，保留否定、条件、冲突及缺证；只提出关系，不宣称正式审批。',
            sources=[dict(id=k,artifact_id=k,source_type='timestamped_text',content=v) for k,v in sources.items()],
            tasks=tasks,projects=list(projects),changes=list(changes),queries=queries))
        gold.append(dict(case_id=ident,relations=[dict(predicate=q[0],subject=q[1],object=q[2],
            verdict=a[0],required_sources=a[1],rationale=a[2]) for q,a in zip(queries,answers)]))
    def task(handle,source,ordinal,project,deadline):
        return dict(handle=handle,source=source,ordinal=ordinal,project=project,deadline_text=deadline)
    add('revision',{
        'plan.md':'[2026-12-01T09:00:00+08:00] 许禾: 我负责栖湾项目任务 RG-71 的网关接入，计划在2026年12月10日前完成。\n',
        'update.md':'[2026-12-02T09:00:00+08:00] 许禾: 我负责栖湾项目任务 RG-71 的网关接入。请把plan.md中的工作认作本条的同一事项；旧的交付安排到此作废，新的任务计划在2026年12月14日前完成，由此版接替，不是另开任务。\n'},
        [task('old','plan.md',0,'栖湾项目','2026年12月10日'),task('new','update.md',0,'栖湾项目','2026年12月14日')],
        [('SAME_TASK','new','old'),('REVISES_PLAN','new','old')],
        [('SUPPORTED',['plan.md','update.md'],'明确编号、同事项与接替旧交付安排。'),('SUPPORTED',['plan.md','update.md'],'明确作废并接替业务计划，截止后移四日。')])
    add('causality',{
        'choice.md':'[2026-12-02T10:00:00+08:00] 李棠: 澄川项目的短信供应商此次换成了远铃，之前使用的是岚声；这份资料尚未经过人工审核。\n',
        'work.md':'[2026-12-02T10:05:00+08:00] 许禾: 我负责澄川项目任务 CC-81 的签名适配，计划在2026年12月16日前完成；choice.md所述更换直接导致签名规范变化，所以这项工作必须重做签名映射。\n'
                  '[2026-12-02T10:06:00+08:00] 许禾: 我负责澄川项目任务 CC-82 的日志归档，计划在2026年12月16日前完成；就choice.md这次供应商更换而言，归档工作完全不受影响，无须调整。\n'
                  '[2026-12-02T10:07:00+08:00] 许禾: 我负责澄川项目任务 CC-83 的缓存优化，计划在2026年12月16日前完成。\n'},
        [task('adapt','work.md',0,'澄川项目','2026年12月16日'),task('archive','work.md',1,'澄川项目','2026年12月16日'),task('cache','work.md',2,'澄川项目','2026年12月16日')],
        [('AFFECTED_BY','adapt','change'),('AFFECTED_BY','archive','change'),('AFFECTED_BY','cache','change')],
        [('SUPPORTED',['choice.md','work.md'],'直接变更导致必须重做。'),('REFUTED',['choice.md','work.md'],'明确不受此次变更影响。'),('UNKNOWN',['choice.md','work.md'],'仅同项目同日期，缺少缓存任务因果桥接。')],
        changes=[dict(handle='change',source='choice.md',ordinal=0,project='澄川项目',before='岚声',after='远铃')])
    add('conflict',{
        'first.md':'[2026-12-01T09:00:00+08:00] 许禾: 我负责岑湖项目任务 CH-91 的回调联调，计划在2026年12月11日前完成。second.md的15日版本只是待议草案，不会取代这里的执行安排。\n',
        'second.md':'[2026-12-03T09:00:00+08:00] 许禾: 我负责岑湖项目任务 CH-91 的回调联调，计划在2026年12月15日前完成。这就是first.md里的同一任务；这里是应执行的新计划，覆盖first.md的11日安排。\n'},
        [task('old','first.md',0,'岑湖项目','2026年12月11日'),task('new','second.md',0,'岑湖项目','2026年12月15日')],
        [('SAME_TASK','new','old'),('REVISES_PLAN','new','old')],
        [('SUPPORTED',['first.md','second.md'],'显式同一任务及同编号。'),('CONFLICT',['first.md','second.md'],'两当前同权来源明确相反，不按时间裁决。')])
    add('correction',{
        'original.md':'[2026-12-01T09:00:00+08:00] 许禾: 我负责眠沙项目任务 MS-17 的报表接入，计划在2026年12月20日前完成。\n',
        'correction.md':'[2026-12-04T09:00:00+08:00] 许禾: 我负责眠沙项目任务 MS-17 的报表接入，仍是original.md中同一个事项。这里纠正一次资料笔误：22日其实是文档整理日期，业务任务从来没改过截止，计划在2026年12月20日前完成。\n'},
        [task('old','original.md',0,'眠沙项目','2026年12月20日'),task('new','correction.md',0,'眠沙项目','2026年12月20日')],
        [('SAME_TASK','new','old'),('REVISES_PLAN','new','old')],
        [('SUPPORTED',['original.md','correction.md'],'显式同事项。'),('REFUTED',['correction.md'],'资料纠错而非业务计划变更。')])
    add('independent',{
        'tasks.md':'[2026-12-01T09:00:00+08:00] 许禾: 我负责青汀项目任务 QT-41 的生产网关联调，计划在2026年12月10日前完成。\n'
                   '[2026-12-02T09:00:00+08:00] 许禾: 我负责青汀项目任务 QT-42 的沙箱网关联调，计划在2026年12月14日前完成。这是另一份独立工作，请勿与QT-41合为一个任务；两项联调共享测试结果，存在明确工作联系。\n'},
        [task('production','tasks.md',0,'青汀项目','2026年12月10日'),task('sandbox','tasks.md',1,'青汀项目','2026年12月14日')],
        [('SAME_TASK','sandbox','production'),('RELATED_TO','sandbox','production')],
        [('REFUTED',['tasks.md'],'明确另一独立任务。'),('SUPPORTED',['tasks.md'],'共享测试结果构成明确工作联系。')])
    add('alias',{
        'glossary.md':'[2026-12-01T08:00:00+08:00] 李棠: 在本期工作区，白桨只是苇岸项目的内部称呼，两个名称指向同一个项目。\n',
        'task.md':'[2026-12-01T09:00:00+08:00] 许禾: 我负责白桨的收据适配，计划在2026年12月12日前完成；这项工作归属白桨。\n'},
        [task('work','task.md',0,None,'2026年12月12日')],
        [('PROJECT_ALIAS','alias','canonical'),('IN_PROJECT','work','canonical')],
        [('SUPPORTED',['glossary.md'],'明确同项目名称。'),('SUPPORTED',['glossary.md','task.md'],'明确归属与别名桥接，不回写M1.project。')],
        projects=[dict(handle='alias',name='白桨'),dict(handle='canonical',name='苇岸项目')])
    add('conditional',{
        'change.md':'[2026-12-01T09:00:00+08:00] 李棠: 清芦项目的短信供应商由晓声改成晚铃。\n',
        'task.md':'[2026-12-02T09:00:00+08:00] 许禾: 我负责清芦项目任务 QL-55 的验收，计划在2026年12月18日前完成；如果change.md的切换将来改变签名规范，我们才考虑增加补测，目前没有证据证明需要调整。\n'},
        [task('work','task.md',0,'清芦项目','2026年12月18日')],
        [('AFFECTED_BY','work','change')],
        [('UNKNOWN',['change.md','task.md'],'条件与考虑不等于已证实直接影响，也不是明确无影响。')],
        changes=[dict(handle='change',source='change.md',ordinal=0,project='清芦项目',before='晓声',after='晚铃')])
    add('missing-year',{
        'old.md':'[2026-12-01T09:00:00+08:00] 李棠: 岚溪项目任务 LX-61：计划在12月20日前完成校验，负责人待定。\n',
        'new.md':'[2026-12-02T09:00:00+08:00] 李棠: 岚溪项目任务 LX-61仍然是old.md的原任务；原先交付计划被本条撤换，改成计划在12月23日前完成，负责人仍未确定。\n'},
        [task('old','old.md',0,'岚溪项目','12月20日'),task('new','new.md',0,'岚溪项目','12月23日')],
        [('SAME_TASK','new','old'),('REVISES_PLAN','new','old')],
        [('SUPPORTED',['old.md','new.md'],'明确同任务。'),('SUPPORTED',['old.md','new.md'],'业务计划被明确撤换；年份缺失不阻止关系判断，但不得计算日历差值。')])
    DEST.mkdir(parents=True,exist_ok=True)
    for name,data in [('inputs/dev.json',dict(schema='task-semantic-dev/v1',split='dev',cases=public)),
                      ('gold/dev.json',dict(schema='task-semantic-gold/v1',split='dev',author_visible=True,cases=gold))]:
        path=DEST/name
        path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('x',encoding='utf8') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2)
            stream.write('\n')


if __name__=='__main__':
    main()
