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
    SUPPORTED: t('The model leans toward support; unconfirmed', '模型倾向支持，尚未确认'),
    REFUTED: t('The model leans against it; unconfirmed', '模型倾向否定，尚未确认'),
    CONFLICT: t('The model reports conflicting evidence', '模型报告证据冲突'),
    UNKNOWN: t('The model cannot determine this', '模型无法判断'),
  }[verdict] || t('The model cannot determine this', '模型无法判断'));
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
    if (blockers.includes('SOURCE_SCOPED_OPERATION_UNSUPPORTED')) return t('Reading within the specified source scope is not supported. This does not mean the evidence is absent.', '当前不支持按指定来源范围执行此读取，不代表证据不存在。');
    if (blockers.includes('TIME_SCOPED_OPERATION_UNSUPPORTED')) return t('Reading within the specified time scope is not supported. The requested period was not evaluated.', '当前不支持按指定时间范围执行此读取，尚未评估该时段。');
    if (blockers.includes('NO_EXECUTABLE_OPERATION')) return t('This installation does not provide the read operation needed for this step.', '当前系统尚未提供此步骤所需的读取操作。');
    if (blockers.includes('P4_VERDICT_UNKNOWN')) return t('The existing relationship read is still unknown. Unknown does not mean refuted.', '现有关系读取结果仍为未知，未知不等于否定。');
    if (step.status === 'CONFLICT') return t('The evidence conflicts. Check the original records before choosing a conclusion.', '现有证据相互冲突，需要先核对原文。');
    if (step.status === 'REFUTED') return t('Counter-evidence was returned; this step does not support the proposed relationship.', '本步骤返回否定证据，不能据此支持所提关系。');
    if ((step.blocking_reasons || []).some(r => r.startsWith('DEPENDENCY_'))) return t('A prerequisite relationship is unconfirmed, so this step could not continue.', '前置关系尚未确认，因此本步骤无法继续。');
    return t('No current, independently verifiable evidence satisfies this step. Model support cannot replace that evidence.', '尚缺当前有效、可独立核验的证据；模型支持不能替代这一前提。');
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
    if (comparison.plan_currentness === 'EFFECTIVE_AT_REFERENCE') return `${t('Effective-time conditions hold only at the shown reference date', '仅在所示核验参考日满足生效条件')}: ${date(comparison.as_of)}; ${t('declared effective date', '原文声明生效日期')} ${date(comparison.asserted_effective_at)}. ${t('This does not confirm the current formal plan.', '这不代表当前正式计划已确认。')}`;
    return t('The dates can be compared, but the currently effective formal plan cannot be confirmed.', '可比较日期边界，但不能确认当前正式生效的计划。');
  };

  return `<section class="panel ask-answer ask-answer--notice task-answer">
    <p class="ask-answer-status">${result.status === 'UNKNOWN' ? t('Insufficient information', '资料不足') : t('Partial investigation · confirmation pending', '部分调查结果 · 待确认')}</p>
    <h2 class="ask-answer-headline">${observations.length ? t('Source records found; the formal task state remains unconfirmed', '已找到来源记录，任务的正式状态仍待确认') : t('The available materials are insufficient for a clear conclusion', '现有资料不足，暂时无法给出明确结论')}</h2>
    <p class="ask-answer-sentence">${t('Records are kept separate. Source statements and model interpretations do not establish the current owner or effective plan.', '各条记录分别保留。原文陈述与模型解读，尚不代表当前负责人或生效计划。')}</p>
    <p><strong>${t('Formal business status:', '正式业务状态：')}</strong> ${t('Unconfirmed. This investigation does not grant approval.', '尚未确认。本次调查不授予业务批准。')}</p>
    ${proposals.length ? `<div class="task-model-summary"><strong>${t('Model judgments · confirmation pending', '模型判断 · 待确认')}</strong><ul>${proposals.slice(0, 3).map(p => `<li>${esc(label(p.predicate))} ${esc(proposalState(p.proposed_verdict))}</li>`).join('')}</ul><p class="hint">${t('Checked citations do not verify the interpretation. Full proposals and evidence are expandable below.', '引用核验不等于判断成立。完整提案与证据可在下方展开。')}</p></div>` : ''}
    <div class="ask-unknown"><h3>${t('Open questions', '待确认事项')}</h3>
      ${open.slice(0, 3).map(s => `<p><strong>${esc(s.question || label(s.predicate))}</strong> ${esc(reason(s))}</p>`).join('') || `<p>${steps.length ? t('The available executable evidence reads are complete. This does not confirm the current formal business state.', '已完成现有可执行的证据读取，仍不能据此确认当前正式业务状态。') : t('The investigation did not establish a current formal task state.', '本次调查未能确认任务当前的正式状态。')}</p>`}
      ${proposals.flatMap(p => p.missing_evidence || []).slice(0, 3).map(g => `<p>${t('Model-reported gap:', '模型报告的缺口：')} ${esc(g)}</p>`).join('')}
      ${!observations.length ? `<p>${t('No verifiable task candidate was returned. This does not establish that the task does not exist.', '本次没有返回可核验的任务候选，不能据此断言任务不存在。')}</p>` : ''}
      ${(prep.gaps || []).some(g => g === 'DISCOVERY_COVERAGE_INCOMPLETE') ? `<p>${t('Some source material was not fully examined in this investigation.', '部分来源尚未在本次调查中完整检查。')}</p>` : ''}
    </div>
    ${comparisons.map(c => `<p>${t('Source plan boundaries', '来源计划日期边界')}: ${esc(c.current_comparison.old_deadline?.value)} → ${esc(c.current_comparison.new_deadline?.value)} · ${t('Calendar-day difference', '日历日差值')}: ${esc(c.current_comparison.delta_calendar_days)}</p><p class="hint">${currentness(c.current_comparison)}</p>`).join('')}
    <section class="task-source-records" aria-label="${t('Source records', '来源记录')}"><h3>${t('What the sources say', '原文记录了什么')}</h3>
      <p class="hint">${t('Fields are shown per message. Field interpretations still need review; nothing is inherited from another record.', '字段按消息分别展示，字段解释仍待复核，不从其他记录继承信息。')}</p>
      <div class="task-record-grid">${observations.map(o => {
        const source = sources.find(e => e.evidence_ref === o.provenance?.evidence_ref);
        return `<article class="ask-evidence" data-task-observation="${esc(o.observation_id)}"><h4>${esc(source?.source_name || t('Source record', '来源记录'))}</h4>
          ${o.event_time ? `<p class="metadata">${t('Recorded at', '记录时间')} ${date(o.event_time)}</p>` : ''}
          <p class="hint">${t('Source excerpt', '原文片段')}</p><blockquote class="quote task-record-quote">${esc((o.provenance?.quote || '').slice(0, 160))}${(o.provenance?.quote || '').length > 160 ? '…' : ''}</blockquote>
          <p><strong>${field(o.description)}</strong></p><dl class="task-fields">
            ${['owner', 'deadline', 'project', 'vendor'].map(f => `<div><dt>${esc(fieldLabel(f))}</dt><dd>${field(o[f])}</dd></div>`).join('')}</dl>
          ${o.deadline_text ? `<p class="hint">${t('Date wording in source:', '原文日期表述：')} ${esc(o.deadline_text)}${o.deadline_role !== 'TASK_DEADLINE' ? ` · ${t('The date meaning is not established', '日期含义尚未确定')}` : ''}</p>` : ''}
          ${(o.unknown_fields || []).length ? `<p class="hint">${t('Not established:', '尚未确定：')} ${esc(o.unknown_fields.map(fieldLabel).join(' · '))}</p>` : ''}
          ${source?.source_id ? `<button class="button quiet" data-action="inspect-source" data-id="${esc(source.source_id)}">${t('View source', '查看来源')}</button>` : ''}
          <details class="ask-source-details"><summary>${t('Field evidence and original message', '字段证据与原始消息')}</summary>
            <p class="metadata">${t('Source version', '来源版本')}: <code>${esc(source?.source_version || o.provenance?.artifact_version_id)}</code> · ${t('Lines', '行号')} ${esc(source?.line_start)}–${esc(source?.line_end)}</p>
            <blockquote class="quote">${esc(o.provenance?.quote)}</blockquote>
            ${(o.field_evidence || []).map(f => `<p><strong>${esc(fieldLabel(f.field))}</strong>: ${esc(f.quote)} <small>(${esc(f.char_start)}–${esc(f.char_end)})</small></p>`).join('')}
            <p>${t('Deadline role', '日期角色')}: ${esc(o.deadline_role || 'UNKNOWN')} · ${t('Precision', '精度')}: ${esc(o.deadline_precision || 'UNKNOWN')}</p>
            <p>${t('Unknown fields', '未知字段')}: ${esc((o.unknown_fields || []).join(' · '))}</p>
            <p>${t('Validation issues', '校验问题')}: ${esc((o.validation_issues || []).join(' · '))}</p>
            ${o.field_bindings ? `<p class="hint">${t('Model field roles with checked quotes; semantic confirmation is pending.', '以下字段角色为模型解释，原文已核验，语义仍待复核。')}</p><pre>${esc(JSON.stringify(o.field_bindings, null, 2))}</pre>` : ''}</details></article>`;
      }).join('')}</div></section>
    <details class="task-investigation-details ask-technical"><summary>${t('Relationship proposals and investigation details', '展开关系提案与调查详情')} (${proposals.length} / ${steps.length})</summary>
      <h3>${t('Model proposals · not formal relationships', '模型提案 · 尚非正式关系')}</h3>
      ${proposals.map(p => `<article class="ask-evidence"><h4>${esc(label(p.predicate))}</h4>
        <p data-task-proposal-verdict="${esc(p.proposed_verdict || 'UNKNOWN')}">${esc(proposalState(p.proposed_verdict))}</p>
        <p class="hint">${p.evidence_status === 'EVIDENCE_VALID' ? t('Citations checked; interpretation and business approval remain unconfirmed.', '引用已核验；判断和业务批准仍未确认。') : t('Citations or coverage failed validation; no trusted relationship is established.', '引用或覆盖未通过校验，不能建立可信关系。')}</p>
        ${p.evidence_status === 'EVIDENCE_VALID' ? [p.subject, p.object].map(ref => `<p>${esc(observations.find(o => o.observation_id === ref?.observation_id)?.raw_text || t('See the referenced source', '请查看对应来源'))}</p>`).join('') : ''}
        <details><summary>${t('Supporting evidence', '支持证据')}</summary>${quotes(p.supporting_evidence)}</details>
        <details><summary>${t('Counter-evidence or conflicts', '反对证据或冲突')}</summary>${quotes(p.counter_evidence)}</details>
        <p>${t('Model-reported evidence gaps', '模型报告的证据缺口')}: ${esc((p.missing_evidence || []).join(' · ') || t('No additional gap reported; this does not imply confirmation.', '未报告额外缺口，仍不表示已确认。'))}</p>
        <p class="metadata">${esc(p.proposed_verdict)} · ${esc(p.evidence_status)} · ${esc((p.reason_codes || []).join(' · '))}</p></article>`).join('') || `<p>${t('No model proposal returned.', '本次未返回模型提案。')}</p>`}
      <h3>${t('Existing relationship reads and remaining steps', '既有关系读取与剩余步骤')}</h3>
      ${steps.map(s => `<article class="ask-evidence"><h4>${esc(s.question || label(s.predicate))}</h4><p>${esc(stepState(s.status))}</p>${!['SUPPORTED', 'COMPLETED'].includes(s.status) ? `<p>${esc(reason(s))}</p>` : ''}</article>`).join('')}
      ${(view.next_actions || []).map(a => `<p>${esc(nextAction(a))}</p>`).join('')}
      <p>${t('This installation has no authenticated task-relationship approval interface. Viewing evidence does not approve a relationship.', '当前系统尚无具备身份与权限校验的任务关系批准接口。查看证据不会批准关系。')}</p>
      ${['FAKE', 'REPLAY'].includes(prep.execution_kind) ? `<p class="hint">${t('Offline scripted or recorded adapters; real-model understanding was not evaluated.', '本次为离线脚本或回放，未评估真实模型理解能力。')}</p>` : ''}
      <details><summary>${t('All original citations', '全部原始引用')}</summary>${sources.map(citation).join('')}</details>
      <details><summary>${t('Investigation trace and review', '调查轨迹与审查结果')}</summary><pre>${esc(JSON.stringify({steps, relations: view.relations, tools: view.executed_tools, gaps: prep.gaps, next_actions: view.next_actions, reviewer: result.reviewer}, null, 2))}</pre></details>
    </details></section>`;
};
