"""V3's permissioned, offline-testable wrapper around the adaptive provider."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import importlib.metadata
import os
from pathlib import Path
from typing import Any, Callable

from linkloom.agents.providers.gemini_api import build_gemini_sdk_client
from scripts.adaptive_provider import (
    AdaptiveProvider,
    ProviderStopped,
    StrictProviderOutcomeUnknown,
)


MODEL = "gemini-3.8-flash"
V3_MAX_REQUESTS, V3_MAX_USD, V3_MAX_SECONDS = 80, 4.0, 7200
OFFICIAL_ENDPOINT = "generativelanguage.googleapis.com"
DEFAULT_LEDGER = Path(__file__).resolve().parents[1] / "work/v3-live/provider.jsonl"
EXPERIMENT_ID = "linkloom-contract-v3-20261016"
STRICT_STOP_REASON = "TWO_STRICT_UNCERTAIN_STOP"


class ProviderPermissionRequired(RuntimeError):
    code = "PROVIDER_PERMISSION_REQUIRED"


def _model_version(sdk: Any, supplied: str | None) -> str:
    if supplied is not None:
        return supplied if isinstance(supplied, str) and supplied else "UNKNOWN"
    for owner in (sdk, getattr(sdk, "models", None)):
        for name in ("actual_model_version", "model_version", "model"):
            value = getattr(owner, name, None)
            if isinstance(value, str) and value:
                return value
    return "UNKNOWN"


class ContractV3Provider:
    """Thin policy wrapper; accounting, task leases, and payload checks reuse AdaptiveProvider."""

    model = MODEL

    def __init__(self, sdk: Any, ledger_path: str | os.PathLike[str] = DEFAULT_LEDGER,
                 *, experiment_id: str = EXPERIMENT_ID, provider_permission: bool | None = None,
                 permission: bool | None = None, actual_model_version: str | None = None) -> None:
        if permission is not None:
            provider_permission = permission
        if provider_permission is not True:
            raise ProviderPermissionRequired("PROVIDER_PERMISSION_REQUIRED")
        self.sdk = sdk
        self._inner = AdaptiveProvider(
            sdk, ledger_path, experiment_id=experiment_id, max_requests=V3_MAX_REQUESTS,
            max_usd=V3_MAX_USD, max_seconds=V3_MAX_SECONDS,
        )
        self.provider = self._inner
        self.models = self
        self.provider_permission = True
        self._stopped = False
        self._stop_reason: str | None = None
        self._actual_model_version = _model_version(sdk, actual_model_version)
        self._record_preflight()

    def _record_preflight(self) -> None:
        if any(row.get("event") == "contract_v3_preflight" for row in self._inner._records):
            return
        try:
            sdk_version = importlib.metadata.version("google-genai")
        except importlib.metadata.PackageNotFoundError:
            sdk_version = "UNKNOWN"
        self._inner._append(
            "contract_v3_preflight", expected_model=MODEL,
            actual_model_version=self._actual_model_version, actual_model_version_status=(
                "matched" if self._actual_model_version == MODEL else "unknown_or_unconfirmed"
            ), sdk_version=sdk_version, official_endpoint=OFFICIAL_ENDPOINT,
            sdk_retry_attempts=1, serial=True, provider_permission=True,
            max_requests=V3_MAX_REQUESTS, max_usd=V3_MAX_USD, max_seconds=V3_MAX_SECONDS,
        )

    def _persist_cumulative_stop(self) -> None:
        self._inner._rebuild()
        if not getattr(self._inner, "_ledger_stop", None):
            self._inner._append(
                "experiment_stopped", reason=STRICT_STOP_REASON,
                strict_uncertain_count=self._inner._strict_count,
            )
            self._inner._rebuild()
        self._inner._stopped = True
        self._inner._stop_reason = getattr(self._inner, "_ledger_stop", None) or STRICT_STOP_REASON
        self._stopped, self._stop_reason = True, self._inner._stop_reason

    def _guard(self) -> None:
        summary = self._inner.summary()
        if summary["strict_uncertain_count"] >= 2:
            self._persist_cumulative_stop()
        if self._stopped or summary["stopped"] or self._inner._stopped:
            reason = self._stop_reason or self._inner._stop_reason or STRICT_STOP_REASON
            self._stopped, self._stop_reason = True, reason
            raise ProviderStopped(reason)

    @contextmanager
    def task(self, case_id: str, trial: int, phase: str) -> Iterator["ContractV3Provider"]:
        self._guard()
        with self._inner.task(case_id, trial, phase):
            yield self

    def generate_content(self, model: str, contents: Any, config: Any) -> Any:
        self._guard()
        try:
            response = self._inner.generate_content(model, contents, config)
            version = (response.get("model_version") if isinstance(response, dict)
                       else getattr(response, "model_version", None))
            if isinstance(version, str) and version:
                self._actual_model_version = version
            return response
        except StrictProviderOutcomeUnknown:
            if self._inner.summary()["strict_uncertain_count"] >= 2:
                self._persist_cumulative_stop()
            raise

    def summary(self) -> dict[str, Any]:
        summary = self._inner.summary()
        known_usd = unknown_usd = 0.0
        for request_id, reserved in self._inner._reservations.items():
            finished = self._inner._finishes.get(request_id)
            if finished and finished.get("usage_status") == "known" and finished.get("actual_cost_usd") is not None:
                known_usd += float(finished["actual_cost_usd"])
            else:
                unknown_usd += float(reserved["reservation_usd"])
        summary.update(
            v3=True, serial=True, provider_permission=True, max_seconds=V3_MAX_SECONDS,
            actual_model_version=self._actual_model_version,
            sdk_retry_attempts=1, official_endpoint=OFFICIAL_ENDPOINT,
            usage_unknown_count=summary["request_count"] - summary["usage_known_count"],
            known_usage_usd=round(known_usd, 12),
            unknown_usage_reserved_usd=round(unknown_usd, 12),
        )
        if self._stopped:
            summary.update(stopped=True, stop_reason=self._stop_reason, status="STOPPED")
        return summary

    def close(self) -> Any:
        close = getattr(self.sdk, "close", None)
        return close() if callable(close) else None


V3Provider = ContractV3Provider


def connect(*, provider_permission: bool | None = None, permission: bool | None = None,
            sdk: Any | None = None, sdk_factory: Callable[..., Any] | None = None,
            ledger_path: str | os.PathLike[str] = DEFAULT_LEDGER,
            experiment_id: str = EXPERIMENT_ID) -> tuple[Any, ContractV3Provider, dict[str, Any]]:
    if permission is not None:
        provider_permission = permission
    if provider_permission is not True:
        raise ProviderPermissionRequired("PROVIDER_PERMISSION_REQUIRED")
    identity = {"endpoint": OFFICIAL_ENDPOINT, "requests": 0}

    def check_endpoint(request: Any) -> None:
        host = getattr(getattr(request, "url", None), "host", None)
        if host != OFFICIAL_ENDPOINT:
            raise RuntimeError("SDK_ENDPOINT_IDENTITY_MISMATCH")
        identity["requests"] += 1

    if sdk is None:
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_CREDENTIAL_UNAVAILABLE")
        from google.genai import types
        builder = sdk_factory or build_gemini_sdk_client
        sdk = builder(key, http_options_kwargs={
            "base_url": "https://" + OFFICIAL_ENDPOINT, "timeout": 130000,
            "retry_options": types.HttpRetryOptions(attempts=1),
            "client_args": {"http2": False, "event_hooks": {"request": [check_endpoint]}},
        })
    return sdk, ContractV3Provider(
        sdk, ledger_path, experiment_id=experiment_id, provider_permission=True,
    ), identity


def call_provider(provider: ContractV3Provider, *, case_id: str, trial: int = 1,
                  phase: str = "A") -> Any:
    with provider.task(case_id, trial, phase):
        return provider.generate_content(
            MODEL, [{"role": "user", "parts": [{"text": "synthetic"}]}], {},
        )


__all__ = [
    "ContractV3Provider", "DEFAULT_LEDGER", "MODEL", "OFFICIAL_ENDPOINT",
    "ProviderPermissionRequired", "V3Provider", "V3_MAX_REQUESTS", "V3_MAX_SECONDS",
    "V3_MAX_USD", "call_provider", "connect",
]
