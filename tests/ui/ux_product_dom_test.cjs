/* L1 navigation, meeting counts and local terminology lifecycle. No model/provider is called. */
const {strict: assert} = require('node:assert');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
(async () => {
  const elements = new Map(), events = {}, writes = [], pages = [], storage = new Map([['linkloom.tasks.reviewer', 'local-reviewer']]);
  const element = id => {
    if (!elements.has(id)) elements.set(id, {id, value:'', dataset:{}, innerHTML:'', textContent:'', listeners:{}, isConnected:true,
      setAttribute(){}, removeAttribute(){}, hasAttribute(){return false;}, addEventListener(type, fn){this.listeners[type]=fn;}, querySelectorAll(){return [];}, querySelector(){return null;}});
    return elements.get(id);
  };
  const nav = ['sources','decisions','ask'].map(screen => Object.assign(element(screen), {dataset:{screen}}));
  const sources = ['a','b'].map(source_id => ({source_id,name:'meeting-'+source_id,source_version:'hash-'+source_id,candidate_count:2,ingestion_status:'COMPLETE',segment_summary:{failed:0,retryable:0}}));
  const evidence = {source_id:'a',source_name:'meeting-a',source_version:'hash-a',evidence_ref:'ref-a',quote:'We decided on Birchline.',line_start:1,line_end:1};
  const decision = {decision_id:'decision-a', subject:'Launch', relation:'uses supplier', value:'Birchline', memory_state:'ACTIVE', evidence:[evidence], valid_from:'2026-10-01T10:00:00Z'};
  let entries = [], transitioned = true;
  const oldEvidence = {...evidence, evidence_ref:'ref-old',quote:'The previous approved supplier was Northstar.'};
  const oldDecision = {...decision,decision_id:'decision-old',memory_state:'SUPERSEDED',evidence:[oldEvidence]};
  const fetch = async (path, options={}) => {
    if (options.method) writes.push({path,body:JSON.parse(options.body)});
    let payload;
    if (path.startsWith('/api/product/task-candidates?')) {
      const offset = Number(new URLSearchParams(path.split('?')[1]).get('offset')); pages.push(offset);
      payload = offset === 0 ? {items:[{source_summary:[{source_id:'a'},{source_id:'a'}]}],page:{has_more:true,next_offset:100}} : {items:[{source_summary:[{source_id:'b'}]}],page:{has_more:false}};
    } else if (path === '/api/workspaces') payload={workspaces:[{workspace_id:'w',name:'Synthetic'}]};
    else if (path === '/api/workspaces/current') payload={workspace:{workspace_id:'w',name:'Synthetic'}};
    else if (path === '/api/status') payload={ready:false};
    else if (path === '/api/sources') payload={sources};
    else if (path.startsWith('/api/sources/')) payload={source:{content:'[2026-10-01T10:00:00Z] Alice: Synthetic meeting.'}};
    else if (path === '/api/review/candidates') payload={candidates:[{source_id:'a',claim_type:'DECISION'},{source_id:'a',claim_type:'FACT'},{source_id:'b',claim_type:'FACT'}]};
    else if (path === '/api/product/ask/config') payload={workspace_id:'w',scopes:[{subject:'Launch',relation:'uses supplier'}],investigation_context:{source_count:2,sources:sources.map(s=>({...s,version:s.source_version}))},agent:{available:false}};
    else if (path === '/api/product/ask') payload={result:{workspace_id:'w',path:'DETERMINISTIC',status:'COMPLETED',query:'当前供应商？',scope:{subject:'Launch',relation:'uses supplier'},conclusions:[{decision_id:'decision-a',value:'Birchline'}],timeline:{effective:decision,history:[oldDecision,decision],transitions:transitioned?[{from_decision_id:'decision-old',to_decision_id:'decision-a'}]:[],proposals:[{value:'Not approved',evidence:{...evidence,evidence_ref:'unapproved-ref'}}]},evidence:[evidence]}};
    else if (path === '/api/product/feedback/propose') {entries=[{id:'feedback-a',term:'供方',relation:'uses supplier',status:'pending',actor:'local-reviewer'}];payload={entry:entries[0]};}
    else if (path.startsWith('/api/product/feedback?')) payload={entries};
    else if (path === '/api/decisions') payload={decisions:[decision]};
    else if (path === '/api/decisions/recover') payload={recovery:{effective:decision}};
    else payload={};
    return {ok:true,status:200,json:async()=>payload};
  };
  const location={pathname:'/',search:'?lang=zh',hash:'#meetings'};
  vm.runInNewContext(fs.readFileSync('src/linkloom/ui/static/product.js','utf8'), {
    document:{documentElement:{lang:'zh'},getElementById:element,querySelectorAll:selector=>selector.includes('primary-nav')?nav:[]},
    window:{location,history:{replaceState(){}},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)},sessionStorage:{getItem(){return null;}},addEventListener:(name,fn)=>events[name]=fn,setTimeout(){}},
    URLSearchParams,fetch,setTimeout,console,
    FormData:class{constructor(form){this.fields=form.fields||{};} *[Symbol.iterator](){yield* Object.entries(this.fields);}get(key){return this.fields[key];}}
  });
  await tick();await tick();
  const html=element('page').innerHTML;
  assert.deepEqual(pages,[0,100], 'meeting task count must consume every candidate page');
  const cards=html.split('<article class="card meeting-card"').slice(1).map(card=>card.split('</article>')[0]);
  assert.equal(cards.length,2);
  assert.match(cards[0],/1 条待办待确认 →/);
  assert.match(cards[1],/1 条待办待确认 →/);
  assert.match(cards[0],/1 条决定待确认 →/);
  assert.doesNotMatch(cards[1],/条决定待确认/, 'zero decisions stay hidden; facts must not be counted as decisions');
  assert.match(cards[0],/href="\/tasks\?/);
  assert.match(cards[0],/href="\/#review"/);
  assert.match(cards[0],/<time datetime="2026-10-01T10:00:00Z">\d{4}-\d{2}-\d{2}<\/time>/);
  const shell=fs.readFileSync('src/linkloom/ui/static/product.html','utf8').split('<nav class="primary-nav"')[1].split('</nav>')[0];
  assert.equal((shell.match(/<a /g)||[]).length,4);
  assert.doesNotMatch(shell,/data-screen="review"|刷新|Refresh/);
  await element('ask').listeners.click();await tick();
  const ask=element('page').innerHTML;
  assert.match(ask,/<details id="ask-advanced"><summary>/);
  const advanced=ask.split('<details id="ask-advanced">')[1].split('</details>')[0];
  for(const id of ['ask-language','ask-domain','ask-slot','ask-date']) assert.ok(advanced.includes('id="'+id+'"'));
  assert.doesNotMatch(ask,/重新提问|reset-ask/);
  assert.match(ask,/<details class="ask-source-details"><summary>.*来源详情/);
  assert.match(ask,/<details class="feedback-footer" id="feedback-panel" hidden>/);
  assert.equal(element('feedback-actor').value,'local-reviewer');
  assert.equal(element('feedback-identity-field').hidden,true);
  const samePage=element('page').innerHTML; location.hash='#main-content'; await events.hashchange();await tick();
  assert.equal(element('page').innerHTML,samePage,'skip-to-content must not navigate away from Ask');
  element('ask-form').fields={query:'当前供应商？',as_of:''};
  await element('page').listeners.submit({preventDefault(){},target:element('ask-form')});
  assert.match(element('ask-result').innerHTML,/当前有效/, element('page').innerHTML + JSON.stringify(writes));
  assert.match(element('ask-result').innerHTML,/已被替代/);
  assert.match(element('ask-result').innerHTML,/The previous approved supplier was Northstar/);
  const answered = element('ask-result').innerHTML;
  const citationCards = [...answered.matchAll(/<article class="ask-evidence">[\s\S]*?<\/article>/g)].map(match => match[0]);
  assert.equal(citationCards.length, 2, 'only the current and proven replaced formal citations become cards');
  assert.match(citationCards[0], /<span class="badge">当前有效<\/span>/);
  assert.match(citationCards[0], /<code>ref-a<\/code>/);
  assert.match(citationCards[1], /<span class="badge">已被替代<\/span>/);
  assert.match(citationCards[1], /<code>ref-old<\/code>/);
  assert.match(answered, /<details class="ask-technical"><summary>校验信息<\/summary>/);
  assert.match(answered.split('<details class="ask-technical">')[1], /unapproved-ref/, 'raw pending proposal remains available in collapsed verification JSON');
  assert.match(element('ask-result').innerHTML,/meeting-a/);
  assert.match(element('ask-result').innerHTML,/We decided on Birchline\./);
  assert.equal(element('feedback-panel').hidden,false);
  element('feedback-panel').open=true;
  element('feedback-form').fields={term:'供方',relation:'uses supplier'};
  await element('page').listeners.submit({preventDefault(){},target:element('feedback-form')});
  assert.equal(writes.at(-1).body.actor,'local-reviewer');
  assert.equal(writes.at(-1).path,'/api/product/feedback/propose');
  assert.equal(element('feedback-panel').hidden,false);
  assert.equal(element('feedback-panel').open,true);
  assert.match(element('feedback-entries').innerHTML,/feedback-approve/);
  assert.match(element('feedback-entries').innerHTML,/feedback-reject/);
  assert.doesNotMatch(element('feedback-entries').innerHTML,/feedback-revoke/);
  transitioned = false;
  await element('page').listeners.submit({preventDefault(){},target:element('ask-form')});
  const withoutTransition = element('ask-result').innerHTML;
  const remainingCards = [...withoutTransition.matchAll(/<article class="ask-evidence">[\s\S]*?<\/article>/g)].map(match => match[0]);
  assert.equal(remainingCards.length, 1, 'history without a formal transition cannot generate a replaced citation card');
  assert.match(remainingCards[0], /<span class="badge">当前有效<\/span>/);
  assert.match(remainingCards[0], /<code>ref-a<\/code>/);
  assert.match(withoutTransition.split('<details class="ask-technical">')[1], /ref-old/, 'unproven historical reference remains in raw verification data');
  await element('decisions').listeners.click();await tick();
  element('decisions-date-filter').fields={as_of:'2026-10-02'};
  await element('page').listeners.submit({preventDefault(){},target:element('decisions-date-filter')});
  assert.equal(writes.at(-1).path,'/api/decisions/recover');
  assert.equal(writes.at(-1).body.as_of,'2026-10-02');
  console.log('PASS: four entries, split paginated meeting counts, zero-decision hiding, advanced options, skip link, verified citations, local identity and separate correction review, effective-date lookup');
})().catch(error=>{console.error(error);process.exitCode=1;});
