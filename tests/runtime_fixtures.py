"""Inputs copied from the current Runtime request by synthetic models."""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def offline_retrieval(monkeypatch):
    """Synthetic Agent fixtures exercise tools without optional dense models."""
    monkeypatch.setenv("LINKLOOM_RETRIEVAL_MODE", "bm25")


def source_context_from_text(text):
    marker = ("team_decision_request=" if "team_decision_request=" in text
              else "retrieval_request=")
    packet, _ = json.JSONDecoder().raw_decode(text.split(marker, 1)[1])
    return packet["source_context"]


def source_context_from_request(request):
    return source_context_from_text(request.user_input)


def source_context_from_contents(contents):
    return source_context_from_text("\n".join(
        part["text"] for content in contents for part in content["parts"]
        if isinstance(part.get("text"), str)
    ))
