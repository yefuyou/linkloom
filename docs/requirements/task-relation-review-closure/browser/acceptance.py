"""Actual Chromium Product operations; scripted model transport, never proof seed.

Run from repository root with PYTHONPATH=src. Each label uses new databases and
an exclusive evidence directory. Source creation/review/continuation use forms.
Browser fetch is used only for explicitly labelled adversarial API checks.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import sleep

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    root = Path.cwd()
    out = root / "output/playwright/task-relation-review" / args.label
    out.mkdir(parents=True, exist_ok=False)
    data = root / "work/task-relation-review" / args.label
    data.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(root / "src"), PYTHONUTF8="1", LINKLOOM_RETRIEVAL_MODE="bm25")
    servers, logs, contexts, events = [], [], [], []
    streams = {key: (out / (key + ".jsonl")).open("w", encoding="utf8")
               for key in ("actions", "http", "console", "external")}

    def record(kind, **value):
        row = dict(time=datetime.now(timezone.utc).isoformat(), **value)
        streams[kind].write(json.dumps(row, ensure_ascii=False) + "\n")
        streams[kind].flush()
        if kind == "http":
            events.append(row)

    def start(name, command):
        log = (out / (name + "-server.log")).open("w", encoding="utf8")
        logs.append(log)
        process = subprocess.Popen([sys.executable, "-u", *command], cwd=root, env=env,
                                   stdout=log, stderr=subprocess.STDOUT)
        servers.append(process)

    def response(response):
        if "/api/" not in response.url:
            return
        try:
            body = response.json()
        except Exception:
            body = None
        record("http", url=response.url, method=response.request.method,
               request_body=response.request.post_data, status=response.status, body=body)

    def snap(page, name, selector=None):
        target = page.locator(selector) if selector else page
        target.screenshot(path=str(out / (name + ".png")), **({} if selector else {"full_page": True}))
        (out / (name + ".txt")).write_text(page.locator("body").inner_text(), encoding="utf8")
        record("actions", operation="SCREENSHOT", name=name, url=page.url)
        print(name, flush=True)

    def click(page, selector):
        record("actions", operation="UI_CLICK", selector=selector, url=page.url)
        page.locator(selector).click()

    def fill(page, selector, text):
        record("actions", operation="UI_FILL", selector=selector, text=text)
        page.locator(selector).fill(text)

    def post_form(page, step, action, note, outcome="UNSURE"):
        form = page.locator(f'[data-task-review-step="{step}"]')
        fill(page, f"#task-review-actor-{step}", "local-demo-reviewer")
        fill(page, f"#task-review-note-{step}", note)
        if action == "REVIEW":
            form.locator("select").select_option(outcome)
        with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/product/task-review")) as wait:
            click(page, f'[data-task-review-step="{step}"] [data-task-review-action="{action}"]')
        reply = wait.value
        body = reply.json()
        page.wait_for_timeout(250)
        return reply.status, body, json.loads(reply.request.post_data)

    def ask(page, question):
        if not page.locator("#ask-query").count():
            click(page, "[data-screen=ask]")
        page.locator("#ask-query").wait_for()
        page.locator("#ask-domain").select_option("tasks")
        fill(page, "#ask-query", question)
        click(page, "#ask-submit-button")
        page.locator('[data-task-review-step="identity"]').wait_for(timeout=60000)
        page.wait_for_timeout(250)
        run = page.evaluate("Object.entries(sessionStorage).find(([k])=>k.startsWith('linkloom:last-task:'))?.[1]")
        record("actions", operation="UI_INVESTIGATION_READY", run_id=run)
        return run

    def api_check(page, method, path, payload=None):
        record("actions", operation="ADVERSARIAL_API_CHECK", method=method, path=path, payload=payload)
        return page.evaluate("""async ({method,path,payload})=>{
          const r=await fetch(path,{method,headers:{'Content-Type':'application/json'},
            ...(payload?{body:JSON.stringify(payload)}:{})});return {status:r.status,body:await r.json()};
        }""", dict(method=method, path=path, payload=payload))

    try:
        start("original", ["-m", "linkloom.ui", "--port", "8791", "--database", str(data / "original.sqlite"),
                           "--offline-task-demo", "--allow-local-task-review"])
        start("positive", [str(Path(__file__).with_name("offline_source_host.py")), str(data / "positive.sqlite"), "8792"])
        p = sync_playwright().start()
        if p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            (out / "environment.json").write_text(json.dumps(dict(
                browser=browser.version, root=str(root), label=args.label,
                mode="REAL_CHROMIUM_UI_EXPLICIT_OFFLINE_MODEL_TRANSPORT_NO_PRESEEDED_PROOF",
            ), indent=2), encoding="utf8")

            def page_for(name, url):
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN",
                    record_har_path=str(out / (name + ".har")), record_har_content="embed")
                context.tracing.start(screenshots=True, snapshots=True, sources=False)
                contexts.append((name, context))
                def route(route):
                    if route.request.url.startswith(("http://127.0.0.1:8791/", "http://127.0.0.1:8792/", "data:")):
                        route.continue_()
                    else:
                        record("external", url=route.request.url)
                        route.abort()
                context.route("**/*", route)
                page = context.new_page()
                page.on("response", response)
                page.on("console", lambda m: record("console", context=name, type=m.type, text=m.text, location=m.location))
                page.on("pageerror", lambda e: record("console", context=name, type="pageerror", text=str(e)))
                page.on("dialog", lambda d: d.accept())
                for _attempt in range(80):
                    try:
                        page.goto(url, wait_until="networkidle")
                        break
                    except Exception:
                        sleep(.25)
                else:
                    raise RuntimeError("Product did not become reachable")
                return page

            original = page_for("original", "http://127.0.0.1:8791/")
            ask(original, "这两场会议中的完成日志迁移是否是同一任务？负责人是谁，截止日期是否从2029-06-20调整到了2029-06-24？")
            snap(original, "01-original-review-entry")
            status, receipt, payload = post_form(original, "identity", "REVIEW", "原文不足以证明身份；保留不确定。")
            assert status == 200 and receipt["receipt"]["status"] == "RECORDED", receipt
            status, blocked, _ = post_form(original, "revision", "CONTINUE", "独立检查原示例，不覆盖缺失前提。")
            assert status == 409 and blocked["status"] == "BLOCKED", blocked
            snap(original, "02-original-still-unknown")
            original.reload(wait_until="networkidle")
            click(original, "[data-screen=ask]")
            click(original, "[data-action=reopen-task]")
            original.locator(".task-review-history > summary").wait_for()
            click(original, ".task-review-history > summary")
            assert original.locator("[data-task-review-history-item]").count() >= 2
            snap(original, "03-original-history-after-reload")

            page = page_for("positive", "http://127.0.0.1:8792/")
            fill(page, "#workspace-name", "Explicit source proof demo")
            click(page, "#workspace-create button[type=submit]")
            page.locator("#source-create").wait_for()
            workspace = page.locator("#workspace-picker").input_value()
            source_a = "[2026-10-10T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4 的签名接口联调，计划在2026年10月14日前完成。"
            fill(page, "#source-name", "A.md")
            fill(page, "#source-content", source_a)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/sources/import")):
                click(page, "#source-create button[type=submit]")
            page.locator("#source-update").wait_for()
            source_id = page.locator("#source-update").get_attribute("data-id")
            click(page, '[data-action="back-sources"]')
            source_b = ("[2026-10-15T09:00:00+08:00] 陈晨: 我负责星河项目任务 API-4；本条就是" + source_id
                + "的同一任务，现修订其计划，将原定2026年10月14日前完成改为2026年10月18日前完成，本计划替代原计划。")
            fill(page, "#source-name", "B.md")
            fill(page, "#source-content", source_b)
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/sources/import")):
                click(page, "#source-create button[type=submit]")
            page.locator("#source-update").wait_for()
            click(page, '[data-action="back-sources"]')
            page.locator('[data-action="inspect-source"]').nth(1).wait_for()
            (out / "source-inputs.json").write_text(json.dumps(dict(workspace_id=workspace, source_a_id=source_id,
                A=source_a, B=source_b), ensure_ascii=False, indent=2), encoding="utf8")
            snap(page, "04-positive-imported-via-ui")
            question = "调查星河项目 API-4 的任务延续、计划修订和截止日期变化。"
            run1 = ask(page, question)
            snap(page, "05-positive-before-proof-capture")
            assert not (data / "positive.sqlite.relations.sqlite").exists(), "No P4 seed is permitted"
            status, review, _ = post_form(page, "identity", "REVIEW", "已对照两项任务原文；本地声明不代表业务批准。")
            assert status == 200, review
            status, committed, continue_payload = post_form(page, "dates", "CONTINUE", "按既有来源合同独立核验身份、新旧方向及日期边界。")
            assert status == 200 and committed["status"] == "COMMITTED", committed
            assert len(committed["proof"]["assessments"]) == 2 and len(committed["proof"]["comparisons"]) == 1
            snap(page, "06-source-proof-committed")
            retry = api_check(page, "POST", "/api/product/task-review", continue_payload)
            assert retry["status"] == 200 and retry["body"]["idempotent"] is True, retry
            collision = api_check(page, "POST", "/api/product/task-review", {**continue_payload, "note": "Altered duplicate"})
            assert collision["status"] == 409, collision
            run2 = ask(page, question)
            assert run2 != run1
            page.locator(".task-answer").wait_for()
            assert "日历日差值: 4" in page.locator(".task-answer").inner_text()
            click(page, ".task-review-history > summary")
            assert page.locator("[data-task-review-history-item]").count() >= 2
            assert run1 in page.locator(".task-review-history").inner_text()
            snap(page, "07-new-investigation-dates-and-related-history")
            page.reload(wait_until="networkidle")
            click(page, "[data-screen=ask]")
            click(page, "[data-action=reopen-task]")
            page.locator('[data-task-review-step="dates"]').wait_for()
            click(page, ".task-review-history > summary")
            assert run1 in page.locator(".task-review-history").inner_text()
            snap(page, "08-positive-reload-retains-history")
            record("actions", operation="RESTART_OWNED_PRODUCT_HOST", database=str(data / "positive.sqlite"))
            servers[1].terminate()
            servers[1].wait(timeout=10)
            start("positive-cold", [str(Path(__file__).with_name("offline_source_host.py")), str(data / "positive.sqlite"), "8792"])
            for _attempt in range(80):
                try:
                    page.reload(wait_until="networkidle")
                    break
                except Exception:
                    sleep(.25)
            page.locator('[data-screen="ask"]').wait_for()
            click(page, "[data-screen=ask]")
            click(page, "[data-action=reopen-task]")
            page.locator('[data-task-review-step="dates"]').wait_for()
            click(page, ".task-review-history > summary")
            assert run1 in page.locator(".task-review-history").inner_text()
            assert "日历日差值: 4" in page.locator(".task-answer").inner_text()
            snap(page, "08b-cold-host-retains-source-proof-and-history")
            status, withdrawn, _ = post_form(page, "identity", "WITHDRAW", "撤回个人声明；不撤销独立来源证明。")
            assert status == 200, withdrawn
            assert "日历日差值: 4" in page.locator(".task-answer").inner_text()
            snap(page, "09-local-withdrawal-keeps-source-proof")

            # Scope switch is an actual Product action. The following fetch is
            # explicitly adversarial; it does not stand in for a user flow.
            click(page, "#new-workspace")
            fill(page, "#workspace-name", "Other synthetic scope")
            click(page, "#workspace-create button[type=submit]")
            page.locator("#source-create").wait_for()
            foreign = api_check(page, "GET", f"/api/product/task-review?workspace_id={workspace}&run_id={run2}")
            assert foreign["status"] == 409 and "source_snapshot_hash" not in foreign["body"], foreign
            snap(page, "10-other-workspace-no-task-data")
            page.locator("#workspace-picker").select_option(workspace)
            page.locator("#source-create").wait_for()
            click(page, "[data-screen=ask]")
            click(page, "[data-action=reopen-task]")
            page.locator('[data-task-review-step="identity"]').wait_for()
            fill(page, "#task-review-actor-identity", "local-demo-reviewer")
            fill(page, "#task-review-note-identity", source_a)
            second = page_for("source-change", "http://127.0.0.1:8792/")
            click(second, '[data-action="inspect-source"] >> nth=1')
            conflict_b = source_b + "本条不是" + source_id + "的API-4，是独立任务。"
            fill(second, "#update-content", conflict_b)
            with second.expect_response(lambda r: r.request.method == "PUT" and "/api/sources/" in r.url):
                click(second, "#source-update button[type=submit]")
            snap(second, "11-second-browser-updates-source")
            with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/api/product/task-review")) as stale:
                click(page, '[data-task-review-step="identity"] [data-task-review-action="REVIEW"]')
            assert stale.value.status == 409
            page.locator("[data-task-review-unavailable]").wait_for()
            assert source_a.split(": ", 1)[1] not in page.locator("body").inner_text()
            assert "日历日差值: 4" not in page.locator("body").inner_text()
            snap(page, "12-previous-positive-result-invalidated")
            # The UI tells the user to refresh the current source scope after
            # an external mutation; do that before submitting a new question.
            page.reload(wait_until="networkidle")
            ask(page, question)
            identity_text = page.locator('[data-task-review-step="identity"]').inner_text()
            assert "CONFLICT" in identity_text, identity_text
            assert ("本条不是" + source_id) in identity_text
            status, conflict, _ = post_form(page, "dates", "CONTINUE", "有明确反证；检查不得继续计算修订日期。")
            assert status == 409 and conflict["status"] == "BLOCKED", conflict
            assert "日历日差值: 4" not in page.locator(".task-answer").inner_text()
            snap(page, "13-conflict-support-and-counter-evidence")
            # Workspace removal is the Product's synthetic source-access
            # revocation surface. No physical file is deleted.
            click(second, '[data-action="delete-source"]')
            second.locator("#source-create").wait_for()
            snap(second, "14-source-removed-from-workspace")
            status, revoked, _ = post_form(page, "identity", "REVIEW", "已撤回来源访问，拒绝旧核验。")
            assert status == 409, revoked
            assert not page.locator('[data-task-review-step="identity"]').count()
            snap(page, "15-removed-source-clears-old-evidence")
            (out / "result.json").write_text(json.dumps(dict(status="PASS", workspace_id=workspace,
                first_run=run1, fresh_run=run2, committed=committed, original_blocked=blocked,
                cross_workspace=foreign, retry=retry, collision=collision), ensure_ascii=False, indent=2), encoding="utf8")
            print("BROWSER_ACCEPTANCE_PASS", flush=True)
    except Exception as error:
        record("actions", operation="ACCEPTANCE_FAILURE", error=str(error))
        (out / "failure.txt").write_text(str(error), encoding="utf8")
        for name, context in contexts:
            try:
                for index, failed_page in enumerate(context.pages):
                    snap(failed_page, f"failure-{name}-{index}")
            except Exception:
                pass
        raise
    finally:
        for name, context in contexts:
            try:
                context.tracing.stop(path=str(out / (name + "-trace.zip")))
                context.close()
            except Exception as error:
                record("actions", operation="CAPTURE_CLOSE_FAILURE", error=str(error))
        if "browser" in locals():
            browser.close()
        if "p" in locals():
            p.stop()
        for process in servers:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
        for log in logs:
            log.close()
        for stream in streams.values():
            stream.close()


if __name__ == "__main__":
    main()

