/* Deterministic template snapshots; these are NOT browser screenshots. */
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
const baseline='7720390a796662ff42c27b5eef6f8685e5155802';
const destination=process.argv[2] || 'work/copy-template-snapshots';
fs.mkdirSync(destination,{recursive:true});
const files=['product.js','product-task-result.js'];
const source=(name,before)=>before?execFileSync('git',['show',`${baseline}:src/linkloom/ui/static/${name}`],{encoding:'utf8'}):fs.readFileSync(`src/linkloom/ui/static/${name}`,'utf8');
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function sources(before,locale){
 const elements=new Map();
 const element=id=>{if(!elements.has(id))elements.set(id,{innerHTML:'',textContent:'',value:'',dataset:{},addEventListener(){},setAttribute(){},removeAttribute(){}});return elements.get(id);};
 const fetch=async url=>({ok:true,json:async()=>url==='/api/workspaces'?{workspaces:[{workspace_id:'w',name:'Public project'}]}:url==='/api/workspaces/current'?{workspace:{workspace_id:'w',name:'Public project'}}:url==='/api/status'?{ready:false,message:'AI extraction is not configured.'}:url==='/api/sources'?{sources:[]}:{candidates:[]}});
 vm.runInNewContext(source('product.js',before),{document:{documentElement:{lang:locale},getElementById:element,querySelectorAll:()=>[]},window:{setTimeout(){}},fetch,console});
 await new Promise(resolve=>setImmediate(resolve));await new Promise(resolve=>setImmediate(resolve));
 return element('page').innerHTML;
}
function task(before,locale){
 const context={window:{}};vm.runInNewContext(source('product-task-result.js',before),context);
 const observations=[{observation_id:'a',description:'Public migration task',owner:'Mira',deadline:'2029-06-20',deadline_role:'TASK_DEADLINE',provenance:{evidence_ref:'a',quote:'Mira will finish the migration by June 20.'}},{observation_id:'b',description:'Public migration task',deadline_text:'late June',deadline_role:'UNKNOWN',unknown_fields:['owner','deadline'],provenance:{evidence_ref:'b',quote:'We discussed late June; confirmation is pending.'}}];
 const result={status:'PARTIAL',reviewer:{accepted:true},preparation:{execution_kind:'FAKE',gaps:['DISCOVERY_COVERAGE_INCOMPLETE']},evidence:observations.map((o,i)=>({evidence_ref:o.observation_id,source_name:`Public meeting ${i+1}`,source_version:`v${i+1}`,line_start:1,line_end:1})),task_review:{observations,proposals:[{predicate:'SAME_TASK',proposed_verdict:'UNKNOWN',evidence_status:'EVIDENCE_VALID',missing_evidence:['The records need a direct link.']}],steps:[{kind:'relation',predicate:'SAME_TASK',status:'UNKNOWN',blocking_reasons:['P4_VERDICT_UNKNOWN']}],next_actions:[{kind:'REVIEW_ORIGINALS'}]}};
 return context.window.renderTaskAnswer(result,{escapeHtml:esc,t:(en,zh)=>locale==='zh-CN'?zh:en,date:esc,citation:e=>`<blockquote>${esc(e.quote)}</blockquote>`});
}
(async()=>{
 const summary=[];
 for(const before of [true,false])for(const locale of ['en','zh-CN'])for(const screen of ['sources','task']){
  const rendered=screen==='sources'?await sources(before,locale):task(before,locale);
  const html=rendered.replace(/^[ \t]+$/gm,''); // empty template indentation only
  const name=`${before?'before':'after'}-${screen}-${locale}.html`;
  fs.writeFileSync(path.join(destination,name),`<!doctype html><html lang="${locale}"><meta charset="utf-8"><title>Template snapshot — not browser acceptance</title><body>${html}</body></html>`);
  summary.push({file:name,baseline:before?baseline:null,locale,screen,h1:(html.match(/<h1[ >]/g)||[]).length,eyebrows:(html.match(/class="eyebrow"/g)||[]).length,ledes:(html.match(/class="lede"/g)||[]).length,disclosures:(html.match(/<details[ >]/g)||[]).length});
 }
 assert.equal(summary.length,8);
 fs.writeFileSync(path.join(destination,'SUMMARY.json'),JSON.stringify({kind:'VM_TEMPLATE_SNAPSHOTS_NOT_BROWSER',normalization:'Empty template indentation lines removed; public fixture content unchanged',baseline,records:summary},null,2)+'\n');
 console.log('Captured 8 deterministic template snapshots; browser/visual acceptance remains separate.');
})().catch(e=>{console.error(e);process.exitCode=1;});
