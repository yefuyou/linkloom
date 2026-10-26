// Windows-only environment adapter for the unchanged POSIX browser harness.
const cp = require('node:child_process');
const path = require('node:path');
const spawn = cp.spawn;
cp.spawn = function(command, args, options) {
  if (options?.env?.PYTHONPATH === 'src:.') {
    options = {...options, env:{...options.env,
      PYTHONPATH:[path.resolve('src'),process.cwd()].join(path.delimiter)}};
  }
  if (args?.includes('--root') || args?.includes('--directory')) {
    args = [...args];
    const fs = require('node:fs'), os = require('node:os');
    // Runtime source-artifact names exceed Windows MAX_PATH under the long
    // screenshot directory. Change only the synthetic DB/artifact root.
    const flag=args.includes('--root')?'--root':'--directory';
    args[args.indexOf(flag)+1] = path.join(fs.mkdtempSync(path.join(os.tmpdir(),'ll26c-')),'db');
  }
  return spawn.call(this,command,args,options);
};
// The harness's custom executable path adds Linux single-process flags that
// crash installed Windows Chrome. Keep the normal Playwright launch flags.
const {chromium} = require('playwright');
const launch = chromium.launch.bind(chromium);
chromium.launch = async options => {
  const browser = await launch({...options,
    args:options?.args?.filter(flag => !['--single-process','--no-zygote'].includes(flag))});
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async options => {
    const context = await newContext(options);
    context.setDefaultTimeout(180000);
    const newPage=context.newPage.bind(context);
    context.newPage=async () => {
      const page=await newPage(), locator=page.locator.bind(page);
      page.locator=(selector,...args) => {
        const result=locator(selector,...args);
        if (selector==='[data-action="ask-decision"]') {
          const click=result.click.bind(result);
          result.click=async options => {
            // Clearing the historical date starts an async recovery GET.
            // Let that current-state response finish before opening Ask.
            await page.waitForLoadState('networkidle');
            return click(options);
          };
        }
        return result;
      };
      return page;
    };
    return context;
  };
  return browser;
};
