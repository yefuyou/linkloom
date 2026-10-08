"""Start LinkLoom's local product application."""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
from uuid import uuid4

from linkloom.agents.providers import GeminiProviderAdapter, build_gemini_sdk_client
from linkloom.semantic_ingestion.extraction import (
    ProviderNeutralSemanticExtractor,
    ProviderRequestAuthorization,
)
from linkloom.runtime.errors import ValidationError
from linkloom.ui.demo import DemoCase, DemoRunBackend
from linkloom.ui.product import ProductApplication
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
    parser.add_argument(
        "--model",
        default=os.environ.get("LINKLOOM_SEMANTIC_MODEL", "gemini-3.8-flash"),
        help="Semantic extraction model used only with --allow-external-provider.",
    )
    parser.add_argument(
        "--estimated-cost-per-request",
        type=float,
        default=0.02,
        help="Audited upper-bound estimate recorded for each provider request.",
    )
    args = parser.parse_args()
    if not math.isfinite(args.estimated_cost_per_request) or args.estimated_cost_per_request < 0:
        parser.error("--estimated-cost-per-request must be a finite non-negative number")

    extractor = None
    sdk_client = None
    if args.allow_external_provider:
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            parser.error("--allow-external-provider requires GEMINI_API_KEY in the environment")
        try:
            sdk_client = build_gemini_sdk_client(api_key)
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

    product_app = ProductApplication(database_path=args.database, extractor=extractor)
    backend = DemoRunBackend(DemoCase.load_mps_001())
    server = create_http_server(backend, product_app=product_app, host=args.host, port=args.port)
    print(f"LinkLoom Product UI: http://{args.host}:{server.server_port}")
    print(f"Local product database: {Path(args.database).expanduser()}")
    if extractor is None:
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
