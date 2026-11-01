# 本数据为页面流程夹具，句子经改写以适配旧抽取器，不代表抽取效果。
"""Fresh, public A/B meeting fixture for the formal-task browser smoke test.

The disposable database is populated through the real offline ingestion helpers;
all model/extractor entry points are forbidden once the HTTP server starts.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import Mock, patch

from linkloom.agents.providers.deepseek_api import DeepSeekProviderAdapter
from linkloom.agents.providers.gemini_api import GeminiProviderAdapter
from linkloom.semantic_ingestion.extraction import ProviderNeutralSemanticExtractor
from linkloom.semantic_ingestion.meeting_tasks_v2 import MeetingTaskExtractor
from linkloom.ui.product import ProductApplication
from linkloom.ui.server import create_http_server
from tests.unit.test_task_legacy_adapter import RecordedProvider, close, seed
from tests.unit.test_task_rebind import revise


A_TEXT = "由李明负责联调报告，2026年10月12日前完成。"
B_TEXT = "由王芳负责联调报告，2026年10月16日前完成。"
A_CONTENT = "[2026-10-08T10:00:00+08:00] 主持人: " + A_TEXT
B_CONTENT = "[2026-10-10T10:00:00+08:00] 评审人: " + B_TEXT


class BrowserBackend:
    def context(self):
        return {"schema_version": "offline-formal-task-browser"}

    def start(self, query):
        raise AssertionError("the task page must not start Ask")

    def inspect(self, run_id):
        raise AssertionError("the task page must not inspect Ask")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    imported = seed(args.root / "tasks.sqlite", content=A_CONTENT, text=A_TEXT)
    _, candidate_b = revise(imported, content=B_CONTENT, text=B_TEXT,
        parts=("联调报告",), source_id="meeting-b.txt")
    # Names belong to this fresh synthetic catalogue, not to source evidence text.
    with imported.repository._connection:
        imported.repository._connection.execute(
            "UPDATE ll_source SET name=? WHERE workspace_id=? AND source_id=?",
            ("10/10 评审", imported.workspace, "meeting-b.txt"))
        imported.repository._connection.execute(
            "UPDATE ll_source_version SET source_name=? WHERE workspace_id=? AND source_id=?",
            ("10/10 评审", imported.workspace, "meeting-b.txt"))
    product = ProductApplication(database_path=imported.path, extractor=None)
    models = []
    try:
        with ExitStack() as stack:
            for kind, method in ((GeminiProviderAdapter, "complete"),
                (DeepSeekProviderAdapter, "complete"), (RecordedProvider, "complete"),
                (ProviderNeutralSemanticExtractor, "extract"), (MeetingTaskExtractor, "extract")):
                spy = Mock(side_effect=AssertionError("browser request called a model/extractor"))
                stack.enter_context(patch.object(kind, method, spy))
                models.append(spy)
            host = create_http_server(BrowserBackend(), product_app=product, port=args.port)
            info = {"url": f"http://127.0.0.1:{host.server_port}",
                "workspace_id": imported.workspace,
                "candidate_a": imported.observation.observation_id,
                "candidate_b": candidate_b.task_observation.observation_id,
                "quote_a": A_TEXT, "quote_b": B_TEXT}
            print(json.dumps(info, ensure_ascii=False), flush=True)
            try:
                host.serve_forever(poll_interval=0.05)
            except KeyboardInterrupt:
                pass
            finally:
                host.server_close()
            assert all(model.call_count == 0 for model in models)
    finally:
        product.close()
        close(imported)


if __name__ == "__main__":
    main()
