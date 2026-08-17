import pytest
from linkloom.memory.models import MemoryScope, MemoryCandidate, MemoryItem, MemoryStatus

def test_memory_candidate_creation():
    cand = MemoryCandidate(
        id="a" * 64,
        scope=MemoryScope.PROJECT,
        key="test_key",
        value={"prop": 123},
        source_refs=["doc1"],
        created_at="2026-08-16T12:00:00Z"
    )
    assert len(cand.id) == 64
    assert cand.scope == MemoryScope.PROJECT
    assert cand.value["prop"] == 123

def test_memory_item_creation():
    item = MemoryItem(
        id="b" * 64,
        candidate_id="a" * 64,
        scope=MemoryScope.PROJECT,
        key="test_key",
        value={"prop": 123},
        source_refs=["doc1"],
        confirmed_at="2026-08-16T12:01:00Z",
        actor="user1"
    )
    assert item.actor == "user1"


def test_memory_contract_rejects_invalid_ids_and_scopes():
    with pytest.raises(ValueError):
        MemoryCandidate("not-a-digest", MemoryScope.PROJECT, "key", {"ok": True}, ["doc1"], "2026-08-16T12:00:00Z")
    with pytest.raises(ValueError):
        MemoryCandidate("a" * 64, "unknown", "key", {"ok": True}, ["doc1"], "2026-08-16T12:00:00Z")
    with pytest.raises(ValueError):
        MemoryCandidate("a" * 64, MemoryScope.PROJECT, "key", {"ok": True}, ["C:\\vault\\note.md"], "2026-08-16T12:00:00Z")


def test_memory_status_defaults_are_explicit():
    candidate = MemoryCandidate("a" * 64, MemoryScope.GLOBAL, "preference", {"ok": True}, ["ref"], "2026-08-16T12:00:00Z")
    assert candidate.status is MemoryStatus.PENDING
