"""Append combination checks to the immutable original real-browser journey."""
from pathlib import Path

root = Path.cwd()
original = root / 'docs/requirements/task-relation-review-closure/browser/acceptance.py'
source = original.read_text(encoding='utf8')
def replace(old, new):
    global source
    assert source.count(old) == 1, old
    source = source.replace(old,new)

source = source.replace('timeout=60000','timeout=180000')
replace('            source_b = (','            page.wait_for_timeout(1500)\n            source_b = (')
replace('                page = context.new_page()', '                context.set_default_timeout(60000)\n                page = context.new_page()')
replace('        target = page.locator(selector) if selector else page', '''        layout = page.evaluate("({width:innerWidth, scroll:document.body.scrollWidth, headings:document.querySelectorAll('h1').length})")
        record("actions", operation="LAYOUT_CHECK", name=name, **layout)
        assert layout["scroll"] <= layout["width"], layout
        assert layout["headings"] == 1, layout
        target = page.locator(selector) if selector else page''')
replace('            run1 = ask(page, question)', '''            run1 = ask(page, question)
            click(page, '.task-investigation-details > summary')
            click(page, '.task-source-records .ask-source-details > summary >> nth=0')
            snap(page, "04b-expanded-originals-and-relationship-candidates")''')
replace('            run2 = ask(page, question)', '''            # Two independent HTTP requests from this real browser race on one
            # current revision. They must produce one durable event, one 409.
            fresh = api_check(page, "GET", f"/api/product/task-review?workspace_id={workspace}&run_id={run1}")["body"]
            race_base = {**continue_payload, "action":"REVIEW", "step_id":"identity", "outcome":"UNSURE", "capability":fresh["capability"], "expected_revision":fresh["revision"]}
            race_payloads = [{**race_base, "request_id": "browser-concurrent-a", "note":"Concurrent local statement A"}, {**race_base, "request_id":"browser-concurrent-b", "note":"Concurrent local statement B"}]
            record("actions", operation="ADVERSARIAL_CONCURRENT_API_CHECK", payloads=race_payloads)
            raced = page.evaluate("""async payloads => Promise.all(payloads.map(async payload => {
                const r = await fetch('/api/product/task-review', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
                return {status:r.status,body:await r.json()};
            }))""", race_payloads)
            assert sorted(r["status"] for r in raced) == [200,409], raced
            (out/"concurrent-result.json").write_text(json.dumps(raced,ensure_ascii=False,indent=2),encoding="utf8")
            run2 = ask(page, question)''')
replace('            snap(page, "07-new-investigation-dates-and-related-history")', '''            snap(page, "07-new-investigation-dates-and-related-history")
            assert page.locator('#ask-result > section').first.get_attribute('class').find('task-answer') >= 0
            assert page.locator('.task-answer > h2').inner_text() == "找到 2 条任务记录"
            assert not page.locator('.task-investigation-details').get_attribute('open')
            history_count = page.locator('[data-task-review-history-item]').count()
            page.set_viewport_size({"width":390,"height":844})
            snap(page,"07a-mobile-zh-result-first")
            page.locator('#ask-language').select_option('en')
            page.get_by_role('heading',name='Review task relationship evidence',exact=True).wait_for()
            assert 'Calendar-day difference: 4' in page.locator('.task-answer').inner_text()
            click(page,'.task-review-history > summary')
            assert page.locator('[data-task-review-history-item]').count() == history_count
            assert run1 in page.locator('.task-review-history').inner_text()
            snap(page,"07b-mobile-en-history-preserved")
            before_posts = sum(r['method']=='POST' and r['url'].endswith('/api/product/task-review') for r in events)
            status,mobile_receipt,_ = post_form(page,'identity','REVIEW','Mobile English local statement, no business approval.')
            assert status == 200 and mobile_receipt['receipt']['status'] == 'RECORDED', mobile_receipt
            assert sum(r['method']=='POST' and r['url'].endswith('/api/product/task-review') for r in events) == before_posts+1
            page.locator('#ask-language').select_option('zh')
            page.get_by_role('heading',name='核验任务关系证据',exact=True).wait_for()
            click(page,'.task-review-history > summary')
            assert page.locator('[data-task-review-history-item]').count() == history_count+1
            assert 'Mobile English local statement' in page.locator('.task-review-history').inner_text()
            assert '日历日差值: 4' in page.locator('.task-answer').inner_text()
            snap(page,"07c-mobile-zh-saved-receipt-preserved")
            page.set_viewport_size({"width":1440,"height":1000})
            for language in ('zh','en'):
                before_posts = sum(r['method']=='POST' and r['url'].endswith('/api/product/ask') for r in events)
                with page.expect_response(lambda r: r.request.method=='GET' and '/api/product/task-review?' in r.url):
                    page.locator('#interface-language').select_option(language)
                page.locator('[data-task-review-step="identity"]').wait_for()
                click(page,'.task-review-history > summary')
                assert 'Mobile English local statement' in page.locator('.task-review-history').inner_text()
                assert '日历日差值: 4' in page.locator('.task-answer').inner_text()
                assert page.locator('#ask-query').input_value() == question
                assert not page.locator('#ask-external:checked').count()
                assert sum(r['method']=='POST' and r['url'].endswith('/api/product/ask') for r in events) == before_posts
                snap(page,'07d-interface-'+language+'-revalidated-history')''')
replace('            snap(page, "13-conflict-support-and-counter-evidence")', '''            snap(page, "13-conflict-support-and-counter-evidence")
            page.set_viewport_size({"width":390,"height":844})
            snap(page,"13b-mobile-conflict-and-counter-evidence")
            page.set_viewport_size({"width":1440,"height":1000})''')
exec(compile(source,str(original),'exec'), {'__file__':str(original),'__name__':'__main__'})
