"""Start LinkLoom's local product application."""

from __future__ import annotations

import argparse
import math
import json
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
    parser = argparse.ArgumentParser(prog="python -m linkloom.ui")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost"])
    parser.add_argument("--port", type=int, default=8765)
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
    parser.add_argument('--allow-canary-live', action='store_true',
        help='Enable only the frozen public M3 Canary with a separately approved local grant (default off).')
    parser.add_argument('--canary-live-grant', type=Path,
        help='Host-local approval JSON; never accepted from HTTP. Does not itself grant permission.')
    parser.add_argument(
        "--allow-temporal-agent",
        action="store_true",
        help="Explicitly enable bounded read-only temporal Agent runs using the configured Gemini model.",
    )
    parser.add_argument(
        "--adaptive-planning", action="store_true",
        help="Opt in to bounded model planning for unfamiliar source-relation questions (default off).",
    )
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
    if bool(args.canary_live_grant) != args.allow_canary_live:
        parser.error('Canary LIVE requires both --allow-canary-live and --canary-live-grant')
    if args.allow_canary_live and (args.allow_external_provider or args.allow_temporal_agent or args.adaptive_planning):
        parser.error('Canary LIVE cannot be combined with ordinary Provider/Agent flags')
    if args.allow_canary_live and args.database != parser.get_default('database'):
        parser.error('Canary LIVE uses only its fixed synthetic catalog; --database is not accepted')
    if not math.isfinite(args.estimated_cost_per_request) or args.estimated_cost_per_request < 0:
        parser.error("--estimated-cost-per-request must be a finite non-negative number")
    if args.model != GEMINI_38_FLASH_MODEL_ID:
        parser.error(
            f"Gemini product runs are pinned to {GEMINI_38_FLASH_MODEL_ID}; no model fallback is allowed."
        )
    if args.allow_temporal_agent and not args.allow_external_provider:
        parser.error("--allow-temporal-agent requires --allow-external-provider")
    if args.adaptive_planning and not args.allow_temporal_agent:
        parser.error("--adaptive-planning requires --allow-temporal-agent")

    extractor = None
    sdk_client = None
    canary_host = None
    if args.allow_canary_live:
        from linkloom.ui.canary import CanaryHost
        try:
            grant = json.loads(args.canary_live_grant.read_text(encoding='utf8'))
            canary_host = CanaryHost.open_live(enabled=True, grant=grant)
            sdk_client = canary_host._sdk
        except (OSError, ValueError, ImportError, TypeError) as error:
            parser.error('Canary admission failed: ' + str(error))
    if args.allow_external_provider:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            parser.error("--allow-external-provider requires GEMINI_API_KEY in the environment")
        try:
            sdk_client = build_gemini_sdk_client(api_key, http_options_kwargs={
                "timeout": 130000, "retry_options": {"attempts": 1},
            })
            provider = GeminiProviderAdapter(
                GeminiSDKClientBridge(sdk_client),
                model_id=args.model,
            )
        except (ImportError, TypeError, ValueError, ValidationError):
            parser.error(
                "Gemini provider could not be initialized. Install the configured provider dependency and check GEMINI_API_KEY."
            )

        guard_id = "linkloom-local-ui-explicit-provider-opt-in/v1"

        def authorize_request(request):
            return ProviderRequestAuthorization.approve(
                request,
                guard_id=guard_id,
                reservation_id=f"local-provider-request-{uuid4().hex}",
                estimated_cost_upper_bound_usd=args.estimated_cost_per_request,
            )

        extractor = ProviderNeutralSemanticExtractor(
            provider,
            provider_id="gemini",
            model_id=args.model,
            request_guard=authorize_request,
            request_guard_id=guard_id,
            max_attempts=1,
        )

    product_app = (canary_host.product_application() if canary_host else
                   ProductApplication(database_path=args.database, extractor=extractor))
    backend = DemoRunBackend(DemoCase.load_mps_001())
    investigation_service = (ProductInvestigationService(product_app, canary_host=canary_host,
        run_mode='LIVE_CANARY_EXPLICIT', relation_max_steps=6) if canary_host else (
        ProductInvestigationService(
            product_app,
            model_factory=lambda _query: GeminiProviderAdapter(
                GeminiSDKClientBridge(sdk_client), model_id=args.model,
            ),
            run_mode="GEMINI_38_FLASH_EXPLICIT",
            max_steps=8,
            adaptive_planning=args.adaptive_planning,
        )
        if args.allow_temporal_agent else None
    ))
    server = create_http_server(
        backend, product_app=product_app, investigation_service=investigation_service,
        host=args.host, port=args.port,
    )
    print(f"LinkLoom Product UI: http://{args.host}:{server.server_port}")
    print(f"Local product database: {product_app.repository.database_path}")
    if canary_host:
        print('Public synthetic Canary only; application reservation <= $3, not a cloud billing hard limit.')
    elif extractor is None:
        print("Semantic extraction is disabled. Configure a provider and explicitly opt in before sending source text.")
    else:
        print(f"Semantic extraction provider enabled: Gemini model {args.model}")
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
