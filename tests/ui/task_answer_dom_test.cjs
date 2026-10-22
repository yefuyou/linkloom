const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const context = {window: {}};
vm.runInNewContext(fs.readFileSync('src/linkloom/ui/static/product-task-result.js', 'utf8'), context);
const escapeHtml = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
const helpers = {escapeHtml, t: (en, zh) => zh, date: escapeHtml,
  citation: e => `<blockquote>${escapeHtml(e.quote)}</blockquote>`};
const observation = (id, owner, deadline, role = 'TASK_DEADLINE') => ({
  observation_id: id, description: '日志迁移', owner, deadline, deadline_text: deadline || '六月下旬',
  deadline_role: role, deadline_precision: deadline ? 'DAY' : 'UNKNOWN',
  unknown_fields: owner ? [] : ['owner', 'deadline'], validation_issues: [],
  provenance: {evidence_ref: id, quote: `日志迁移 ${owner || '负责人待定'}`},
  field_evidence: [{field: 'description', quote: '日志迁移', char_start: 0, char_end: 4}],
});
function result(status = 'PARTIAL') {
  return {status, query: '任务是否延期？', reviewer: {accepted: true}, preparation: {execution_kind: 'FAKE', gaps: []},
    evidence: [{evidence_ref: 'a', source_id: 'source-a', source_name: '会议一', source_version: 'v1', line_start: 1, line_end: 1},
      {evidence_ref: 'b', source_id: 'source-b', source_name: '会议二', source_version: 'v2', line_start: 2, line_end: 2}],
    task_review: {observations: [observation('a', '林枫', '2029-06-20'), observation('b', null, null, 'UNKNOWN')],
      proposals: [{predicate: 'REVISES_PLAN', proposed_verdict: 'SUPPORTED', evidence_status: 'EVIDENCE_VALID',
        subject: {observation_id: 'b'}, object: {observation_id: 'a'}, supporting_evidence: [],
        counter_evidence: [], missing_evidence: ['缺少替代计划的明确记录']}],
      steps: [{step_id: 'dates', kind: 'compare_dates', question: '延期几天？', status: 'BLOCKED',
        blocking_reasons: ['DEPENDENCY_NOT_SUPPORTED'], comparisons: []}], next_actions: []}};
}
const render = r => context.window.renderTaskAnswer(r, helpers);
const html = render(result());
assert.match(html, /已找到来源记录/);
assert.match(html, /正式业务状态.*尚未确认/);
assert.match(html, /模型倾向支持，尚未确认/);
assert.match(html, /缺少替代计划的明确记录/);
assert.match(html, /前置关系尚未确认/);
assert.match(html, /data-action="inspect-source" data-id="source-a"/);
assert.match(html, /字段解释仍待复核/);
assert.match(html, /字段证据与原始消息/);
assert.match(html, /原文片段/);
assert.doesNotMatch(html, /已确认延期|已批准|日历日差值.*4/);
const second = html.split('data-task-observation="b"')[1].split('</article>')[0];
assert.doesNotMatch(second, /林枫|2029-06-20/);
assert.match(second, /尚未确定/);
assert.match(second, /日期含义尚未确定/);
assert.ok(html.indexOf('待确认事项') < html.indexOf('task-source-records'));
assert.ok(html.indexOf('task-source-records') < html.indexOf('task-investigation-details'));
for (const verdict of ['UNKNOWN', 'CONFLICT', 'REFUTED']) {
  const r = result(); r.task_review.proposals[0].proposed_verdict = verdict;
  assert.match(render(r), new RegExp(`data-task-proposal-verdict="${verdict}"`));
  if (verdict === 'CONFLICT') assert.match(render(r), /模型报告证据冲突/);
}
const unknown = result('UNKNOWN'); unknown.task_review.observations = []; unknown.task_review.proposals = [];
assert.match(render(unknown), /资料不足/);
assert.match(render(unknown), /不能据此断言任务不存在/);
for (const variant of ['BLOCKED', 'rejected', 'missing']) {
  const r = result();
  if (variant === 'BLOCKED') r.status = variant;
  if (variant === 'rejected') r.reviewer.accepted = false;
  if (variant === 'missing') r.task_review = null;
  assert.doesNotMatch(render(r), /林枫|2029-06-20|source-a/);
}
const comparison = result();
comparison.task_review.steps[0] = {kind: 'compare_dates', status: 'COMPLETED', comparisons: [{current_valid: true,
  current_comparison: {old_deadline: {value: '2029-06-20'}, new_deadline: {value: '2029-06-24'},
    delta_calendar_days: 4, plan_currentness: 'NOT_ESTABLISHED'}}]};
assert.match(render(comparison), /日历日差值.*4/);
assert.match(render(comparison), /不能确认当前正式生效的计划/);
assert.match(render(comparison), /已完成现有可执行的证据读取/);
assert.doesNotMatch(render(comparison), /哪些任务关系和计划正式有效，仍需独立确认/);
comparison.task_review.steps[0].comparisons[0].current_valid = false;
assert.doesNotMatch(render(comparison), /日历日差值/);
for (const [blocker, expected] of [
  ['SOURCE_SCOPED_OPERATION_UNSUPPORTED', '不支持按指定来源范围执行此读取'],
  ['TIME_SCOPED_OPERATION_UNSUPPORTED', '不支持按指定时间范围执行此读取'],
  ['NO_EXECUTABLE_OPERATION', '尚未提供此步骤所需的读取操作'],
  ['P4_VERDICT_UNKNOWN', '现有关系读取结果仍为未知，未知不等于否定'],
]) {
  const r = result(); r.task_review.steps[0].blocking_reasons = [blocker];
  assert.ok(render(r).includes(expected), blocker);
}
for (const currentness of ['FUTURE_EFFECTIVE', 'EFFECTIVE_AT_REFERENCE']) {
  const r = result();
  r.task_review.steps = structuredClone(comparison.task_review.steps);
  const c = r.task_review.steps[0].comparisons[0]; c.current_valid = true;
  Object.assign(c.current_comparison, {plan_currentness: currentness,
    asserted_effective_at: '2029-06-10', as_of: '2029-06-05'});
  const summary = render(r).split('task-source-records')[0];
  assert.match(summary, /2029-06-10/); assert.match(summary, /2029-06-05/);
  assert.ok(summary.includes(currentness === 'FUTURE_EFFECTIVE' ? '在该参考日尚未生效' : '仅在所示核验参考日满足生效条件'));
}
const unsafe = result();
unsafe.task_review.observations[0].owner = '<img src=x onerror=alert(1)>';
unsafe.task_review.proposals[0].missing_evidence = ['<script>attack</script>'];
assert.doesNotMatch(render(unsafe), /<img|<script>/);
assert.match(render(unsafe), /&lt;img/);
console.log('PASS: bounded answer, unknown/conflict/blocked, field isolation, P4 limits, citations and escaping');
