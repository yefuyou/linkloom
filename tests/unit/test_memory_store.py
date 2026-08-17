import pytest
import os
import tempfile
from linkloom.memory.store import MemoryStore
from linkloom.memory.models import MemoryScope, MemoryStatus

@pytest.fixture
def store():
    with tempfile.TemporaryDirectory() as tmpdir:
        log_path = os.path.join(tmpdir, "memory.jsonl")
        yield MemoryStore(log_path)

def test_create_candidate_is_pending_default(store):
    cand = store.create_candidate(
        MemoryScope.GLOBAL, "test_key", {"data": "valid"}, ["ref1"]
    )
    assert cand.id in store.candidates
    assert store.get_candidate(cand.id) == cand
    assert len(store.list_active()) == 0

def test_confirm_candidate(store):
    cand = store.create_candidate(MemoryScope.PROJECT, "k", {"d": 1}, ["ref"])
    item = store.confirm_candidate(cand.id, "actor_a")
    assert cand.id not in store.candidates
    assert item.id in store.active_items
    assert item.value == {"d": 1}

def test_confirm_candidate_with_edit(store):
    cand = store.create_candidate(MemoryScope.PROJECT, "k", {"d": 1}, ["ref"])
    item = store.confirm_candidate(cand.id, "actor_a", edited_value={"d": 2})
    assert item.value == {"d": 2}

def test_reject_candidate(store):
    cand = store.create_candidate(MemoryScope.PROJECT, "k", {"d": 1}, ["ref"])
    store.reject_candidate(cand.id, "actor_a")
    assert cand.id not in store.candidates
    assert len(store.list_active()) == 0

def test_revoke_item(store):
    cand = store.create_candidate(MemoryScope.PROJECT, "k", {"d": 1}, ["ref"])
    item = store.confirm_candidate(cand.id, "actor_a")
    store.revoke_item(item.id, "actor_a")
    assert item.id not in store.active_items

def test_reload(store):
    cand = store.create_candidate(MemoryScope.PROJECT, "k", {"d": 1}, ["ref"])
    item = store.confirm_candidate(cand.id, "actor_a")
    
    # Reload store
    store.reload()
    assert len(store.candidates) == 0
    assert len(store.active_items) == 1
    assert item.id in store.active_items

def test_no_raw_source_text_on_create(store):
    from linkloom.memory.policy import MemoryPolicyViolation
    with pytest.raises(MemoryPolicyViolation):
        store.create_candidate(MemoryScope.PROJECT, "k", {"body": "full raw notes"}, ["ref"])


def test_terminal_states_are_replayable(store):
    rejected = store.create_candidate(MemoryScope.PROJECT, "reject_me", {"d": 1}, ["ref-reject"])
    store.reject_candidate(rejected.id, "human")
    accepted = store.create_candidate(MemoryScope.PROJECT, "revoke_me", {"d": 2}, ["ref-revoke"])
    item = store.confirm_candidate(accepted.id, "human")
    store.revoke_item(item.id, "human")

    reloaded = MemoryStore(store.log_path)
    assert reloaded.get_candidate(rejected.id, include_terminal=True).status is MemoryStatus.REJECTED
    assert reloaded.get_item(item.id, include_terminal=True).status is MemoryStatus.REVOKED
    assert reloaded.list_active() == []
    assert reloaded.list_candidates() == []


def test_metadata_is_policy_checked(store):
    from linkloom.memory.policy import MemoryPolicyViolation
    with pytest.raises(MemoryPolicyViolation):
        store.create_candidate(MemoryScope.PROJECT, "k", {"d": 1}, ["ref"], metadata={"Gold": "x"})


def test_torn_final_event_is_ignored_on_reload(store):
    candidate = store.create_candidate(MemoryScope.PROJECT, "reloadable", {"d": 1}, ["ref"])
    with open(store.log_path, "a", encoding="utf-8") as handle:
        handle.write('{"event_id":"torn"')
    reloaded = MemoryStore(store.log_path)
    assert reloaded.get_candidate(candidate.id) is not None
