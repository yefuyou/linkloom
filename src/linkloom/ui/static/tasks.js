/* Independent formal-task page. Authority remains in the existing task services. */
(function (global) {
  'use strict';
  const FIELD_NAMES = {title: '任务描述', owner: '负责人', deadline: '截止时间'};
  const STATUS_NAMES = {open: '未开始', in_progress: '进行中', blocked: '受阻', done: '已完成', cancelled: '已取消'};
  const REVIEW_NAMES = {approved: '已确认', needs_review: '待复核', invalidated: '已失效', rejected: '已拒绝'};
  const EVENT_NAMES = {created: '建立任务', owner_changed: '负责人变更', deadline_changed: '截止时间变更', status_changed: '状态变更', title_changed: '任务描述变更'};
  const STORAGE_KEY = 'linkloom.tasks.reviewer';
  const esc = (value) => String(value == null ? '' : value).replace(/[&<>"']/g, (char) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
  const text = (value, empty = '未指定') => value == null || value === '' ? empty : String(value);
  function parsedTime(value) {
    const raw = String(value || '').trim();
    const parts = /^(\d{4})-(\d{2})-(\d{2})(?:$|T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$)/.exec(raw);
    if (!parts) return null;
    const year = Number(parts[1]), month = Number(parts[2]), day = Number(parts[3]);
    const calendar = new Date(0); calendar.setUTCFullYear(year, month - 1, day); calendar.setUTCHours(0, 0, 0, 0);
    if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) return null;
    const dateOnly = raw.length === 10;
    const date = dateOnly ? new Date(year, month - 1, day) : new Date(raw);
    return Number.isNaN(date.getTime()) ? null : {date, dateOnly};
  }
  function formattedTime(value, includeTime) {
    if (!value) return '未提供';
    const parsed = parsedTime(value); if (!parsed) return String(value);
    const options = {month: 'numeric', day: 'numeric'};
    if (includeTime && !parsed.dateOnly) Object.assign(options, {hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
    const parts = new Intl.DateTimeFormat('zh-CN', options).formatToParts(parsed.date);
    const get = type => parts.find(part => part.type === type).value;
    return get('month') + '月' + get('day') + '日' + (options.hour ? ' ' + get('hour') + ':' + get('minute') : '');
  }
  const time = value => formattedTime(value, true);
  const meetingDate = value => formattedTime(value, false);
  const id = (value) => encodeURIComponent(value);
  const deadline = (value) => value && typeof value === 'object' ? text(value.deadline || value.deadline_text) : text(value);
  const fieldValue = (field, value) => field === 'deadline' ? deadline(value) : field === 'status' ? text(STATUS_NAMES[value] || value) : text(value);
  const EMPTY_PAGE = () => ({items: [], page: {limit: 20, offset: 0, next_offset: null, has_more: false}});
  function patchFor(candidate, draft) {
    const patch = {};
    if (draft.title.trim() !== (candidate.title || '')) patch.title = draft.title.trim();
    if ((draft.owner.trim() || null) !== (candidate.owner || null)) patch.owner = draft.owner.trim() || null;
    const date = draft.deadline.trim() || null, raw = draft.deadline_text.trim() || null;
    if (date !== (candidate.deadline || null) || raw !== (candidate.deadline_text || null)) { patch.deadline = date; patch.deadline_text = raw; }
    return patch;
  }
  function defaultReason(action, fields = []) {
    const names = fields.filter((field) => field !== 'deadline_text').map((field) => FIELD_NAMES[field] || field).join('、');
    if (action === 'create') return names ? '人工修正：' + names : '按原文确认，未修改';
    if (action === 'merge') return '合并入已有任务：' + (names || '不应用字段，仅关联原文');
    return '';
  }
  function errorText(error) {
    const code = error.code || 'NETWORK_ERROR';
    const messages = {
      INVALID_REQUEST: '请检查填写内容，必填项不能留空。', INVALID_JSON: '提交格式不正确，请刷新页面后重试。',
      UNSUPPORTED_MEDIA_TYPE: '提交格式不受支持，请刷新页面后重试。', RESOURCE_NOT_FOUND: '这条记录已不可用，或不在当前工作区。',
      WORKSPACE_NOT_FOUND: '工作区已不可用，请重新选择。', CANDIDATE_CHANGED: '候选内容已经变化，请重新读取原文并审核。',
      REVIEW_CHANGED: '审核记录已经变化，请刷新后重新预览。', PREVIEW_STALE: '预览已过期，任务或来源已变化，请重新预览并确认。',
      ALREADY_REVIEWED: '这条候选已被处理，请查看最新列表。', SOURCE_INVALID: '原文校验未通过，当前不能确认这条候选。',
      REVIEW_CONFLICT: '当前记录不满足操作条件，请刷新核对；跨版本重新绑定暂不在本页提供。',
      TASK_BUSY: '本地数据库正忙，请稍后刷新再试。', TASK_API_UNAVAILABLE: '正式待办暂不可用，请使用配置了持久数据库的本地服务。',
      NETWORK_ERROR: '连接中断，操作结果尚未确认。请先刷新核对，不要重复提交。', INTERNAL_ERROR: '操作结果尚未确认，请先刷新核对。'
    };
    if (code === 'UNDO_BLOCKED') {
      const op = error.details && error.details.first_blocking_operation && error.details.first_blocking_operation.operation;
      return '无法撤销这次合并。' + (op === 'merge' ? '请先撤销后续合并，再按相反顺序操作。' : op ? '之后已有' + (EVENT_NAMES[op] || ({edit: '人工修改', rebind: '来源重新绑定', invalidate: '失效处理'}[op]) || '其他操作') + '，为保留后续变更，本页不能撤销。' : '该合并已撤销，或已不是最近一次可撤销的合并。');
    }
    return messages[code] || (error.status === 409 ? '记录已经变化，请刷新并重新预览。' : error.status === 403 || error.status === 421 ? '请从本地 LinkLoom 页面访问。' : '请求未完成，请检查连接并刷新。');
  }
  function sourcesHtml(sources) {
    return '<div class="source-list">' + (sources || []).map((source) => '<p>' + esc(source.source_name || '来源未提供') + ' · 会议时间 ' + esc(meetingDate(source.meeting_time)) + '</p>').join('') + '</div>';
  }
  function evidenceHtml(references, role) {
    if (!references || !references.length) return '<p class="muted">无原文引用' + (role === 'manual' ? '，此项由人工操作。' : '。') + '</p>';
    return references.map((reference) => {
      const position = [];
      if (reference.message_id != null) position.push('发言 ' + reference.message_id);
      if (reference.line_start != null) position.push('行 ' + reference.line_start + (reference.line_end != null && reference.line_end !== reference.line_start ? '–' + reference.line_end : ''));
      if (reference.char_start != null) position.push('字符 ' + reference.char_start + (reference.char_end != null ? '–' + reference.char_end : ''));
      return '<div class="quote"><p>' + esc(reference.source_name || '来源未提供') + ' · 会议时间 ' + esc(meetingDate(reference.meeting_time)) + '</p><blockquote>' + esc(reference.quote) + '</blockquote>' + (role === 'context' ? '<p>背景原文：字段值由人工确定，此引用不作为该值的原文依据。</p>' : '') + '<details><summary>校验信息</summary><div>' + esc(position.join(' · ') || '位置未提供') + '</div><div>来源 ID：' + esc(reference.source_id || '未提供') + '</div><div>来源版本：' + esc(reference.source_version_id || '未提供') + '</div><div>引用哈希：' + esc(reference.quote_sha256 || '未提供') + '</div>' + (reference.source_sha256 ? '<div>来源哈希：' + esc(reference.source_sha256) + '</div>' : '') + '</details></div>';
    }).join('');
  }
  function lowHtml(candidate) { return candidate.confidence === 'LOW' ? '<span class="badge low">低置信度</span><p class="muted">' + esc(candidate.confidence_reason || '请核对原文') + '</p>' : ''; }
  function comparable(field, value) {
    return field === 'deadline' ? JSON.stringify([value && value.deadline || null, value && value.deadline_text || null]) : JSON.stringify(value == null ? null : value);
  }
  function diffValue(field, value) {
    return field === 'deadline' && value && value.deadline && value.deadline_text ? value.deadline + '（' + value.deadline_text + '）' : fieldValue(field, value);
  }
  function statusHtml(task, review) { return '<span class="badge' + (review === 'needs_review' ? ' warn' : '') + '">' + esc(STATUS_NAMES[task.status] || task.status) + (review === 'needs_review' ? ' · 待复核' : '') + '</span>'; }
  function originHtml(origin) { return '<span class="badge ' + (origin && origin.origin === 'human' ? 'human' : '') + '">' + (!origin ? '来源待确认' : origin.origin === 'human' ? '人工修改' : '原文证据') + '</span>'; }
  function diffHtml(current, proposed, fields, heading = '合并后') {
    const changed = ['title', 'owner', 'deadline'].filter(field => fields.includes(field) && comparable(field, current[field]) !== comparable(field, proposed[field]));
    if (!changed.length) return '<p class="muted">没有字段变化' + (heading === '合并后' ? '，仅关联原文。' : '；恢复来源绑定。') + '</p>';
    return '<table class="diff"><thead><tr><th>字段</th><th>当前值</th><th>' + heading + '</th></tr></thead><tbody>' + changed.map(field => '<tr><th>' + FIELD_NAMES[field] + '</th><td>' + esc(diffValue(field, current[field])) + '</td><td class="applied">' + esc(diffValue(field, proposed[field])) + '</td></tr>').join('') + '</tbody></table>';
  }
  function taskFields(task) { return {title: task.title, owner: task.owner, deadline: {deadline: task.deadline, deadline_text: task.deadline_text}}; }
  function createApp(environment = {}) {
    const doc = environment.document || global.document, fetcher = environment.fetch || global.fetch.bind(global), askName = environment.prompt || global.prompt.bind(global);
    const loc = environment.location || global.location || {search: ''}, hist = environment.history || global.history;
    const root = doc.getElementById('tasks-app'), workspacePicker = doc.getElementById('workspace'), identityButton = doc.getElementById('identity'), notice = doc.getElementById('notice');
    let storage, storageAvailable = true;
    try { storage = Object.prototype.hasOwnProperty.call(environment, 'storage') ? environment.storage : global.localStorage; if (!storage) storageAvailable = false; } catch (_error) { storageAvailable = false; }
    const state = {workspace: null, workspaces: [], epoch: 0, busy: false, loading: false, view: 'candidates', candidateState: 'pending', candidates: EMPTY_PAGE(), tasks: EMPTY_PAGE(), blockedReviews: new Set(), catalog: [], catalogRequest: 0, selection: null, previewRequest: 0, lastResult: null, events: EMPTY_PAGE(), recent: [], recentRequest: 0, candidateOffset: 0, taskOffset: 0, eventOffset: 0, targetOffset: 0, ownerMode: 'all', owner: '', status: '', detail: null, form: null, evidence: {}, identity: '', storageAvailable, receipt: null, notice: null, started: false, taskRequest: 0};
    try { if (storageAvailable) state.identity = (storage.getItem(STORAGE_KEY) || '').trim(); } catch (_error) { state.storageAvailable = false; }
    function identityLabel() {
      identityButton.textContent = state.identity ? '以 ' + state.identity + ' 身份操作 · 更改' : '设置操作身份';
      identityButton.title = state.storageAvailable ? '本地自报身份，用于操作留痕；负责人需在任务字段中填写。' : '本地存储不可用，每次写操作都会询问你的名字。';
    }
    function taskUrl(taskId, mergeId, view = state.view) {
      const query = new URLSearchParams(); if (state.workspace) query.set('workspace_id', state.workspace.workspace_id);
      if (taskId) { query.set('task', taskId); query.set('view', view); } if (mergeId) query.set('merge', mergeId);
      return '/tasks' + (query.toString() ? '?' + query.toString() : '');
    }
    function setNotice(message, kind = 'error', code = '', result = null) {
      state.notice = {message, kind, code, result};
      notice.innerHTML = '<div class="notice ' + esc(kind) + '"><span>' + esc(message) + (code ? ' <small>[' + esc(code) + ']</small>' : '') + '</span>' + (result && result.task_id ? '<a data-action="view-task" data-testid="view-task" data-task="' + esc(result.task_id) + '" data-merge="' + esc(result.merge_id || '') + '" href="' + esc(taskUrl(result.task_id, result.merge_id, 'tasks')) + '">查看任务 →</a>' : '') + '</div>';
    }
    function getIdentity(change = false) {
      if (!change && state.storageAvailable && state.identity) return state.identity;
      const name = askName('你的名字', change ? state.identity : '');
      if (name == null) return null;
      const cleaned = String(name).trim();
      if (!cleaned) { setNotice('请填写你的名字；操作尚未提交。', 'error', 'IDENTITY_REQUIRED'); return null; }
      state.identity = cleaned;
      if (state.storageAvailable) { try { storage.setItem(STORAGE_KEY, cleaned); } catch (_error) { state.storageAvailable = false; } }
      identityLabel();
      if (!state.storageAvailable) setNotice('本地存储不可用，本次使用“' + cleaned + '”；下次写操作会再次询问名字。', 'info');
      return cleaned;
    }
    async function request(path, options = {}, params = {}) {
      const query = new URLSearchParams(params); if (path.startsWith('/api/product/')) query.set('workspace_id', state.workspace.workspace_id);
      let response; try { response = await fetcher(path + (query.toString() ? '?' + query.toString() : ''), {headers: {'Content-Type': 'application/json'}, ...options}); } catch (_error) { throw {code: 'NETWORK_ERROR', status: 0}; }
      let value; try { value = await response.json(); } catch (_error) { throw {code: 'INTERNAL_ERROR', status: response.status, outcomeUnknown: options.method === 'POST'}; }
      if (!response.ok) throw {...(value.error || {}), status: response.status}; return value;
    }
    const post = (path, body) => request(path, {method: 'POST', body: JSON.stringify(body)});
    const current = (epoch) => epoch === state.epoch;
    const listParams = (offset) => ({limit: 20, offset});
    function taskParams(offset, filter = true) { const params = listParams(offset); if (filter && state.ownerMode !== 'all') params.owner = state.ownerMode === 'unassigned' ? '' : state.owner.trim(); if (filter && state.status) params.status = state.status; return params; }
    const candidateKey = item => item.candidate_id + ':' + item.candidate_fingerprint;
    async function loadCandidates() {
      const epoch = state.epoch, value = await request('/api/product/task-candidates', {}, {...listParams(state.candidateOffset), state: state.candidateState});
      if (!current(epoch)) return;
      const selected = state.selection;
      state.blockedReviews.clear(); state.candidates = value;
      state.selection = value.items.find(item => selected && item.candidate_id === selected.candidate_id && item.candidate_fingerprint === selected.candidate_fingerprint) || value.items[0] || null;
      if (state.selection) await showEvidence(value.items.indexOf(state.selection));
    }
    async function loadTasks() { const epoch = state.epoch, value = await request('/api/product/tasks', {}, taskParams(state.taskOffset)); if (current(epoch)) state.tasks = value; }
    async function allPages(path, valid) {
      const items = []; let offset = 0;
      while (valid()) {
        const value = await request(path, {}, {limit: 100, offset}); if (!valid()) return null;
        items.push(...value.items); if (!value.page.has_more) return items;
        const next = value.page.next_offset; if (!Number.isInteger(next) || next <= offset) throw {code: 'INTERNAL_ERROR', status: 500}; offset = next;
      }
      return null;
    }
    async function loadCatalog() {
      const epoch = state.epoch, number = ++state.catalogRequest;
      const items = await allPages('/api/product/tasks', () => current(epoch) && number === state.catalogRequest);
      if (items) { state.catalog = items; await loadRecent(); }
    }
    async function loadRecent() {
      const epoch = state.epoch, number = ++state.recentRequest;
      const valid = () => current(epoch) && number === state.recentRequest;
      const lists = await Promise.all(state.catalog.map(async item => {
        const events = await allPages('/api/product/tasks/' + id(item.task.id) + '/events', valid);
        return (events || []).map(event => ({...event, task: item.task}));
      }));
      if (!valid()) return;
      const grouped = new Map();
      lists.flat().forEach((event, index) => {
        const key = event.task.id + ':' + (event.operation_id || event.id || index);
        if (!grouped.has(key)) grouped.set(key, []); grouped.get(key).push(event);
      });
      state.recent = Array.from(grouped.values()).sort((a, b) => new Date(b[0].created_at || 0) - new Date(a[0].created_at || 0)).slice(0, 12);
    }
    async function loadEvents(taskId, requestNumber = state.taskRequest) {
      const epoch = state.epoch;
      const items = await allPages('/api/product/tasks/' + id(taskId) + '/events', () => current(epoch) && requestNumber === state.taskRequest && state.detail && state.detail.task.id === taskId);
      if (items) state.events = {...EMPTY_PAGE(), items};
    }
    function updateUrl(taskId, mergeId) { if (hist && hist.replaceState) hist.replaceState(null, '', taskUrl(taskId, mergeId)); }
    async function openTask(taskId, options = {}) {
      const epoch = state.epoch, requestNumber = ++state.taskRequest, value = await request('/api/product/tasks/' + id(taskId)); if (!current(epoch) || requestNumber !== state.taskRequest) return;
      state.view = 'tasks'; state.detail = value; state.events = EMPTY_PAGE(); state.eventOffset = 0; if (!options.keepForm) state.form = null; if (!options.keepReceipt) state.receipt = null;
      updateUrl(taskId, options.mergeId || (state.receipt && state.receipt.merge_id)); render(); await loadEvents(taskId, requestNumber); if (current(epoch) && requestNumber === state.taskRequest) render();
    }
    function fail(error, mutation = false) {
      const code = error.code || 'NETWORK_ERROR'; if (state.form && state.form.kind === 'merge') state.form.preview = null;
      if (error.status === 409 || error.status === 404 || (mutation && (!error.status || error.status >= 500 || error.outcomeUnknown))) state.form = null;
      if (error.status === 404) { state.detail = null; state.events = EMPTY_PAGE(); state.receipt = null; updateUrl(null); }
      setNotice(errorText(error), 'error', code); render();
    }
    async function refreshCurrent() {
      if (!state.workspace || state.busy) return;
      const epoch = state.epoch; state.loading = true; state.form = null; render();
      try { await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]); if (current(epoch) && state.detail) await openTask(state.detail.task.id, {keepReceipt: true}); }
      catch (error) { if (current(epoch)) fail(error); } finally { if (current(epoch)) { state.loading = false; render(); } }
    }
    async function runRead(work) {
      if (state.busy || state.loading) return;
      const epoch = state.epoch; state.loading = true; render();
      try { await work(); } catch (error) { if (current(epoch)) fail(error); } finally { if (current(epoch)) { state.loading = false; render(); } }
    }
    async function write(work) {
      if (state.busy || state.loading) return; const actor = getIdentity(); if (!actor) return;
      const epoch = state.epoch, submittedForm = state.form; state.busy = true; state.writeCompleted = false; state.lastResult = null; render();
      try { await work(actor); }
      catch (error) { if (current(epoch)) { if (state.writeCompleted) { state.form = null; setNotice('操作已完成，但页面刷新未完成。请重新打开页面核对；不要重复提交。', 'error', error.code || 'NETWORK_ERROR', state.lastResult); render(); return; } if (submittedForm && submittedForm.item && (!error.status || error.status >= 500 || error.outcomeUnknown)) state.blockedReviews.add(candidateKey(submittedForm.item)); fail(error, true); if (error.status === 409) { try { await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]); if (state.detail) await openTask(state.detail.task.id, {keepReceipt: true}); } catch (_refreshError) { /* Preserve the mutation error. */ } } } }
      finally { if (current(epoch)) { state.busy = false; render(); } }
    }
    function pagerHtml(page, action) {
      const meta = page.page;
      return '<div class="pager"><span>' + (page.items.length ? '第 ' + (meta.offset + 1) + '–' + (meta.offset + page.items.length) + ' 条' : '暂无记录') + '</span><div><button type="button" data-action="' + action + '" data-offset="' + Math.max(0, meta.offset - meta.limit) + '"' + (meta.offset === 0 ? ' disabled' : '') + '>上一页</button><button type="button" data-action="' + action + '" data-offset="' + meta.next_offset + '"' + (!meta.has_more ? ' disabled' : '') + '>下一页</button></div></div>';
    }
    function sameTitleHtml(candidate, index) {
      return state.catalog.filter(item => item.task.title === candidate.title).map(item => '<div class="same-title-hint"><span>看起来是这条已有任务的更新：<strong>' + esc(item.task.title) + '（' + esc(text(item.task.owner)) + ' · ' + esc(deadline(item.task)) + '）</strong></span><button type="button" data-action="choose-same-task" data-index="' + index + '" data-task="' + esc(item.task.id) + '" data-testid="choose-same-task">选它</button></div>').join('');
    }
    function candidateActions(item, index) {
      if (state.blockedReviews.has(candidateKey(item)) || !item.is_current || (item.reviewed && item.review_status !== 'needs_review')) return '<p class="muted">' + (state.blockedReviews.has(candidateKey(item)) ? '已提交，重新读取列表前不会重复提交此候选。' : item.is_current ? '此候选已处理。' : '历史候选，当前不可直接审核。') + '</p>';
      return '<div class="actions processing-actions">' + [['create', '作为新任务'], ['merge', '合并更新'], ['reject', '拒绝']].filter(([kind]) => kind === 'create' || !item.reviewed).map(([kind, label]) => '<button type="button"' + (kind === 'merge' ? ' class="primary"' : kind === 'reject' ? ' class="danger"' : '') + ' data-action="candidate-' + kind + '" data-index="' + index + '" data-testid="candidate-' + kind + '">' + label + '</button>').join('') + '</div>';
    }
    function candidateHtml(item, index) {
      const candidate = item.candidate, selected = state.selection === item;
      return '<article class="card candidate' + (selected ? ' selected' : '') + '" data-testid="candidate-' + esc(item.candidate_id) + '"><div class="card-head"><h3 class="card-title">' + esc(candidate.title || '任务描述待补充') + '</h3>' + (item.review_status === 'needs_review' ? '<span class="badge warn">待复核</span>' : '') + lowHtml(candidate) + '</div><div class="facts"><span class="owner">负责人：' + esc(text(candidate.owner)) + '</span><span>截止：' + esc(deadline(candidate)) + '</span></div>' + sourcesHtml(item.source_summary) + (item.review_status === 'rejected' ? '<p class="muted">已拒绝 · 拒绝理由：' + esc(item.reason) + '</p>' : '') + '<button class="link candidate-toggle" type="button" data-action="candidate-select" data-index="' + index + '" data-testid="candidate-select" aria-expanded="' + selected + '">' + (selected ? '收起原文' : '查看原文 →') + '</button>' + (selected ? processingHtml() : candidateActions(item, index)) + '</article>';
    }
    function processingHtml() {
      const item = state.selection; if (!item) return '';
      const index = state.candidates.items.indexOf(item), cache = state.evidence[item.candidate_id + ':' + item.candidate_fingerprint];
      const form = state.form && state.form.item === item && ['create', 'merge', 'reject'].includes(state.form.kind) ? state.form : null;
      return '<div class="processing-panel" data-testid="processing-panel"><details class="evidence" open><summary>原文引用</summary>' + (cache ? (cache.evidence_basis === 'review_snapshot' ? '<p class="muted">审核时留存的历史引用；不代表当前原文已通过验证。</p>' : '') + evidenceHtml(cache.items, 'source') + pagerHtml(cache, 'evidence-page-' + index) : '<p class="muted">正在读取原文…</p>') + '</details>' + sameTitleHtml(item.candidate, index) + (form ? formHtml() : candidateActions(item, index)) + '</div>';
    }
    function candidatesHtml() {
      return '<section class="column" aria-labelledby="candidate-heading"><div class="section-head"><h2 id="candidate-heading">待确认</h2><label>查看<select data-field="candidateState" aria-label="候选处理状态"><option value="pending"' + (state.candidateState === 'pending' ? ' selected' : '') + '>待处理</option><option value="processed"' + (state.candidateState === 'processed' ? ' selected' : '') + '>已处理</option></select></label></div>' + (state.candidates.items.length ? '<div class="cards">' + state.candidates.items.map(candidateHtml).join('') + '</div>' : '<p class="empty">' + (state.candidateState === 'pending' ? '没有待确认的候选。可先到会议页导入会议记录。' : '暂无已处理候选。') + '</p>') + pagerHtml(state.candidates, 'candidate-page') + '</section>';
    }
    function boardHtml() {
      const active = state.catalog.filter(item => ['open', 'in_progress', 'blocked'].includes(item.task.status));
      const recent = state.recent.map(events => {
        const event = events[0], source = events.flatMap(value => value.after_evidence || []).find(value => value.source_name);
        const name = event.operation === 'merge' ? '合并' : event.operation === 'undo_merge' ? '撤销合并' : EVENT_NAMES[event.event_type] || '更新';
        return '<article class="card recent-card" data-testid="recent-change"><h3>' + esc(name) + ' · ' + esc(event.task.title) + '</h3><p class="muted">' + esc(time(event.created_at)) + (source ? ' · ' + esc(source.source_name) : '') + '</p><button class="link" type="button" data-action="task-detail" data-task="' + esc(event.task.id) + '">查看任务 →</button></article>';
      }).join('');
      return '<div class="kanban">' + candidatesHtml() + '<section class="column" aria-labelledby="active-heading"><h2 id="active-heading">进行中</h2><div class="cards">' + (active.length ? active.map(taskCard).join('') : '<p class="empty">暂无未完成任务。</p>') + '</div></section><section class="column" aria-labelledby="recent-heading"><h2 id="recent-heading">最近变化</h2><div class="cards">' + (recent || '<p class="empty">暂无变化。</p>') + '</div></section></div>';
    }
    function taskCard(item) {
      const task = item.task;
      return '<article class="card" data-testid="task-' + esc(task.id) + '"><div class="card-head"><h3 class="card-title">' + esc(task.title) + '</h3>' + statusHtml(task, item.review_status) + '</div><div class="facts"><span class="owner">负责人：' + esc(text(task.owner)) + '</span><span>截止：' + esc(deadline(task)) + '</span></div>' + (item.review_status === 'invalidated' ? '<p class="muted">来源已失效，保留任务与历史。</p>' : '') + sourcesHtml(item.source_summary) + '<div class="actions"><button type="button" data-action="task-detail" data-task="' + esc(task.id) + '" data-testid="task-detail">任务详情与变化历史 →</button></div></article>';
    }
    function tasksHtml() {
      const groups = new Map(); for (const item of state.tasks.items) { const owner = item.task.owner == null ? null : item.task.owner; if (!groups.has(owner)) groups.set(owner, []); groups.get(owner).push(item); }
      return '<section class="column" aria-labelledby="task-list-heading"><h2 id="task-list-heading">按负责人</h2><div class="toolbar"><label>负责人范围<select data-field="ownerMode" aria-label="负责人范围"><option value="all"' + (state.ownerMode === 'all' ? ' selected' : '') + '>全部负责人</option><option value="named"' + (state.ownerMode === 'named' ? ' selected' : '') + '>指定负责人</option><option value="unassigned"' + (state.ownerMode === 'unassigned' ? ' selected' : '') + '>未指定</option></select></label>' + (state.ownerMode === 'named' ? '<label>负责人原文<input data-field="ownerFilter" value="' + esc(state.owner) + '" aria-label="负责人原文" placeholder="完整姓名，按原文匹配"></label>' : '') + '<label>任务状态<select data-field="statusFilter" aria-label="任务状态"><option value="">全部状态</option>' + Object.entries(STATUS_NAMES).map(([value, name]) => '<option value="' + value + '"' + (state.status === value ? ' selected' : '') + '>' + name + '</option>').join('') + '</select></label><button type="button" data-action="filter-tasks">筛选</button></div><p class="muted">按负责人对当前页分组；可筛选负责人查看其任务。</p>' + (groups.size ? Array.from(groups, ([owner, items]) => '<h3 class="group-title">' + esc(owner == null ? '未指定' : owner) + '</h3><div class="cards">' + items.map(taskCard).join('') + '</div>').join('') : '<p class="empty">没有符合条件的正式任务。</p>') + pagerHtml(state.tasks, 'task-page') + '</section>';
    }
    function fieldDetails(detail) {
      return ['title', 'owner', 'deadline'].map((field) => '<div><h4>' + FIELD_NAMES[field] + ' ' + originHtml(detail.field_sources && detail.field_sources[field]) + '</h4><p class="' + (field === 'owner' ? 'owner' : '') + '">' + esc(fieldValue(field, taskFields(detail.task)[field])) + '</p>' + sourcesHtml(detail.field_source_summary && detail.field_source_summary[field] && detail.field_source_summary[field].sources) + '</div>').join('');
    }
    function verificationHtml(detail) {
      if (!detail.machine_validation && !detail.human_declarations && !detail.merge_human_declarations) return '';
      const proof = detail.merge_machine_validation;
      let machine = detail.machine_validation ? '<p>来源绑定机器核验：同源、来源版本及原文完整性等可验证部分，已留存核验记录。</p>' : '';
      if (proof && proof.evidence) machine += '<p>合并时机器核验：候选原文已校验，属于历史核验记录；“同一任务”由人判断。</p>';
      if (proof && proof.restored_review_status) machine += proof.restored_review_status === 'approved' ? '<p>撤销时的历史核验：当时恢复的来源绑定已通过核验；当前有效性以本页审核状态为准。</p>' : '<p>撤销时的历史核验：当时恢复来源未通过核验，任务被标为待复核；当前有效性以本页审核状态为准。</p>';
      function declaration(value) {
        if (!value) return '';
        if (value.withdraws_merge) return '人工撤销原合并声明。';
        if (value.task_absent) return '仍为同一场会议，且任务已不存在。';
        if (value.same_task) return '属于同一任务；不是机器验证的结论。';
        if (value.same_meeting) return '仍为同一场会议。';
        return '来源重新绑定判断由人确认。';
      }
      const latest = declaration(detail.human_declarations), merge = declaration(detail.merge_human_declarations);
      return '<details class="verification"><summary>校验信息</summary>' + machine + (latest ? '<p>人工声明：' + latest + '</p>' : '') + (merge && merge !== latest ? '<p>合并人工声明：' + merge + '</p>' : '') + '</details>';
    }
    function eventGroups() {
      const groups = new Map();
      state.events.items.forEach((event, index) => {
        const key = event.operation_id ? 'operation:' + event.operation_id : 'event:' + (event.id || index);
        if (!groups.has(key)) groups.set(key, []); groups.get(key).push(event);
      });
      return Array.from(groups.values());
    }
    function operationEvidence(events, side) {
      const seen = new Set(), references = [];
      for (const event of events) {
        const role = event[side + '_evidence_role'];
        for (const reference of event[side + '_evidence'] || []) {
          const key = JSON.stringify([role, ...['source_id', 'source_version_id', 'message_id', 'line_start', 'line_end', 'char_start', 'char_end', 'quote_sha256', 'source_sha256', 'quote'].map(field => reference[field] == null ? null : reference[field])]);
          if (!seen.has(key)) { seen.add(key); references.push(evidenceHtml([reference], role)); }
        }
      }
      return references.join('') || '<p class="muted">无原文引用，此项由人工操作。</p>';
    }
    function eventHtml(events) {
      const event = events[0], operation = event.operation;
      const meeting = events.flatMap(item => item.after_evidence || []).find(reference => reference.source_name);
      const title = operation === 'merge' ? '合并' : operation === 'undo_merge' ? '撤销合并' : event.event_type === 'created' ? '创建' : EVENT_NAMES[event.event_type] || event.event_type;
      const values = events.map(item => {
        const field = item.event_type.replace('_changed', '');
        return item.event_type === 'created' ? '<p class="change-value">' + esc(item.new_value && item.new_value.title) + '<br>负责人：' + esc(text(item.new_value && item.new_value.owner)) + ' · 截止：' + esc(deadline(item.new_value)) + '</p>' : '<p class="change-value change-pill"><strong>' + esc(FIELD_NAMES[field] || (field === 'status' ? '状态' : field)) + '</strong> ' + esc(diffValue(field, item.old_value)) + ' → ' + esc(diffValue(field, item.new_value)) + '</p>';
      }).join('');
      const truncated = events.filter(item => item.evidence_truncated);
      return '<li class="event" data-testid="task-event"><h3>' + esc(title) + (meeting ? ' · 来自 ' + esc(meeting.source_name) : ' · ' + esc(meetingDate(event.meeting_time))) + '</h3>' + values + '<p class="audit"><span>会议时间：' + esc(meetingDate(event.meeting_time)) + '</span><span>审核人：' + esc(event.actor) + ' · 操作时间：' + esc(time(event.created_at)) + '</span><span>理由：' + esc(event.reason) + '</span></p>' + (events.some(item => item.evidence_role === 'context') ? '<p class="muted">人工修改 / 人工操作 · 原文作为背景</p>' : '') + '<details class="evidence"><summary>展开变更前后的原文</summary><div class="event-evidence"><div><h4>变更前</h4>' + operationEvidence(events, 'before') + '</div><div><h4>变更后</h4>' + operationEvidence(events, 'after') + '</div></div>' + (truncated.length ? '<p class="muted">部分事件引用被截断；其中事件共有 ' + Math.max(...truncated.map(item => item.before_evidence_count + item.after_evidence_count)) + ' 段前后引用，每个事件最多展示 5 段。完整事件引用查询将在后续提供。</p>' : '') + '</details>' + (operation === 'merge' && event.operation_id ? '<div class="actions"><button type="button" data-action="undo-preview" data-merge="' + esc(event.operation_id) + '" data-testid="undo-preview">撤销这次合并…</button></div>' : '') + '</li>';
    }
    function detailHtml() {
      if (!state.detail) return ''; const detail = state.detail, task = detail.task;
      return '<aside class="detail" data-testid="task-detail-panel" aria-labelledby="detail-heading"><button class="link back" type="button" data-action="close-detail">← 返回列表</button><h2 id="detail-heading">任务详情</h2><h3 class="card-title">' + esc(task.title) + '</h3><div class="facts"><span class="owner" data-testid="task-owner">负责人：' + esc(text(task.owner)) + '</span><span data-testid="task-deadline">截止：' + esc(deadline(task)) + '</span>' + statusHtml(task, detail.review_status) + '</div>' + (detail.review_status !== 'approved' ? '<p class="muted">此任务' + esc(REVIEW_NAMES[detail.review_status] || '待复核') + '，保留当前值及全部历史。跨版本重新绑定与失效操作将在后续提供。</p>' : '') + '<details class="evidence"><summary>字段来源与会议</summary>' + fieldDetails(detail) + '</details>' + (task.source_decision_id ? '<details><summary>校验信息</summary><p>关联决定 ID：' + esc(task.source_decision_id) + '</p></details>' : '') + verificationHtml(detail) + (state.receipt && state.receipt.task_id === task.id && !state.events.items.some(event => event.operation === 'merge' && event.operation_id === state.receipt.merge_id) ? '<div class="receipt"><p>已记录本次合并，变化历史已保留。</p><button type="button" data-action="undo-preview" data-merge="' + esc(state.receipt.merge_id) + '" data-testid="receipt-undo-preview">撤销这次合并…</button></div>' : '') + '<label class="status-field">状态<select data-field="detailStatus" data-testid="new-status">' + Object.entries(STATUS_NAMES).map(([value, name]) => '<option value="' + value + '"' + ((state.form && state.form.kind === 'status' ? state.form.status : task.status) === value ? ' selected' : '') + '>' + name + '</option>').join('') + '</select></label><h2 class="history-heading">变化历史</h2><p class="muted">会议时间来自原文记录；审核人和操作时间用于留痕。撤销会追加历史，原记录保留。</p>' + (state.events.items.length ? '<ol class="timeline">' + eventGroups().map(eventHtml).join('') + '</ol>' : '<p class="empty">暂无变化记录。</p>') + (state.form && ['undo', 'status'].includes(state.form.kind) ? formHtml() : '') + '</aside>';
    }
    function createFormHtml(form) {
      const candidate = form.item.candidate;
      return '<h3>' + (form.item.task_id ? '重新确认候选' : '确认成为正式待办') + '</h3><p>核对原文，可修正下列字段。实际改动会标为“人工修改”，原始候选与证据保留。</p><div class="form-grid">' + [['title', '任务描述'], ['owner', '负责人（执行这项任务的人）'], ['deadline', '截止日期（YYYY-MM-DD，可空）'], ['deadline_text', '截止时间原文（可空）']].map(([field, label]) => '<label' + (field === 'title' ? ' class="wide"' : '') + '>' + label + '<input data-field="' + field + '" data-testid="confirm-' + field + '" value="' + esc(form[field]) + '"' + (field === 'title' ? ' required' : '') + '></label>').join('') + '</div>' + (candidate.deadline == null && candidate.deadline_text ? '<p class="muted">原文未解析出完整日期，系统不会自动推断年份；可保留原文或人工填写日期。</p>' : '');
    }
    function mergePreviewHtml(form) {
      return form.previewPending ? '<p role="status">正在更新合并预览…</p>' : form.preview ? '<div data-testid="merge-diff">' + diffHtml(form.preview.current_values, form.preview.candidate_values, form.applyFields) + '<p class="muted">同一任务：待你人工确认。原文已按服务端规则校验。</p></div>' : '<p class="muted">选择目标后自动预览差异。</p>';
    }
    function mergeFieldsHtml(form) {
      const target = state.catalog.find(item => item.task.id === form.target);
      if (!target) return '<p class="muted">先选择目标任务，再勾选要更新的内容。</p>';
      const currentValues = taskFields(target.task);
      const proposed = {title: form.item.candidate.title, owner: form.item.candidate.owner, deadline: {deadline: form.item.candidate.deadline, deadline_text: form.item.candidate.deadline_text}};
      return ['title', 'owner', 'deadline'].filter(field => comparable(field, currentValues[field]) !== comparable(field, proposed[field])).map(field => '<label class="change-pill"><input type="checkbox" data-merge-field="' + field + '" data-testid="merge-field-' + field + '"' + (form.applyFields.includes(field) ? ' checked' : '') + '><span>' + FIELD_NAMES[field] + ' ' + esc(diffValue(field, currentValues[field])) + ' → ' + esc(diffValue(field, proposed[field])) + '</span></label>').join('') || '<p class="muted">没有字段变化，可以只关联这次会议的原文。</p>';
    }
    function mergeFormHtml(form) {
      return '<label>选择合并目标<select id="merge-target" data-field="mergeTarget" data-testid="merge-target"><option value="">请选择目标任务</option>' + state.catalog.map(item => '<option value="' + esc(item.task.id) + '"' + (form.target === item.task.id ? ' selected' : '') + '>' + esc(item.task.title + ' · 负责人：' + text(item.task.owner)) + '</option>').join('') + '</select></label><div class="check-row" data-testid="merge-field-options">' + mergeFieldsHtml(form) + '</div><p class="muted">未勾选的字段保留当前值。没有勾选时，只关联这次会议的原文。</p><div data-testid="merge-preview-state" aria-live="polite">' + mergePreviewHtml(form) + '</div>';
    }
    function previewKey(form) { return JSON.stringify([form.item.candidate_id, form.item.candidate_fingerprint, form.target, form.applyFields]); }
    function validPreview(form) { return form && form.kind === 'merge' && form.preview && !form.previewPending && form.previewBinding === previewKey(form); }
    function renderMergePreview(form) {
      const panel = root.querySelector && root.querySelector('[data-testid="merge-preview-state"]');
      // Update only the preview fragment. Replacing a clicked checkbox/select while its
      // native event is still dispatching can discard focus even if focus is restored.
      if (!panel || !panel.getAttribute || state.renderedForm !== form) { render(); return; }
      panel.innerHTML = mergePreviewHtml(form);
      const confirm = root.querySelector('[data-testid="merge-confirm"]'); if (confirm) confirm.disabled = !validPreview(form);
      const reason = root.querySelector('[data-testid="review-reason"]'); if (reason && !form.reasonEdited) reason.value = form.reason;
    }
    async function previewMerge() {
      const form = state.form; if (!form || form.kind !== 'merge') return;
      const number = ++state.previewRequest, epoch = state.epoch, key = previewKey(form);
      form.preview = null; form.previewBinding = null; form.previewPending = !!form.target; renderMergePreview(form);
      if (!form.target) return;
      const valid = () => current(epoch) && number === state.previewRequest && state.form === form && key === previewKey(form);
      try {
        const preview = await post('/api/product/task-candidates/' + id(form.item.candidate_id) + '/merge-preview', {task_id: form.target, expected_candidate_fingerprint: form.item.candidate_fingerprint, apply_fields: form.applyFields.slice()});
        if (!valid()) return; form.preview = preview; form.previewBinding = key; form.previewPending = false; renderMergePreview(form);
      } catch (error) { if (valid()) { form.previewPending = false; fail(error); } }
    }
    function formHtml() {
      const form = state.form; if (!form) return '';
      let content = '';
      if (form.kind === 'create') content = createFormHtml(form);
      if (form.kind === 'reject') content = '<h3>拒绝这条候选</h3><p>只记录拒绝理由，候选和原文保留，不建立正式任务。</p>';
      if (form.kind === 'merge') content = mergeFormHtml(form);
      if (form.kind === 'undo') content = '<h3>撤销合并预览</h3><p>本次撤销将恢复以下字段及其来源。仍为当前有效版本的候选会返回待确认；来源变化时保留历史并待复核。</p>' + diffHtml(taskFields(state.detail.task), form.preview.restored_values, ['title', 'owner', 'deadline'], '撤销后') + '<div class="row">' + ['title', 'owner', 'deadline'].map((field) => '<span>' + FIELD_NAMES[field] + ' ' + originHtml(form.preview.restored_field_sources && form.preview.restored_field_sources[field]) + '</span>').join('') + '</div><p class="muted">恢复后的审核状态：' + esc(REVIEW_NAMES[form.preview.restored_review_status]) + (form.preview.restored_review_status === 'needs_review' ? '。原来源已变化，恢复数值后仍需人工复核。' : '') + '</p>';
      if (form.kind === 'status') content = '<h3>确认状态更改</h3><p>状态尚未保存，请填写理由后确认。</p>';
      const action = form.kind === 'merge' ? 'merge-confirm' : form.kind === 'undo' ? 'undo-confirm' : form.kind === 'status' ? 'status-submit' : 'confirm-review';
      const label = form.kind === 'merge' ? '合并更新' : form.kind === 'reject' ? '拒绝' : form.kind === 'undo' ? '确认撤销合并' : form.kind === 'status' ? '保存状态' : '作为新任务';
      return '<section class="form-panel" data-testid="review-form" aria-label="待办审核操作">' + content + '<details class="review-checks"' + (['reject', 'undo', 'status'].includes(form.kind) ? ' open' : '') + '><summary>理由与校验</summary><label class="reason-field">' + (['reject', 'undo', 'status'].includes(form.kind) ? '理由（请手写，必填）' : '理由（可编辑）') + '<textarea data-field="reason" data-testid="review-reason" required>' + esc(form.reason) + '</textarea></label></details><div class="actions"><button type="button" class="primary" data-action="' + action + '" data-testid="' + action + '"' + (form.kind === 'merge' && !validPreview(form) ? ' disabled' : '') + '>' + label + '</button>' + (form.kind === 'merge' ? '<button type="button" data-action="candidate-create" data-index="' + state.candidates.items.indexOf(form.item) + '" data-testid="candidate-create">作为新任务</button><button type="button" class="danger" data-action="candidate-reject" data-index="' + state.candidates.items.indexOf(form.item) + '" data-testid="candidate-reject">拒绝</button>' : '') + '<button class="link" type="button" data-action="cancel-form">取消</button></div></section>';
    }
    function render() {
      identityLabel(); workspacePicker.disabled = state.busy || state.loading || !state.workspaces.length;
      if (!state.workspace) { root.innerHTML = '<p class="empty">请先到<a href="/#meetings">会议页</a>创建或选择工作区。</p>'; root.setAttribute('aria-busy', 'false'); return; }
      const waiting = state.busy || state.loading; root.setAttribute('aria-busy', String(waiting));
      if (doc.body && doc.body.dataset) doc.body.dataset.busy = String(state.busy);
      // Read the active control at each render, including async replies, rather than capturing
      // the control that initiated an older request and stealing focus back to it.
      const active = doc.activeElement;
      let focus = null;
      if (!state.focusForm && state.renderedForm === state.form && active && root.contains && root.contains(active) && active.dataset) {
        const field = active.dataset.mergeField ? 'data-merge-field' : active.dataset.field ? 'data-field' : null;
        const value = field === 'data-merge-field' ? active.dataset.mergeField : active.dataset.field;
        if (field && /^[a-zA-Z_-]+$/.test(value)) focus = {selector: '[' + field + '="' + value + '"]', start: active.selectionStart, end: active.selectionEnd};
      }
      root.innerHTML = '<div class="tasks-heading"><h1>待办</h1><nav class="tabs" aria-label="待办视图"><button type="button" data-action="tab-candidates"' + (state.view === 'candidates' ? ' aria-current="page"' : '') + '>看板</button><button type="button" data-action="tab-tasks"' + (state.view === 'tasks' ? ' aria-current="page"' : '') + '>按负责人</button></nav></div>' + (state.view === 'candidates' ? boardHtml() : '<div class="layout owner-layout">' + tasksHtml() + (state.detail ? detailHtml() : '<aside class="detail"><h2>任务详情与变化历史</h2><p class="empty">从左侧选择一项任务。</p></aside>') + '</div>');
      if (waiting && root.querySelectorAll) root.querySelectorAll('button,input,select,textarea').forEach((element) => { element.disabled = true; });
      state.renderedForm = state.form;
      if (!waiting && focus && root.querySelector) {
        const control = root.querySelector(focus.selector);
        if (control && !control.disabled) {
          control.focus({preventScroll: true});
          if (focus.start != null && control.setSelectionRange) control.setSelectionRange(focus.start, focus.end);
        }
      }
      if (!waiting && state.focusForm && state.form && root.querySelector) {
        state.focusForm = false;
        const panel = root.querySelector('[data-testid="review-form"]');
        if (panel && panel.scrollIntoView) panel.scrollIntoView({block: 'nearest'});
        const first = root.querySelector('[data-testid="review-form"] input, [data-testid="review-form"] select, [data-testid="review-reason"]');
        if (first && first.focus) first.focus();
      }
    }
    function formReason(form) { if (form.reasonEdited) return; form.reason = defaultReason(form.kind, form.kind === 'create' ? Object.keys(patchFor(form.item.candidate, form)) : form.applyFields || []); }
    async function showForm(kind, index) {
      const item = state.candidates.items[index]; if (!item || state.blockedReviews.has(candidateKey(item))) return; const candidate = item.candidate;
      state.selection = item;
      state.form = {kind, item, title: candidate.title || '', owner: candidate.owner || '', deadline: candidate.deadline || '', deadline_text: candidate.deadline_text || '', reason: '', reasonEdited: false, target: '', applyFields: [], preview: null};
      state.focusForm = true; formReason(state.form); await showEvidence(index); render();
      const first = root.querySelector && root.querySelector('[data-testid="review-form"] input, [data-testid="review-form"] select, [data-testid="review-reason"]'); if (first && first.focus) first.focus();
    }
    async function showEvidence(index, offset = 0) {
      const item = state.candidates.items[index]; if (!item) return;
      const epoch = state.epoch, value = await request('/api/product/task-candidates/' + id(item.candidate_id) + '/evidence', {}, {...listParams(offset), candidate_fingerprint: item.candidate_fingerprint});
      if (current(epoch)) state.evidence[item.candidate_id + ':' + item.candidate_fingerprint] = value;
    }
    function reasonValid() { if (!state.form || !state.form.reason.trim()) { setNotice('请填写本次操作的理由。', 'error', 'REASON_REQUIRED'); return false; } return true; }
    async function review() {
      const form = state.form; if (!form || !reasonValid()) return;
      if (form.kind === 'merge' && !validPreview(form)) return;
      const patch = form.kind === 'create' ? patchFor(form.item.candidate, form) : {};
      if (form.kind === 'create' && !(patch.title == null ? form.item.candidate.title : patch.title)) { setNotice('请补充任务描述。', 'error', 'TITLE_REQUIRED'); return; }
      if (patch.deadline != null && !/^\d{4}-\d{2}-\d{2}$/.test(patch.deadline)) { setNotice('截止日期请按 YYYY-MM-DD 填写，或留空保留原文。', 'error', 'INVALID_REQUEST'); return; }
      await write(async (reviewer) => {
        const body = {action: form.kind, expected_candidate_fingerprint: form.item.candidate_fingerprint, reviewer, reason: form.reason.trim()};
        if (form.kind === 'create') { if (Object.keys(patch).length) body.patch = patch; if (form.item.task_id && form.item.review_revision != null) body.expected_review_revision = form.item.review_revision; }
        if (form.kind === 'merge') { if (!validPreview(form)) throw {code: 'PREVIEW_STALE', status: 409}; Object.assign(body, {task_id: form.target, apply_fields: form.applyFields, expected_review_revision: form.preview.review_revision, expected_preview_token: form.preview.preview_token}); }
        const receipt = await post('/api/product/task-candidates/' + id(form.item.candidate_id) + '/review', body); state.writeCompleted = true; state.blockedReviews.add(candidateKey(form.item)); state.form = null;
        state.lastResult = receipt.task ? {task_id: receipt.task.id, merge_id: form.kind === 'merge' ? receipt.merge_id : null} : null;
        state.receipt = form.kind === 'merge' ? state.lastResult : null;
        if (state.lastResult) updateUrl(state.lastResult.task_id, state.lastResult.merge_id);
        setNotice(form.kind === 'reject' ? '已拒绝候选，理由已记录。' : form.kind === 'merge' ? '已合并；字段变化与对应会议原文已记录。' : '已确认正式待办。', 'success', '', state.lastResult);
        await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]);
      });
    }
    async function handleAction(event) {
      const element = event.target ? event.target.closest('[data-action]') : event;
      if (!element || !element.dataset) return; if (event.preventDefault) event.preventDefault();
      const action = element.dataset.action; if (state.busy || state.loading) return; const index = Number(element.dataset.index);
      if (action === 'refresh') { await refreshCurrent(); return; }
      if (action === 'cancel-form') { state.form = null; render(); return; }
      if (action === 'close-detail') { ++state.taskRequest; state.detail = null; state.events = EMPTY_PAGE(); state.form = null; state.receipt = null; updateUrl(null); render(); return; }
      if (['candidate-create', 'candidate-reject', 'candidate-merge'].includes(action)) { await runRead(() => showForm(action.replace('candidate-', ''), index)); return; }
      if (action === 'candidate-select') { await runRead(async () => { const item = state.candidates.items[index] || null; state.selection = state.selection === item ? null : item; state.form = null; if (state.selection) await showEvidence(index); }); return; }
      if (action === 'choose-same-task') {
        const item = state.candidates.items[index], target = state.catalog.find(value => value.task.id === element.dataset.task);
        if (!item || !target || item.candidate.title !== target.task.title || !item.is_current || item.reviewed || state.blockedReviews.has(candidateKey(item))) return;
        await runRead(async () => {
          await showForm('merge', index); const form = state.form; if (!form || form.item !== item) return;
          const proposed = {title: item.candidate.title, owner: item.candidate.owner, deadline: {deadline: item.candidate.deadline, deadline_text: item.candidate.deadline_text}};
          const values = taskFields(target.task); form.target = target.task.id;
          form.applyFields = ['title', 'owner', 'deadline'].filter(field => comparable(field, values[field]) !== comparable(field, proposed[field]));
          formReason(form); render(); await previewMerge();
        }); return;
      }
      if (action === 'candidate-evidence') { await runRead(() => showEvidence(index)); return; }
      if (action.startsWith('evidence-page-')) { await runRead(() => showEvidence(Number(action.substring(14)), Number(element.dataset.offset))); return; }
      if (action === 'tab-candidates' || action === 'tab-tasks') { state.view = action === 'tab-tasks' ? 'tasks' : 'candidates'; state.form = null; state.detail = null; updateUrl(state.receipt && state.receipt.task_id, state.receipt && state.receipt.merge_id); await runRead(() => state.view === 'tasks' ? loadTasks() : loadCandidates()); return; }
      if (action === 'candidate-page') { state.candidateOffset = Number(element.dataset.offset); state.form = null; await runRead(loadCandidates); return; }
      if (action === 'task-page' || action === 'filter-tasks') { if (state.ownerMode === 'named' && !state.owner.trim()) { setNotice('请填写要筛选的负责人原文。', 'error', 'INVALID_REQUEST'); return; } state.taskOffset = action === 'task-page' ? Number(element.dataset.offset) : 0; await runRead(() => loadTasks()); return; }
      if (action === 'task-detail' || action === 'view-task') { await runRead(() => openTask(element.dataset.task, {keepReceipt: true, mergeId: element.dataset.merge || undefined})); return; }
      if (action === 'confirm-review' || action === 'merge-confirm') { await review(); return; }
      if (action === 'undo-preview' && state.detail) {
        const taskId = state.detail.task.id, mergeId = element.dataset.merge;
        await runRead(async () => { const epoch = state.epoch, preview = await post('/api/product/tasks/' + id(taskId) + '/merges/' + id(mergeId) + '/undo-preview', {}); if (current(epoch) && state.detail && state.detail.task.id === taskId) { state.form = {kind: 'undo', taskId, mergeId, preview, reason: ''}; state.focusForm = true; } }); return;
      }
      if (action === 'undo-confirm') {
        const form = state.form; if (!form || form.kind !== 'undo' || !reasonValid()) return;
        await write(async (reviewer) => { await post('/api/product/tasks/' + id(form.taskId) + '/merges/' + id(form.mergeId) + '/undo', {expected_review_revision: form.preview.review_revision, expected_preview_token: form.preview.preview_token, reviewer, reason: form.reason.trim()}); state.writeCompleted = true; state.lastResult = {task_id: form.taskId}; updateUrl(form.taskId); state.form = null; state.receipt = null; await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]); await openTask(form.taskId); setNotice('已撤销合并，全部历史保留。当前有效候选可在待确认列表继续审核；来源变化时需复核。', 'success', '', state.lastResult); }); return;
      }
      if (action === 'status-open' && state.detail) { state.form = {kind: 'status', taskId: state.detail.task.id, status: state.detail.task.status, reason: ''}; state.focusForm = true; render(); return; }
      if (action === 'status-submit') {
        const form = state.form; if (!form || form.kind !== 'status' || !reasonValid()) return;
        if (state.detail && form.status === state.detail.task.status) { setNotice('请选择与当前不同的任务状态。', 'error', 'INVALID_REQUEST'); return; }
        await write(async (actor) => { await post('/api/product/tasks/' + id(form.taskId) + '/status', {status: form.status, actor, reason: form.reason.trim()}); state.writeCompleted = true; state.lastResult = {task_id: form.taskId}; state.form = null; await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]); await openTask(form.taskId, {keepReceipt: true}); setNotice('任务状态已更新并追加到变化历史。', 'success', '', state.lastResult); });
      }
    }
    const inputTarget = (event) => event.target || event;
    function handleInput(event) {
      const target = inputTarget(event), field = target.dataset && target.dataset.field; if (state.busy || state.loading || !field) return;
      if (field === 'ownerFilter') { state.owner = target.value; return; }
      const form = state.form; if (!form) return;
      if (field === 'reason') { form.reason = target.value; form.reasonEdited = true; return; }
      if (['title', 'owner', 'deadline', 'deadline_text'].includes(field)) { form[field] = target.value; formReason(form); const reasonInput = root.querySelector && root.querySelector('[data-testid="review-reason"]'); if (reasonInput) reasonInput.value = form.reason; }
    }
    async function handleChange(event) {
      const target = inputTarget(event), field = target.dataset && target.dataset.field; if (state.busy || state.loading) return;
      if (target.dataset && target.dataset.mergeField && state.form && state.form.kind === 'merge') {
        const form = state.form, name = target.dataset.mergeField; form.applyFields = ['title', 'owner', 'deadline'].filter((value) => value === name ? target.checked : form.applyFields.includes(value)); form.preview = null; formReason(form); await previewMerge(); return;
      }
      if (field === 'detailStatus' && state.detail) { const previous = state.form && state.form.kind === 'status' && state.form.taskId === state.detail.task.id ? state.form : null; state.form = {kind: 'status', taskId: state.detail.task.id, status: target.value, reason: previous ? previous.reason : ''}; state.focusForm = true; render(); return; }
      if (field === 'candidateState') { state.candidateState = target.value; state.candidateOffset = 0; state.form = null; await runRead(loadCandidates); return; }
      if (field === 'ownerMode') { state.ownerMode = target.value; render(); return; }
      if (field === 'statusFilter') { state.status = target.value; return; }
      if (field === 'mergeTarget' && state.form && state.form.kind === 'merge') {
        if (state.form.target !== target.value) { state.form.applyFields = []; formReason(state.form); }
        state.form.target = target.value; state.form.preview = null;
        const options = root.querySelector && root.querySelector('[data-testid="merge-field-options"]');
        if (options) options.innerHTML = mergeFieldsHtml(state.form);
        await previewMerge(); return;
      }
      if (field === 'status' && state.form) { state.form.status = target.value; return; }
      handleInput(event);
    }
    async function changeWorkspace(workspaceId) {
      if (state.busy) return; const workspace = state.workspaces.find((item) => item.workspace_id === workspaceId); if (!workspace) return;
      const epoch = ++state.epoch; state.loading = true; state.form = null; state.detail = null; state.receipt = null; state.lastResult = null; state.blockedReviews.clear(); state.selection = null; state.catalog = []; state.recent = []; ++state.recentRequest; state.notice = null; notice.innerHTML = ''; state.evidence = {}; state.candidates = EMPTY_PAGE(); state.tasks = EMPTY_PAGE(); state.events = EMPTY_PAGE(); state.candidateOffset = state.taskOffset = state.eventOffset = 0; state.workspace = workspace; render();
      try { await request('/api/workspaces/current', {method: 'POST', body: JSON.stringify({workspace_id: workspaceId})}); if (!current(epoch)) return; updateUrl(null); await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]); }
      catch (error) { if (current(epoch)) fail(error); } finally { if (current(epoch)) { state.loading = false; render(); } }
    }
    async function start() {
      if (state.started) return; state.started = true;
      root.addEventListener('click', (event) => { handleAction(event).catch(fail); }); notice.addEventListener('click', (event) => { handleAction(event).catch(fail); }); root.addEventListener('input', handleInput); root.addEventListener('change', (event) => { handleChange(event).catch(fail); });
      identityButton.onclick = () => { if (!state.busy) getIdentity(true); }; workspacePicker.onchange = () => { changeWorkspace(workspacePicker.value).catch(fail); }; identityLabel();
      try {
        const [workspaces, selected] = await Promise.all([request('/api/workspaces'), request('/api/workspaces/current')]); state.workspaces = workspaces.workspaces || [];
        const query = new URLSearchParams(loc.search || ''); state.workspace = state.workspaces.find((item) => item.workspace_id === query.get('workspace_id')) || selected.workspace || null;
        workspacePicker.innerHTML = state.workspaces.length ? state.workspaces.map((workspace) => '<option value="' + esc(workspace.workspace_id) + '"' + (state.workspace && workspace.workspace_id === state.workspace.workspace_id ? ' selected' : '') + '>' + esc(workspace.name) + '</option>').join('') : '<option>尚无工作区</option>';
        if (state.workspace) { state.loading = true; render(); await Promise.all([loadCandidates(), loadTasks(), loadCatalog()]); if (query.get('task')) { if (query.get('merge')) state.receipt = {task_id: query.get('task'), merge_id: query.get('merge')}; if (query.get('view') === 'candidates') { setNotice('操作已记录，可查看任务与变化历史。', 'success', '', {task_id: query.get('task'), merge_id: query.get('merge')}); } else { await openTask(query.get('task'), {keepReceipt: true}); } } }
      } catch (error) { fail(error); } finally { state.loading = false; render(); }
    }
    return {state, start, render, handleAction, handleInput, handleChange, changeWorkspace, loadCandidates, loadTasks, loadCatalog, openTask, refreshCurrent, getIdentity};
  }
  global.LinkLoomTasks = {createApp, helpers: {esc, time, meetingDate, diffHtml, patchFor, defaultReason, errorText, evidenceHtml, lowHtml, STORAGE_KEY}};
  if (global.document && !global.LINKLOOM_TASKS_NO_AUTO_START) {
    const mount = () => { if (global.document.getElementById('tasks-app')) { const app = createApp(); global.linkloomTasksApp = app; app.start(); } };
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', mount); else mount();
  }
})(typeof window !== 'undefined' ? window : globalThis);
