/* Real Chromium acceptance. Public synthetic local hosts; never reads a key.
   LINKLOOM_TEST_PYTHON=/path/to/python node tests/ui/product_copy_browser.cjs OUTPUT_DIR
   Baseline capture: use tests.task_answer_browser_server --static-root with the baseline assets. */
const {chromium} = require('playwright');
const {spawn} = require('node:child_process');
const fs = require('node:fs/promises');
const path = require('node:path');
const vm = require('node:vm');
const {strict: assert} = require('node:assert');
(async()=>{
  const directory = path.resolve(process.argv[2] || `work/copy-browser-${Date.now()}`);
  await fs.mkdir(directory, {recursive:true});
  const hosts = [], errors = [], receipts = [], scenarios = [];
  let browser;
  try {
    browser = await chromium.launch({headless:true});
    for (const kind of ['partial','unknown','conflict','model-conflict','p4']) {
      const host = spawn(process.env.LINKLOOM_TEST_PYTHON || 'python3', ['-m','tests.task_answer_browser_server',
        '--case',kind,'--root',path.join(directory,`${kind}-data`),'--port','0'], {
        env:{...process.env,PYTHONPATH:['src','.'].join(path.delimiter)},stdio:['ignore','pipe','pipe']});
      hosts.push(host);
      const url = await new Promise((resolve,reject)=>{
        let text='';
        const timer=setTimeout(()=>reject(new Error('Synthetic server startup timed out')),15000);
        host.stdout.on('data',data=>{text+=data;const match=text.match(/http:\/\/127\.0\.0\.1:\d+/);
          if(match){clearTimeout(timer);resolve(match[0]);}});
        host.on('exit',code=>{clearTimeout(timer);reject(new Error(`Host exited ${code}`));});
        host.on('error',reject);
      });
      const page=await browser.newPage({viewport:{width:1440,height:1000},locale:'en-US'});
      page.on('pageerror',error=>errors.push({kind,message:error.message}));
      page.on('response',response=>{if(response.url().startsWith(url+'/api/'))receipts.push({kind,url:response.url(),status:response.status()});});
      await page.goto(url+'/?lang=en');
      await page.locator('#interface-language').waitFor();
      for (const screen of ['sources','review','decisions','ask']) {
        await page.locator(`[data-screen="${screen}"]`).click();
        await page.locator('#page h1').waitFor();
        assert.equal(await page.locator('#page h1').count(),1);
        assert.equal(await page.locator('.page-heading .eyebrow, .page-heading .lede').count(),0);
        if(kind==='partial')await page.screenshot({path:path.join(directory,`after-${screen}-en.png`),fullPage:true});
        await page.locator('#interface-language').selectOption('zh');
        await page.getByRole('heading',{level:1,name:({sources:'来源',review:'审核',decisions:'决策记忆',ask:'提问'})[screen],exact:true}).waitFor();
        if(kind==='partial'){
          await page.screenshot({path:path.join(directory,`after-${screen}-zh.png`),fullPage:true});
          await page.setViewportSize({width:390,height:844});
          assert.ok(await page.locator('body').evaluate(el=>el.scrollWidth<=innerWidth),'Mobile overflow');
          await page.screenshot({path:path.join(directory,`after-${screen}-zh-mobile.png`),fullPage:true});
          await page.setViewportSize({width:1440,height:1000});
        }
        await page.locator('#interface-language').selectOption('en');
        await page.getByRole('heading',{level:1,name:({sources:'Sources',review:'Review',decisions:'Decision Memory',ask:'Ask'})[screen],exact:true}).waitFor();
      }
      // Existing end-to-end task checks, using the current text contract.
      await page.locator('#ask-language').selectOption('zh');
      const code=await fs.readFile('tests/ui/task_answer_chromium.js','utf8');
      const execute=vm.runInNewContext('('+code+'\n)');
      const report=await execute(page,directory); scenarios.push({kind,...report});
      if(kind==='partial'){
        const safety=vm.runInNewContext('('+await fs.readFile('tests/ui/task_answer_safety_chromium.js','utf8')+'\n)');
        await safety(page,directory); // actual source update, saved-result revalidation and workspace isolation
      }
      await page.close();
    }
    assert.deepEqual(errors,[]);
    await fs.writeFile(path.join(directory,'BROWSER_RESULT.json'),JSON.stringify({status:'PASS',scenarios,errors,receipts},null,2));
    console.log(`PASS: real Chromium scenarios and screenshots: ${directory}`);
  } catch(error) {
    await fs.writeFile(path.join(directory,'BROWSER_RESULT.json'),JSON.stringify({status:'INCOMPLETE',reason:error.message,scenarios,errors,receipts},null,2));
    throw error;
  } finally {if(browser)await browser.close(); for(const host of hosts)host.kill('SIGINT');}
})().catch(error=>{console.error(error.message);process.exitCode=1;});
