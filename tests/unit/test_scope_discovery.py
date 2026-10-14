import pytest

from linkloom.semantic_ingestion.relations import FrozenRelationResolver
from linkloom.ui.scope_discovery import discover_scope, resolve_query_date


SLOTS = [
    {"subject": "Launch", "relation": "uses supplier", "values": ["Northstar", "Seabrook"]},
    {"subject": "Support", "relation": "uses supplier", "values": ["Riverton"]},
    {"subject": "Launch", "relation": "has budget", "values": ["50000"]},
]
RESOLVER = FrozenRelationResolver(
    ("uses supplier", "has budget"), aliases={"合作方": "uses supplier"})


@pytest.mark.parametrize(("query", "slots", "status", "subject"), [
    ("我们现在采用哪家供应商？", SLOTS[:1], "MATCHED", "Launch"),
    ("Launch 现在采用哪家供应商？", SLOTS, "MATCHED", "Launch"),
    ("Why did we switch from Northstar to Seabrook?", SLOTS, "MATCHED", "Launch"),
    ("我们现在采用哪家供应商？", SLOTS, "AMBIGUOUS", None),
    ("What is the weather?", SLOTS, "NO_MATCH", None),
    ("Which supplier does Ghost use?", SLOTS[:1], "NO_MATCH", None),
    ("Which supplier does ghost use?", SLOTS[:1], "NO_MATCH", None),
    ("Which supplier does project 123 use?", SLOTS[:1], "NO_MATCH", None),
    ("Which supplier does Launch or Ghost use?", SLOTS[:1], "NO_MATCH", None),
    ("幽灵项目采用哪家供应商？", SLOTS[:1], "NO_MATCH", None),
    ("Launch 的合作方是哪家？", SLOTS, "MATCHED", "Launch"),
    ("Compare Launch and Support suppliers", SLOTS, "AMBIGUOUS", None),
    ("What supplier uses Northstar and Riverton?", SLOTS, "AMBIGUOUS", None),
])
def test_discovery_requires_agreeing_trusted_identifiers(query, slots, status, subject):
    result = discover_scope(query, slots, RESOLVER)
    assert result["status"] == status
    assert (result.get("scope") or {}).get("subject") == subject
    assert len(result["candidates"]) <= 5


def test_explicit_scope_is_normalized_but_must_exist_in_trusted_index():
    result = discover_scope("current", SLOTS, RESOLVER, subject=" launch ", relation="合作方")
    assert result["status"] == "EXPLICIT"
    assert result["scope"] == {"subject": "Launch", "relation": "uses supplier"}
    assert discover_scope("current", SLOTS, RESOLVER, subject="Ghost", relation="uses supplier")["status"] == "NO_MATCH"


@pytest.mark.parametrize(("query", "selected", "resolved", "error"), [
    ("2026-10-06 用哪家供应商？", None, "2026-10-06", None),
    ("2026年10月6日用哪家供应商？", None, "2026-10-06", None),
    ("上个月这个项目用的是哪家供应商？", None, None, "DATE_SCOPE_REQUIRED"),
    ("last month supplier", "2026-09-15", "2026-09-15", None),
    ("2026-10-06 supplier", "2026-10-10", None, "DATE_SCOPE_REQUIRED"),
    ("between 2026-10-01 and 2026-10-10", None, None, "DATE_SCOPE_REQUIRED"),
    ("2026-02-30 supplier", None, None, "INVALID_AS_OF"),
    ("supplier in September", None, None, "DATE_SCOPE_REQUIRED"),
    ("What supplier did we use in the past month?", None, None, "DATE_SCOPE_REQUIRED"),
    ("上季度采用哪家供应商？", None, None, "DATE_SCOPE_REQUIRED"),
    ("What supplier tomorrow?", None, None, "DATE_SCOPE_REQUIRED"),
    ("Oct 1–Oct 10 supplier", None, None, "DATE_SCOPE_REQUIRED"),
    ("2026-10-07T11:00:00Z supplier", None, "2026-10-07T11:00:00Z", None),
    ("2026-10-07T11:00:00 supplier", None, None, "INVALID_AS_OF"),
    ("between 2026-10-01 and now supplier", None, None, "DATE_SCOPE_REQUIRED"),
    ("2026-10-01 to now supplier", None, None, "DATE_SCOPE_REQUIRED"),
    ("2026-10-01 and now supplier", None, None, "DATE_SCOPE_REQUIRED"),
    ("2026-10-01至今采用哪家供应商？", None, None, "DATE_SCOPE_REQUIRED"),
    ("What supplier were we using in 2025?", None, None, "DATE_SCOPE_REQUIRED"),
])
def test_date_resolution_never_invents_a_relative_or_range_date(query, selected, resolved, error):
    value, failure = resolve_query_date(query, selected)
    assert value == resolved
    assert (failure or {}).get("code") == error
