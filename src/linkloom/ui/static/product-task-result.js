/* Presentation only: authority and current-source validation belong to the server. */
window.renderTaskAnswer = (result, {escapeHtml: esc, t, date, citation}) => {
  if (!['PARTIAL', 'UNKNOWN'].includes(result.status) || result.reviewer?.accepted !== true || !result.task_review) return '';
  const view = result.task_review;
  const prep = result.preparation || {};
  const observations = view.observations || [];
  const proposals = view.proposals || [];
  const steps = view.steps || [];
  const sources = result.evidence || [];
  const label = predicate => ({
    SAME_TASK: t('Do the records describe the same task?', '这些记录是否属于同一任务？'),
    REVISES_PLAN: t('Does one plan replace the other?', '后一计划是否替代前一计划？'),
  }[predicate] || predicate);
  const proposalState = verdict => ({
    SUPPORTED: t('AI proposal: supports the relationship', 'AI 提案：支持该关系'),
    REFUTED: t('AI proposal: refutes the relationship', 'AI 提案：否定该关系'),
    CONFLICT: t('AI proposal: conflicting evidence', 'AI 提案：证据冲突'),
    UNKNOWN: t('AI proposal: insufficient evidence', 'AI 提案：证据不足'),
  }[verdict] || t('AI proposal: insufficient evidence', 'AI 提案：证据不足'));
  const stepState = status => ({
    SUPPORTED: t('Existing relationship evidence supports this step', '既有关系证据支持此步骤'),
    COMPLETED: t('Read completed', '读取已完成'),
    REFUTED: t('Counter-evidence found', '存在否定证据'),
    CONFLICT: t('Conflicting evidence; no unique conclusion', '证据冲突，无法给出唯一结论'),
    UNKNOWN: t('Still unknown', '仍然未知'),
    BLOCKED: t('Cannot continue yet', '暂时无法继续'),
  }[status] || t('Still unknown', '仍然未知'));
  const reason = step => {
    const blockers = step.blocking_reasons || [];
    if (blockers.includes('SOURCE_SCOPED_OPERATION_UNSUPPORTED')) return t('This source scope cannot be checked here. Try a narrower question.', '暂时无法核验此来源范围，请缩小问题范围。');
    if (blockers.includes('TIME_SCOPED_OPERATION_UNSUPPORTED')) return t('This time range has not been checked; time-scoped reading is unavailable.', '尚未核验该时段，当前不支持按时间范围读取。');
    if (blockers.includes('NO_EXECUTABLE_OPERATION')) return t('This step requires an unavailable reading capability.', '当前缺少此步骤所需的读取能力。');
    if (blockers.includes('P4_VERDICT_UNKNOWN')) return t('The relationship still needs evidence.', '任务关系待补充证据。');
    if (step.status === 'CONFLICT') return t('The evidence conflicts. Check the original records before choosing a conclusion.', '现有证据相互冲突，需要先核对原文。');
    if (step.status === 'REFUTED') return t('Counter-evidence refutes the proposed relationship.', '反对证据否定了所提关系。');
    if ((step.blocking_reasons || []).some(r => r.startsWith('DEPENDENCY_'))) return t('Confirm the prerequisite relationship to continue.', '需先确认前置关系，才能继续。');
    return t('Current, independently verifiable relationship evidence is missing.', '缺少当前有效、可独立核验的关系证据。');
  };
  const open = steps.filter(s => !['SUPPORTED', 'COMPLETED'].includes(s.status));
  const fieldLabel = field => ({owner: t('Owner', '负责人'), deadline: t('Deadline', '截止日期'),
    project: t('Project', '项目'), vendor: t('Vendor', '供应商'), description: t('Work', '任务内容')}[field] || field);
  const field = value => esc(value || t('Not established', '尚未确定'));
  const nextAction = action => ({
    PROVIDE_TASK_SOURCE: t('Provide the missing meeting or original task assignment.', '补充缺失的会议或任务分配原文。'),
    IDENTITY_AND_REVISION_REQUIRED: t('Date comparison first needs verified task identity and plan revision.', '比较日期前，需要先核验任务身份和计划修订。'),
    REVIEW_ORIGINALS: action.predicate === 'REVISES_PLAN'
      ? t('Check a source explicitly stating that one plan replaces the other.', '核对明确说明后一计划替代前一计划的来源。')
      : t('Check the referenced original messages and obtain independently verifiable relationship evidence.', '核对所引原始消息，补充可独立核验的关系证据。'),
  }[action.kind] || t('Check the original records for the unresolved question.', '围绕待确认问题核对原始记录。'));
  const quotes = items => (items || []).map(item => `<blockquote class="quote">${esc(item.quote || item.span?.quote || '')}</blockquote>`).join('') || `<p>${t('None provided', '未提供')}</p>`;
  const comparisons = steps.filter(s => s.kind === 'compare_dates' && s.status === 'COMPLETED')
    .flatMap(s => (s.comparisons || []).filter(c => c.current_valid === true && c.current_comparison));
  const currentness = comparison => {
    if (comparison.plan_currentness === 'FUTURE_EFFECTIVE') return `${t('The declared effective date is', '原文声明生效日期为')} ${date(comparison.asserted_effective_at)}; ${t('reference date', '核验参考日')} ${date(comparison.as_of)}. ${t('Not yet effective at that reference date; the current formal plan is unconfirmed.', '在该参考日尚未生效；当前正式计划仍未确认。')}`;
    if (comparison.plan_currentness === 'EFFECTIVE_AT_REFERENCE') return `${t('Effective-time conditions hold only at the shown reference date', '仅在所示核验参考日满足生效条件')}: ${date(comparison.as_of)}; ${t('declared effective date', '原文声明生效日期')} ${date(comparison.asserted_effective_at)}. ${t('Current formal plan: unconfirmed.', '当前正式计划：待确认。')}`;
    return t('Date comparison available · currently effective plan unconfirmed.', '日期可比较 · 当前生效计划待确认。');
  };

  return `<section class="panel ask-answer ask-answer--notice task-answer">
    <h2 class="ask-answer-headline">${observations.length ? t(`Found ${observations.length} task record${observations.length === 1 ? '' : 's'}`, `找到 ${observations.length} 条任务记录`) : t('More source material is needed', '需要补充来源资料')}</h2>
    <p class="ask-answer-status">${result.status === 'UNKNOWN' ? t('Insufficient information', '资料不足') : t('Partial results', '部分结果')} · ${t('Task relationships and effective plan: unconfirmed', '任务关系与生效计划：待确认')}</p>
    ${['FAKE', 'REPLAY'].includes(prep.execution_kind) ? `<p class="execution-mode">${t('Offline demo', '离线演示')}</p>` : ''}
    ${proposals.length ? `<div class="task-model-summary"><strong>${t('AI proposals · awaiting confirmation', 'AI 提案 · 待确认')}</strong><ul>${proposals.slice(0, 3).map(p => `<li>${esc(label(p.predicate))} ${esc((!p.evidence_status || p.evidence_status === 'EVIDENCE_VALID') ? proposalState(p.proposed_verdict) : t('Evidence check failed', '证据校验失败'))}</li>`).join('')}</ul></div>` : ''}
    ${comparisons.map(c => `<p>${t('Source plan boundaries', '来源计划日期边界')}: ${esc(c.current_comparison.old_deadline?.value)} → ${esc(c.current_comparison.new_deadline?.value)} · ${t('Calendar-day difference', '日历日差值')}: ${esc(c.current_comparison.delta_calendar_days)}</p><p class="hint">${currentness(c.current_comparison)}</p>`).join('')}
    <section class="task-source-records" aria-label="${t('Source records', '来源记录')}"><h3>${t('Source records', '来源记录')}</h3>
      <p class="hint">${t('Each card shows one source message. AI field interpretations are pending review.', '每张卡片对应一条来源消息，AI 字段解读待复核。')}</p>
      <div class="task-record-grid">${observations.map(o => {
        const source = sources.find(e => e.evidence_ref === o.provenance?.evidence_ref);
        return `<article class="ask-evidence" data-task-observation="${esc(o.observation_id)}"><h4>${esc(source?.source_name || t('Source record', '来源记录'))}</h4>
          ${o.event_time ? `<p class="metadata">${t('Recorded at', '记录时间')} ${date(o.event_time)}</p>` : ''}
          <blockquote class="quote task-record-quote">${esc((o.provenance?.quote || '').slice(0, 160))}${(o.provenance?.quote || '').length > 160 ? '…' : ''}</blockquote>
          <p><strong>${field(o.description)}</strong></p><dl class="task-fields">
            ${['owner', 'deadline', 'project', 'vendor'].map(f => `<div><dt>${esc(fieldLabel(f))}</dt><dd>${field(o[f])}</dd></div>`).join('')}</dl>
          ${o.deadline_text ? `<p class="hint">${t('Date wording in source:', '原文日期表述：')} ${esc(o.deadline_text)}${o.deadline_role !== 'TASK_DEADLINE' ? ` · ${t('The date meaning is not established', '日期含义尚未确定')}` : ''}</p>` : ''}

          ${source?.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${esc(source.source_id)}">${t('View source', '查看来源')}</button>` : ''}
          <details class="ask-source-details"><summary>${t('Field evidence and original message', '字段证据与原始消息')}</summary>
            <p class="metadata">${t('Source version', '来源版本')}: <code>${esc(source?.source_version || o.provenance?.artifact_version_id)}</code> · ${t('Lines', '行号')} ${esc(source?.line_start)}–${esc(source?.line_end)}</p>
            <blockquote class="quote">${esc(o.provenance?.quote)}</blockquote>
            ${(o.field_evidence || []).map(f => `<p><strong>${esc(fieldLabel(f.field))}</strong>: ${esc(f.quote)} <small>(${esc(f.char_start)}–${esc(f.char_end)})</small></p>`).join('')}
            <p>${t('Deadline role', '日期角色')}: ${esc(o.deadline_role || 'UNKNOWN')} · ${t('Precision', '精度')}: ${esc(o.deadline_precision || 'UNKNOWN')}</p>
            ${(o.unknown_fields || []).length ? `<p>${t('Unknown fields', '未知字段')}: ${esc(o.unknown_fields.map(fieldLabel).join(' · '))}</p>` : ''}
            ${(o.validation_issues || []).length ? `<p>${t('Validation issues', '校验问题')}: ${esc(o.validation_issues.join(' · '))}</p>` : ''}
            ${o.field_bindings ? `<p class="hint">${t('Model field roles with checked quotes; semantic confirmation is pending.', '以下字段角色为模型解释，原文已核验，语义仍待复核。')}</p><pre>${esc(JSON.stringify(o.field_bindings, null, 2))}</pre>` : ''}</details></article>`;
      }).join('')}</div></section>
    <div class="ask-unknown"><h3>${t('Needs confirmation', '待确认事项')}</h3>
      ${open.slice(0, 3).map(s => `<p><strong>${esc(s.question || label(s.predicate))}</strong> ${esc(reason(s))}</p>`).join('')}
      ${[...new Set(proposals.flatMap(p => p.missing_evidence || []))].slice(0, 3).map(g => `<p>${t('AI-reported gap:', 'AI 提出的缺口：')} ${esc(g)}</p>`).join('')}
      ${!observations.length ? `<p>${t('No verifiable task record returned. Add the meeting notes or original assignment to continue.', '本次未检索到可核验的任务记录，请补充会议记录或任务分配原文。')}</p>` : ''}
      ${!open.length && observations.length ? `<p>${t('Confirm the task relationships and effective plan against the original records.', '请根据原始记录确认任务关系与生效计划。')}</p>` : ''}
      ${(prep.gaps || []).includes('DISCOVERY_COVERAGE_INCOMPLETE') ? `<p>${t('Some sources have not been fully checked.', '部分来源尚未完整检查。')}</p>` : ''}
      ${(open.length > 3 || new Set(proposals.flatMap(p => p.missing_evidence || [])).size > 3) ? `<p>${t('See investigation details for all checks and gaps.', '全部核验项与缺口见调查详情。')}</p>` : ''}
    </div>
    ${(view.next_actions || []).length ? `<section class="task-next-actions"><h3>${t('Next steps', '下一步')}</h3><ul>${[...new Set(view.next_actions.map(nextAction))].map(a => `<li>${esc(a)}</li>`).join('')}</ul></section>` : ''}
    <details class="task-investigation-details ask-technical"><summary>${t('Relationship proposals and investigation details', '展开关系提案与调查详情')} (${proposals.length} / ${steps.length})</summary>
      <h3>${t('AI relationship proposals', 'AI 关系提案')}</h3>
      ${proposals.map(p => `<article class="ask-evidence"><h4>${esc(label(p.predicate))}</h4>
        <p data-task-proposal-verdict="${esc(p.proposed_verdict || 'UNKNOWN')}">${esc(proposalState(p.proposed_verdict))}</p>
        <p class="hint">${p.evidence_status === 'EVIDENCE_VALID' ? t('Citations checked · interpretation pending review.', '引用已核验 · 判断待复核。') : t('Evidence check failed. Review the citations and coverage.', '证据校验失败，请核对引用与覆盖范围。')}</p>
        ${p.evidence_status === 'EVIDENCE_VALID' ? [p.subject, p.object].map(ref => `<p>${esc(observations.find(o => o.observation_id === ref?.observation_id)?.raw_text || t('See the referenced source', '请查看对应来源'))}</p>`).join('') : ''}
        <details><summary>${t('Supporting evidence', '支持证据')}</summary>${quotes(p.supporting_evidence)}</details>
        <details><summary>${t('Counter-evidence or conflicts', '反对证据或冲突')}</summary>${quotes(p.counter_evidence)}</details>
        <p>${t('Model-reported evidence gaps', '模型报告的证据缺口')}: ${esc((p.missing_evidence || []).join(' · ') || t('None reported', '未报告'))}</p>
        <p class="metadata">${esc(p.proposed_verdict)} · ${esc(p.evidence_status)} · ${esc((p.reason_codes || []).join(' · '))}</p></article>`).join('') || `<p>${t('No model proposal returned.', '本次未返回模型提案。')}</p>`}
      <h3>${t('Relationship checks', '关系核验')}</h3>
      ${steps.map(s => `<article class="ask-evidence"><h4>${esc(s.question || label(s.predicate))}</h4><p>${esc(stepState(s.status))}</p>${!['SUPPORTED', 'COMPLETED'].includes(s.status) ? `<p>${esc(reason(s))}</p>` : ''}</article>`).join('')}

      <p>${t('Task relationship approval is unavailable in this installation.', '当前版本暂不支持任务关系审批。')}</p>

      <details><summary>${t('All original citations', '全部原始引用')}</summary>${sources.map(citation).join('')}</details>
      <details><summary>${t('Investigation trace and review', '调查轨迹与审查结果')}</summary><pre>${esc(JSON.stringify({steps, relations: view.relations, tools: view.executed_tools, gaps: prep.gaps, next_actions: view.next_actions, reviewer: result.reviewer}, null, 2))}</pre></details>
    </details></section>`;
};
