"""Start LinkLoom's local product application."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
from uuid import uuid4

from linkloom.agents.providers import (
    GEMINI_38_FLASH_MODEL_ID,
    GeminiProviderAdapter,
    build_gemini_sdk_client,
)
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
)
from linkloom.runtime.errors import ValidationError
from linkloom.ui.configuration import GEMINI_CONFIGURATION_HELP, OFFLINE_HELP
from linkloom.ui.demo import DemoCase, DemoRunBackend
from linkloom.ui.product import ProductApplication
from linkloom.ui.investigation import ProductInvestigationService
from linkloom.ui.server import create_http_server


class GeminiSDKClientBridge:
    """Expose the official SDK's model API through the provider client contract."""

    def __init__(self, sdk_client) -> None:
        model_api = getattr(sdk_client, "models", None)
        if not callable(getattr(model_api, "generate_content", None)):
            raise TypeError("Gemini SDK client must expose models.generate_content().")
        self._sdk_client = sdk_client

    def generate_content(self, *, model, contents, config):
        return self._sdk_client.models.generate_content(
            model=model,
            contents=contents,
            config=config,
        )

    def __getattr__(self, name):
        return getattr(self._sdk_client, name)


def main() -> int:
    from dotenv import load_dotenv

    # Configuration belongs to this entry point, never to module imports or other CLIs.
    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False, verbose=False)
    parser = argparse.ArgumentParser(prog="python -m linkloom.ui")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--offline-task-demo",
        action="store_true",
        help="Start the normal UI with public synthetic task sources and deterministic offline stages.",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get(
            "LINKLOOM_DATABASE_PATH", str(Path.home() / ".linkloom" / "product.sqlite")
        ),
        help="Local SQLite product store (or set LINKLOOM_DATABASE_PATH).",
    )
    parser.add_argument(
        "--allow-external-provider",
        action="store_true",
        help="Explicitly allow source text to be sent to the configured Gemini provider.",
    )
    parser.add_argument(
        "--allow-temporal-agent",
        action="store_true",
        help="Explicitly enable bounded read-only temporal Agent runs using the configured Gemini model.",
    )
    parser.add_argument(
        "--allow-local-task-review",
        action="store_true",
        help="Explicitly enable localhost self-asserted task review records (no identity or business approval).",
    )
    parser.add_argument('--allow-task-investigation', action='store_true',
        help='Configure task Provider stages. Each Ask still needs source-snapshot permission.')
    for name in ('requests', 'input-tokens', 'output-tokens'):
        parser.add_argument('--task-max-' + name, type=int)
    for name in ('cost-usd', 'seconds'):
        parser.add_argument('--task-max-' + name, type=float)
    parser.add_argument('--task-request-timeout-seconds', type=float)
    parser.add_argument(
        "--model",
        default=os.environ.get("LINKLOOM_SEMANTIC_MODEL", GEMINI_38_FLASH_MODEL_ID),
        help="Gemini model for explicitly enabled semantic extraction and temporal Agent runs.",
    )
    parser.add_argument(
        "--estimated-cost-per-request",
        type=float,
        default=0.02,
        help="Audited upper-bound estimate recorded for each provider request.",
    )
    args = parser.parse_args()
    task_limits = None
    if args.allow_task_investigation:
        from linkloom.ui.task_provider import TaskProviderLimits
        if not args.allow_external_provider:
            parser.error('--allow-task-investigation requires --allow-external-provider')
        try:
            task_limits = TaskProviderLimits(args.task_max_requests, args.task_max_input_tokens,
                args.task_max_output_tokens, args.task_max_cost_usd, args.task_max_seconds,
                args.task_request_timeout_seconds, args.estimated_cost_per_request)
        except ValueError as exc:
            parser.error('Task Provider requires all explicit --task-* budgets: ' + str(exc))
    if not math.isfinite(args.estimated_cost_per_request) or args.estimated_cost_per_request < 0:
        parser.error("--estimated-cost-per-request must be a finite non-negative number")
    if args.offline_task_demo:
        if args.allow_external_provider or args.allow_temporal_agent or args.allow_task_investigation:
            parser.error("--offline-task-demo cannot be combined with external or temporal Agent flags")
    else:
        if args.model != GEMINI_38_FLASH_MODEL_ID:
            parser.error(
                f"Gemini product runs are pinned to {GEMINI_38_FLASH_MODEL_ID}; no model fallback is allowed."
            )
        if args.allow_temporal_agent and not args.allow_external_provider:
            parser.error("--allow-temporal-agent requires --allow-external-provider")

    extractor = None
    sdk_client = None
    if args.offline_task_demo:
        from linkloom.ui.offline_task_demo import build_offline_task_demo
        default_database = str(Path.home() / ".linkloom" / "product.sqlite")
        demo_database = Path(args.database)
        if not os.environ.get("LINKLOOM_DATABASE_PATH") and args.database == default_database:
            demo_database = Path.cwd() / "work" / "offline-task-demo.sqlite"
        try:
            product_app, investigation_service = build_offline_task_demo(demo_database)
        except ValueError as exc:
            parser.error(f"--offline-task-demo requires a dedicated synthetic database: {exc}")
    else:
        if args.allow_external_provider:
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key or not api_key.strip():
                parser.error(
                    "--allow-external-provider requires GEMINI_API_KEY in the environment. "
                    + GEMINI_CONFIGURATION_HELP + " " + OFFLINE_HELP
                )
            try:
                sdk_client = build_gemini_sdk_client(api_key, http_options_kwargs=(dict(
                    timeout=round(task_limits.request_timeout_seconds * 1000),
                    retry_options=dict(attempts=1)) if task_limits else None))
                provider = GeminiProviderAdapter(
                    GeminiSDKClientBridge(sdk_client),
                    model_id=args.model,
                )
            except (ImportError, TypeError, ValueError, ValidationError):
                parser.error(
                    'Gemini provider could not be initialized. Install the provider dependency with '
                    'python -m pip install -e ".[smoke]" and check GEMINI_API_KEY. '
                    + GEMINI_CONFIGURATION_HELP + " " + OFFLINE_HELP
                )

            guard_id = "linkloom-local-ui-explicit-provider-opt-in/v1"

            def authorize_request(request):
                return ProviderRequestAuthorization.approve(
                    request,
                    guard_id=guard_id,
                    reservation_id=f"local-provider-request-{uuid4().hex}",
                    estimated_cost_upper_bound_usd=args.estimated_cost_per_request,
                )

            # Task configuration is not permission to send imports. Its source
            # disclosure happens only in Ask for the exact selected snapshot.
            extractor = None if task_limits else ProviderNeutralSemanticExtractor(
                provider,
                provider_id="gemini",
                model_id=args.model,
                request_guard=authorize_request,
                request_guard_id=guard_id,
                max_attempts=1,
            )

        product_app = ProductApplication(database_path=args.database, extractor=extractor)
        if task_limits:
            from linkloom.ui.task_provider import compose_task_provider
            investigation_service = compose_task_provider(product_app, provider, task_limits,
                                                         enable_temporal=args.allow_temporal_agent)
        elif args.allow_temporal_agent:
            investigation_service = ProductInvestigationService(
                product_app,
                model_factory=lambda _query: GeminiProviderAdapter(
                    GeminiSDKClientBridge(sdk_client), model_id=args.model,
                ),
                run_mode="GEMINI_38_FLASH_EXPLICIT",
                max_steps=8,
            )
        else:
            # Keep saved task results readable in an ordinary no-provider
            # process.  This service has no model factory, so config remains
            # unavailable for new runs while read_saved_task can revalidate
            # existing artifacts without resuming Runtime or calling a model.
            investigation_service = ProductInvestigationService(
                product_app,
                model_factory=None,
                run_mode="MODEL_NOT_CONFIGURED",
                external_provider=False,
            )
    backend = DemoRunBackend(DemoCase.load_mps_001())
    server = create_http_server(
        backend, product_app=product_app, investigation_service=investigation_service,
        allow_local_task_review=args.allow_local_task_review,
        host=args.host, port=args.port,
    )
    print(f"LinkLoom Product UI: http://{args.host}:{server.server_port}")
    database_display = demo_database if args.offline_task_demo else Path(args.database).expanduser()
    print(f"Local product database: {database_display}")
    if args.offline_task_demo:
        print("Offline synthetic task demo enabled: deterministic FakeModel runtime and FAKE stages; no external provider or business approval.")
    elif task_limits:
        print(f'Task Provider configured: {args.model}; sources require per-question Ask authorization; no business approval.')
    elif extractor is None:
        print("Semantic extraction is disabled. " + GEMINI_CONFIGURATION_HELP + " " + OFFLINE_HELP)
    else:
        print(f"Semantic extraction provider enabled: Gemini model {args.model}")
    if args.allow_local_task_review:
        print("Local task review enabled: localhost self-asserted records only; actor labels are not authenticated and business approval remains false.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        product_app.close()
        if sdk_client is not None:
            sdk_client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
