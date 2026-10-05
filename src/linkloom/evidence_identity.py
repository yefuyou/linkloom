"""Immutable, workspace-scoped identities for indexed and quoted evidence."""

from __future__ import annotations

import hashlib
import json


def document_identity_id(
    *,
    workspace_id: str,
    resource_id: str,
    document_id: str,
    logical_path: str,
    source_ref: str,
    content_hash: str,
) -> str:
    """Return a stable identity for one immutable document version/source."""
    identity = [
        "linkloom-document-v2",
        workspace_id,
        resource_id,
        document_id,
        logical_path,
        source_ref,
        content_hash,
    ]
    return f"doc_v2_{_digest(identity)}"


def passage_evidence_id(
    *,
    workspace_id: str,
    resource_id: str,
    document_id: str,
    logical_path: str,
    source_ref: str,
    content_hash: str,
    line_start: int,
    line_end: int,
    quote_hash: str,
) -> str:
    """Return an identity for one exact quote in one immutable document version."""
    document_id_value = document_identity_id(
        workspace_id=workspace_id,
        resource_id=resource_id,
        document_id=document_id,
        logical_path=logical_path,
        source_ref=source_ref,
        content_hash=content_hash,
    )
    return f"ev_v2_{_digest([document_id_value, line_start, line_end, quote_hash])}"


def _digest(identity: list[object]) -> str:
    canonical = json.dumps(
        identity,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
