"""Local Chromium acceptance host; public synthetic fixtures, no provider calls.

Run with PYTHONPATH=src, then open the printed loopback URL in Chromium.
Each invocation requires a fresh --root so existing user data is never opened.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from linkloom.agents.model_adapter import ModelAction, ModelResponse
from linkloom.tools.contracts import ToolCall
from linkloom.ui import server
from tests.integration.test_product_application_flow import _UnusedRunBackend
from tests.m3p_support import product_case, revision_product


class ConflictSemantics:
    """Explicit fake model output, never a Product verdict override."""
    def __init__(self):
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        data = json.loads(request.user_input.split('AUTHORIZED_SNAPSHOT_JSON\n', 1)[1])
        evidence = [dict(span_id=e['span_id'], quote=e['source']['quote']) for e in data['allowed_evidence']]
        arguments = {'proposals': [dict(predicate=q['predicate'], subject=q['subject'], object=q['object'],
            proposed_verdict='CONFLICT', supporting_evidence=evidence[:2], counter_evidence=evidence[2:],
            missing_evidence=['日期变动与未批准调整的原文需要独立复核'],
            reason_codes=['PUBLIC_SCRIPTED_CONFLICT'], changed_fields=[]) for q in data['queries']]}
        return ModelResponse(action=ModelAction.tool(ToolCall('public-conflict',
            request.available_tools[0].tool_id, arguments)))


def conflict_product(root):
    app, service, ask, stages, workspace = product_case(root, conflict=True)
    semantics = ConflictSemantics()
    service.task_models = replace(service.task_models, semantics=semantics)
    return app, service, ask, [*stages[:2], semantics], workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=['partial', 'conflict', 'model-conflict', 'unknown', 'p4'], default='partial')
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--static-root', type=Path)
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=False)
    if args.static_root:
        server.STATIC_ROOT = args.static_root.resolve()
    if args.case == 'p4':
        app, service, *_ = revision_product(args.root)
    elif args.case == 'model-conflict':
        app, service, *_ = conflict_product(args.root)
    else:
        app, service, *_ = product_case(args.root, conflict=args.case == 'conflict', missing=args.case == 'unknown')
    host = server.create_http_server(_UnusedRunBackend(), product_app=app,
        investigation_service=service, port=args.port)
    print(f'Public synthetic {args.case}: http://127.0.0.1:{host.server_port}', flush=True)
    try:
        host.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.server_close()
        app.close()


if __name__ == '__main__':
    main()
