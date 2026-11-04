"""Real local retrieval/reranking over disposable synthetic meetings; never paid APIs."""
from contextlib import ExitStack
import argparse
import json
from pathlib import Path
from unittest.mock import patch

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.ui.product import ProductApplication
from linkloom.ui.server import create_http_server
from tests.task_mvp_browser_server import BrowserBackend
from tests.unit.test_task_legacy_adapter import close, seed, RecordedProvider
from tests.unit.test_task_rebind import revise
from tests.unit.test_same_task_hints import confirm, incoming


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args()
    args.root.mkdir(parents=True,exist_ok=False)
    imported=seed(args.root/'tasks.sqlite')
    product=None
    host=None
    try:
        report=confirm(imported,title='联调报告')['task']
        text='由李明负责联调周报，2026年10月12日前完成。'
        _,weekly=revise(imported,content='[2026-10-08T10:00:00+08:00] 主持人: '+text,
            text=text,parts=('联调周报',),source_id='weekly-report.txt',owner='李明',deadline='2026年10月12日')
        other=confirm(imported,weekly.task_observation.observation_id,title='联调周报')['task']
        identifier,fingerprint=incoming(imported)
        product=ProductApplication(database_path=imported.path,extractor=None)
        host=create_http_server(BrowserBackend(),product_app=product,port=0)
        # Normal Product startup owns background preloading; no scripted scores.
        with ExitStack() as stack:
            for kind in (GeminiProviderAdapter,DeepSeekProviderAdapter,RecordedProvider):
                stack.enter_context(patch.object(kind,'complete',side_effect=AssertionError('paid/extraction call forbidden')))
            print(json.dumps(dict(url=f'http://127.0.0.1:{host.server_port}',workspace_id=imported.workspace,
                candidate_id=identifier,candidate_fingerprint=fingerprint,report_id=report['id'],weekly_id=other['id']),
                ensure_ascii=False),flush=True)
            host.serve_forever(poll_interval=.05)
    finally:
        if host is not None: host.server_close()
        if product is not None: product.close()
        close(imported)


if __name__=='__main__':
    main()
