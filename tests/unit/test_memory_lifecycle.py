import os
import tempfile
import pytest
from linkloom.memory.models import MemoryScope, MemoryStatus
from linkloom.memory.store import MemoryStore
from linkloom.memory.lifecycle import MemoryLifecycle

@pytest.fixture
def lifecycle():
    with tempfile.TemporaryDirectory() as temp_dir:
        store = MemoryStore(os.path.join(temp_dir, "memory.jsonl"))
        yield MemoryLifecycle(store)

def test_candidate_pending_until_confirm(lifecycle):
    candidate = lifecycle.propose_candidate(
        scope=MemoryScope.GLOBAL,
        key="test_key",
        value={"foo": "bar"},
        source_refs=["ref1"],
    )
    assert candidate.status == MemoryStatus.PENDING
    assert len(lifecycle.list_candidates(MemoryStatus.PENDING)) == 1
    assert len(lifecycle.list_active()) == 0

    item = lifecycle.confirm_candidate(candidate.id, "actor1")
    assert item.status == MemoryStatus.ACTIVE
    assert len(lifecycle.list_candidates(MemoryStatus.PENDING)) == 0
    assert len(lifecycle.list_active()) == 1

def test_edit_confirm(lifecycle):
    candidate = lifecycle.propose_candidate(MemoryScope.GLOBAL, "key", {"a": 1}, ["ref"])
    item = lifecycle.confirm_candidate(candidate.id, "actor", edited_value={"a": 2})
    assert item.value == {"a": 2}

def test_reject(lifecycle):
    candidate = lifecycle.propose_candidate(MemoryScope.GLOBAL, "key", {"a": 1}, ["ref"])
    rejected = lifecycle.reject_candidate(candidate.id, "actor")
    assert rejected.status == MemoryStatus.REJECTED
    assert len(lifecycle.list_candidates(MemoryStatus.PENDING)) == 0
    assert len(lifecycle.list_active()) == 0

def test_revoke_expire_supersede(lifecycle):
    c1 = lifecycle.propose_candidate(MemoryScope.GLOBAL, "key1", {"a": 1}, ["ref"])
    i1 = lifecycle.confirm_candidate(c1.id, "actor")
    revoked = lifecycle.revoke_item(i1.id, "actor")
    assert revoked.status == MemoryStatus.REVOKED

    c2 = lifecycle.propose_candidate(MemoryScope.GLOBAL, "key2", {"a": 1}, ["ref"])
    i2 = lifecycle.confirm_candidate(c2.id, "actor")
    expired = lifecycle.expire_item(i2.id, "actor")
    assert expired.status == MemoryStatus.EXPIRED

    c3 = lifecycle.propose_candidate(MemoryScope.GLOBAL, "key3", {"a": 1}, ["ref"])
    i3 = lifecycle.confirm_candidate(c3.id, "actor")
    superseded = lifecycle.supersede_item(i3.id, "actor")
    assert superseded.status == MemoryStatus.SUPERSEDED

    assert len(lifecycle.list_active()) == 0
