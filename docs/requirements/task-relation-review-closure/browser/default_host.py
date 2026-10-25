"""Real Chromium regression: opt-out Host retains ordinary task answers."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep

from playwright.sync_api import sync_playwright

root = Path.cwd()
out = root / "output/playwright/task-relation-review" / sys.argv[1] / "default-host"
out.mkdir(exist_ok=False)
database = root / "work/task-relation-review" / sys.argv[1] / "original.sqlite"
env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONUTF8="1", LINKLOOM_RETRIEVAL_MODE="bm25")
log = (out / "server.log").open("w", encoding="utf8")
server = subprocess.Popen([sys.executable, "-u", "-m", "linkloom.ui", "--port", "8791", "--database",
    str(database), "--offline-task-demo"], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
rows, console, sources = [], [], []
try:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, record_har_path=str(out / "browser.har"), record_har_content="embed")
        context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        def response(r):
            if "/api/" in r.url:
                try: body = r.json()
                except Exception: body = None
                rows.append(dict(url=r.url, method=r.request.method, status=r.status, request_body=r.request.post_data, body=body))
        page.on("response", response)
        page.on("pageerror", lambda e: console.append(dict(type="pageerror", text=str(e))))
        page.on("console", lambda m: console.append(dict(type=m.type, text=m.text)))
        try:
            for _ in range(40):
                try:
                    page.goto("http://127.0.0.1:8791/", wait_until="networkidle")
                    break
                except Exception: sleep(.25)
            for index in range(2):
                page.locator('[data-action="inspect-source"]').nth(index).click()
                page.locator("#update-content").wait_for()
                content = page.locator("#update-content").input_value()
                source = next(r["body"]["source"] for r in reversed(rows) if (r.get("body") or {}).get("source", {}).get("content") == content)
                digest = hashlib.sha256(content.encode("utf8")).hexdigest()
                assert digest == source["content_sha256"]
                for span in source["evidence"]:
                    assert span["quote"] in content
                    assert hashlib.sha256(span["quote"].encode("utf8")).hexdigest() == span["quote_sha256"]
                sources.append(dict(source_id=source["source_id"], version=source["source_version"], sha256=digest, exact_quotes=source["evidence"]))
                page.screenshot(path=str(out / f"source-{index + 1}.png"), full_page=True)
                page.locator('[data-action="back-sources"]').click()
            page.locator('[data-screen="ask"]').click()
            page.locator("#ask-domain").select_option("tasks")
            page.locator("#ask-query").fill("这两场会议中的完成日志迁移是否是同一任务？截止日期是否从2029-06-20调整到了2029-06-24？")
            page.locator("#ask-submit-button").click()
            page.locator(".task-answer").wait_for(timeout=60000)
            page.locator("#task-review-panel[data-task-review-unavailable]").wait_for()
            assert page.locator(".task-answer").count() == 1
            assert "LOCAL_TASK_REVIEW_DISABLED" in page.locator("#task-review-panel").inner_text()
            assert "日志迁移" in page.locator(".task-answer").inner_text()
            page.screenshot(path=str(out / "disabled-host-retains-answer.png"), full_page=True)
            (out / "dom.txt").write_text(page.locator("body").inner_text(), encoding="utf8")
            (out / "result.json").write_text(json.dumps(dict(status="PASS", browser=browser.version, sources=sources,
                http=rows, console=console, mode="SHIPPED_OFFLINE_DEMO_WITHOUT_LOCAL_REVIEW_OPT_IN"), ensure_ascii=False, indent=2), encoding="utf8")
            print("DEFAULT_HOST_BROWSER_PASS; two original source versions and exact quotes verified", flush=True)
        finally:
            context.tracing.stop(path=str(out / "trace.zip"))
            context.close()
            browser.close()
finally:
    server.terminate()
    server.wait(timeout=10)
    log.close()
