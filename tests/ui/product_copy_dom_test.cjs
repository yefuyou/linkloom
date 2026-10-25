/* Presentation contracts, not a substitute for Chromium visual acceptance. */
const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
const script = fs.readFileSync('src/linkloom/ui/static/product.js', 'utf8');
const taskScript = fs.readFileSync('src/linkloom/ui/static/product-task-result.js', 'utf8');

async function open(locale, {ready = false, external = false, task = false} = {}) {
  const elements = new Map(), writes = [];
  function element(id) {
    if (!elements.has(id)) elements.set(id, {id, innerHTML: '', textContent: '', value: '', dataset: {},
      listeners: {}, addEventListener(type, fn) {this.listeners[type] = fn;},
      setAttribute() {}, removeAttribute() {}, querySelector() {return null;}, querySelectorAll() {return [];}});
    return elements.get(id);
  }
  const nav = ['sources','review','decisions','ask'].map(screen => Object.assign(element(screen), {dataset:{screen}}));
  const sourceName = 'Sources <script>PRIVATE_CONTENT</script>';
  const source = {source_id: 'source-a', name: sourceName, source_version:'VERSION_X', updated_at:'2026-10-25',
    ingestion_status:'FAILED', error:'PROVIDER_UNAVAILABLE: setup flags', candidate_count:0, review_required_count:0,
    segment_summary:{total:1,processed:0,failed:1,retryable:0}};
  const window = {location:{search:`?lang=${locale}`}, localStorage:{getItem(){return null;}, setItem(){}}, setTimeout(){}};
  const document = {documentElement:{lang:'en'}, getElementById:element,
    querySelectorAll(selector) {return selector.includes('primary-nav') ? nav : [];}};
  const decision={decision_id:'d',subject:'Project',relation:'uses supplier',value:'Approved supplier',status:'CURRENT',memory_state:'ACTIVE',valid_from:'2026-10-01',evidence:[],authorization:{receipt_id:'RECEIPT_A'}};
  const config = {workspace_id:'w', scopes:[], investigation_context:{source_count:1,sources:[{name:sourceName,version:'VERSION_X'}]},
    agent:{available:task, external_provider:external, task_investigation:task, task_provider:{model_id:'MODEL_INTERNAL',
      budgets:{max_requests:16,max_input_tokens:5000,max_output_tokens:1000,max_cost_usd:2,max_seconds:120}}}};
  async function fetch(path, options = {}) {
    if (options.method) writes.push(path);
    const payload = path === '/api/workspaces' ? {workspaces:[{workspace_id:'w',name:'Workspace'}]}
      : path === '/api/workspaces/current' ? {workspace:{workspace_id:'w',name:'Workspace'}}
      : path === '/api/status' ? {ready,message:'INTERNAL_RUNNER_STATUS'}
      : path === '/api/sources' ? {sources:[source]}
      : path === '/api/review/candidates' ? {candidates:[],supporting_proposals:[]}
      : path === '/api/review/attention' ? {decisions:[]}
      : path === '/api/decisions' ? {decisions:[decision]}
      : path === '/api/decisions/d' ? {decision}
      : path === '/api/decisions/d/history' ? {history:[decision]}
      : path === '/api/decisions/recover' ? {recovery:{status:'FOUND',as_of:'2026-10-25',effective:decision,transitions:[],proposals:[]}}
      : path === '/api/product/ask/config' ? config : {entries:[]};
    return {ok:true,json:async()=>payload};
  }
  vm.runInNewContext(taskScript+'\n'+script, {document,window,fetch,URLSearchParams,console,setTimeout});
  await tick(); await tick();
  return {element,writes,sourceName,nav};
}

(async()=>{
  for (const locale of ['en','zh']) {
    const zh = locale === 'zh';
    const ui = await open(locale);
    let html = ui.element('page').innerHTML;
    assert.equal((html.match(/<h1[ >]/g)||[]).length,1);
    assert.doesNotMatch(html, /class="eyebrow"|class="lede"/);
    assert.ok(html.includes(zh?'来源':'Sources'));
    assert.ok(html.includes(zh?'保存来源':'Save source'));
    assert.ok(html.includes(zh?'已保存 · 待提取':'Saved · extraction pending'));
    assert.ok(html.includes('data-status="FAILED"'), 'underlying failure state must be preserved');
    assert.ok(html.includes('Sources &lt;script&gt;PRIVATE_CONTENT&lt;/script&gt;'), 'source text must stay untranslated and escaped');
    assert.doesNotMatch(html, /<script>|uiText\(/);
    assert.match(html, /<details><summary>[^<]+<\/summary><p>INTERNAL_RUNNER_STATUS<\/p><\/details>/, 'setup diagnostics must be inside a disclosure');
    await ui.element('interface-language').listeners.change({target:{value:zh?'en':'zh'}}); await tick();
    assert.ok(ui.element('page').innerHTML.includes(zh?'<h1>Sources</h1>':'<h1>来源</h1>'));
    await ui.element('interface-language').listeners.change({target:{value:locale}}); await tick();

    for (const screen of ['review','decisions','ask']) {
      await ui.element(screen).listeners.click(); await tick();
      html = ui.element('page').innerHTML;
      assert.equal((html.match(/<h1[ >]/g)||[]).length,1);
      assert.doesNotMatch(html,/class="eyebrow"|class="lede"/);
      assert.ok(html.includes(zh ? ({review:'审核',decisions:'决策记忆',ask:'提问'}[screen]) : ({review:'Review',decisions:'Decision Memory',ask:'Ask'}[screen])));
    }
    assert.equal(ui.writes.length,0,'navigation and localization never send a request authorizing a model');
    await ui.element('page').listeners.click({target:{closest:()=>({dataset:{action:'inspect-decision',id:'d'}})}});
    const history=ui.element('page').innerHTML;
    assert.ok(history.includes(zh?'已找到审核记录':'Reviewed record found'));
    assert.ok(history.includes(zh?'当前有效':'Current'));
    assert.ok(!history.includes(zh?'待确认':'Needs confirmation'),'current records must not be relabeled unknown');
    assert.equal((history.match(/data-status="ACTIVE"/g)||[]).length,1,'one lifecycle badge per history entry');
    assert.ok(history.indexOf('Approved supplier')<history.indexOf('RECEIPT_A'));

    const paid = await open(locale,{ready:true,task:true,external:true});
    assert.ok(paid.element('page').innerHTML.includes(zh?'可能产生模型费用':'may incur model costs'));
    assert.ok(paid.element('page').innerHTML.includes(zh?'发送来源内容':'sends source text'));
    await paid.element('ask').listeners.click(); await tick();
    const form = paid.element('page').innerHTML;
    assert.ok(form.includes('id="ask-external" type="checkbox"'));
    assert.ok(!form.includes('id="ask-external" type="checkbox" checked'));
    assert.ok(form.includes(zh?'外部 AI':'external AI provider'));
    assert.ok(form.includes(zh?'有上限的模型费用':'bounded model costs'));
    assert.ok(form.indexOf('USD 2') < form.indexOf('id="ask-submit-button"'));
    assert.ok(form.includes('<details><summary>') && form.includes('MODEL_INTERNAL'));
  }
  console.log('PASS: bilingual Sources/Review/Decision Memory/Ask, single titles, escaped unchanged data, raw status preservation, visible cost and unchecked per-question permission');
})().catch(e=>{console.error(e);process.exitCode=1;});
