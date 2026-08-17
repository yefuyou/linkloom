import pytest
from linkloom.memory.models import MemoryScope, MemoryItem, MemoryStatus
from linkloom.memory.retriever import MemoryRetriever

def make_item(id_val, scope, key, value, metadata=None):
    id_val = id_val.rjust(64, "0")
    cand_id = id_val.replace("0", "c")
    return MemoryItem(
        id=id_val,
        candidate_id=cand_id,
        scope=scope,
        key=key,
        value=value,
        source_refs=["ref"],
        confirmed_at="2026-08-16T12:00:00Z",
        actor="tester",
        metadata=metadata or {}
    )

def test_scope_filtering():
    items = [
        make_item("1", MemoryScope.GLOBAL, "g1", {"v": 1}),
        make_item("2", MemoryScope.THREAD, "t1", {"v": 2}, {"scope_key": "thread_a"}),
        make_item("3", MemoryScope.THREAD, "t2", {"v": 3}, {"scope_key": "thread_b"}),
        make_item("4", MemoryScope.PROJECT, "p1", {"v": 4}),
    ]
    retriever = MemoryRetriever(items)
    
    res = retriever.retrieve("query", MemoryScope.THREAD, "thread_a")
    ids = [r["memory_id"] for r in res]
    assert set(ids) == {"1".rjust(64, "0"), "2".rjust(64, "0")}

def test_ranking():
    items = [
        make_item("1", MemoryScope.GLOBAL, "apple", {"desc": "A red fruit"}),
        make_item("2", MemoryScope.GLOBAL, "banana", {"desc": "A yellow fruit"}),
        make_item("3", MemoryScope.GLOBAL, "fruit_basket", {"desc": "Apple and banana"}),
    ]
    retriever = MemoryRetriever(items)
    res = retriever.retrieve("apple", MemoryScope.GLOBAL)
    assert res[0]["key"] == "apple"
    assert res[1]["key"] == "fruit_basket"
    assert len(res) == 3

def test_budget():
    items = [
        make_item("1", MemoryScope.GLOBAL, "k1", {"long": "a" * 1000}),
        make_item("2", MemoryScope.GLOBAL, "k2", {"long": "a" * 1000}),
        make_item("3", MemoryScope.GLOBAL, "k3", {"long": "a" * 1000}),
    ]
    retriever = MemoryRetriever(items)
    res = retriever.retrieve("a", MemoryScope.GLOBAL, max_items=2)
    assert len(res) == 2

    res2 = retriever.retrieve("a", MemoryScope.GLOBAL, max_chars=1500)
    assert len(res2) == 1


def test_terminal_items_are_not_injected():
    item = make_item("9", MemoryScope.GLOBAL, "revoked", {"v": 1})
    item.status = MemoryStatus.REVOKED
    assert MemoryRetriever([item]).retrieve("revoked", MemoryScope.GLOBAL) == []
