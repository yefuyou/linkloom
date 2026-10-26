"""Run the original opt-out browser contract on a fresh isolated port/data path."""
from pathlib import Path
root=Path.cwd()
original=root/'docs/requirements/task-relation-review-closure/browser/default_host.py'
source=original.read_text(encoding='utf8').replace('8791','8793')
source=source.replace('root / "output/playwright/task-relation-review" / sys.argv[1] / "default-host"', 'root / "output/playwright/task-relation-review" / sys.argv[1]')
source=source.replace('root / "work/task-relation-review" / sys.argv[1] / "original.sqlite"','root / "work/task-relation-review" / sys.argv[1] / "host.sqlite"')
marker='            page.screenshot(path=str(out / "disabled-host-retains-answer.png"), full_page=True)'
assert source.count(marker)==1
source=source.replace(marker,'''            disabled_write = page.evaluate("""async () => {
                const r = await fetch('/api/product/task-review', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'REVIEW'})});
                return {status:r.status,body:await r.json()};
            }""")
            assert disabled_write['status']==403 and disabled_write['body']['error']['code']=='LOCAL_TASK_REVIEW_DISABLED', disabled_write
            (out/'disabled-write.json').write_text(json.dumps(disabled_write,indent=2),encoding='utf8')
'''+marker)
exec(compile(source,str(original),'exec'),{'__file__':str(original),'__name__':'__main__'})
