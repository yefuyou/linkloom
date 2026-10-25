/* Optional real-browser acceptance. Uses a fresh synthetic HTTP/SQLite demo,
   a new no-login Chromium context and the existing FakeProvider only. */
const {chromium} = require("playwright");
const {spawn} = require("node:child_process");
const fs = require("node:fs/promises");
const path = require("node:path");
const {strict: assert} = require("node:assert");

(async () => {
  const directory = path.resolve(process.argv[2] || `work/stale-browser-${Date.now()}`);
  const python = process.env.LINKLOOM_TEST_PYTHON || "python";
  const server = spawn(python, ["scripts/demo_stale_revalidation.py", "--directory", directory], {
    env: {...process.env, PYTHONPATH: ["src", "."].join(path.delimiter), GEMINI_API_KEY: "", DEEPSEEK_API_KEY: ""},
    stdio: ["ignore", "pipe", "inherit"],
  });
  let browser;
  try {
    const metadata = await new Promise((resolve, reject) => {
      let text = "";
      const timer = setTimeout(() => reject(new Error("Synthetic server startup timed out")), 15000);
      server.stdout.on("data", data => {
        text += data;
        if (text.includes("\n")) {clearTimeout(timer); resolve(JSON.parse(text.split("\n")[0]));}
      });
      server.on("error", reject);
      server.on("exit", code => reject(new Error(`Synthetic server exited: ${code}`)));
    });
    browser = await chromium.launch({headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto(metadata.url);
    await page.getByRole("button", {name: /^Review/}).click();
    await page.getByRole("button", {name: "Revalidate decision", exact: true}).waitFor();
    await page.screenshot({path: path.join(directory, "01-before-attention.png"), fullPage: true});
    await page.getByRole("button", {name: "Revalidate decision", exact: true}).click();
    await page.getByRole("heading", {name: "Original decision: Birchline"}).waitFor();
    await page.screenshot({path: path.join(directory, "02-source-comparison.png"), fullPage: true});
    await page.getByRole("button", {name: "Keep unresolved", exact: true}).click();
    await page.getByRole("button", {name: "Revalidate decision", exact: true}).click();
    await page.getByRole("button", {name: "Review this new candidate"}).click();
    await page.getByLabel("Reviewer", {exact: true}).fill("browser-reviewer");
    await page.getByLabel("Review reason", {exact: true}).fill("Verified current synthetic source against original evidence");
    await page.screenshot({path: path.join(directory, "03-explicit-new-approval.png"), fullPage: true});
    await page.getByRole("button", {name: "Approve decision", exact: true}).click();
    await page.getByRole("heading", {name: "Decision timeline", exact: true}).waitFor();
    await page.getByText(/Revalidated/).waitFor();
    await page.screenshot({path: path.join(directory, "04-after-history.png"), fullPage: true});
    await page.getByRole("button", {name: /^Review/}).click();
    await page.getByText("No Decision Memory records currently need revalidation.").waitFor();
    await page.screenshot({path: path.join(directory, "05-after-attention.png"), fullPage: true});
    assert.deepEqual(errors, []);
    await fs.writeFile(path.join(directory, "browser-result.json"), JSON.stringify({
      status: "PASS", console_errors: errors, synthetic: true, provider: metadata.provider,
      original_decision_id: metadata.original_decision_id,
    }, null, 2));
    console.log(`PASS: real browser approval + SQLite; screenshots in ${directory}`);
  } finally {
    if (browser) await browser.close();
    server.kill("SIGINT");
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
