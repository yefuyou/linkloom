from __future__ import annotations

from linkloom.retrieval_v2.subgoal_query_strategy import SubgoalQueryStrategy


def test_verified_cobalt_b_targets_owner_subgoal() -> None:
    strategy = SubgoalQueryStrategy()

    queries = strategy.build_queries("owner", ("Cobalt B",))

    assert queries == ("Cobalt B owner",)


def test_verified_mira_chen_targets_leader_subgoal() -> None:
    strategy = SubgoalQueryStrategy()

    queries = strategy.build_queries("leader", ("Mira Chen",))

    assert queries == ("Mira Chen leader",)


def test_missing_verified_entity_does_not_advance_dependent_subgoal() -> None:
    strategy = SubgoalQueryStrategy()

    queries = strategy.build_queries("owner", ())

    assert queries == ()


def test_queries_are_deduplicated_and_bounded_to_two() -> None:
    strategy = SubgoalQueryStrategy()

    queries = strategy.build_queries(
        "leader",
        ("Mira Chen", "Mira Chen", "Cobalt B", "Kai Wu"),
    )

    assert queries == ("Mira Chen Cobalt B Kai Wu leader",)
    assert len(queries) <= 2


def test_empty_subgoal_does_not_create_a_query() -> None:
    strategy = SubgoalQueryStrategy()

    assert strategy.build_queries("   ", ("Cobalt B",)) == ()


def test_bounded_alternate_can_use_alias_phrase_after_verified_entity() -> None:
    strategy = SubgoalQueryStrategy()

    assert strategy.build_queries(
        "successor approval date", ("Aurora Launch",),
        alternate_subgoal="Cobalt B rollout date",
    ) == (
        "Aurora Launch successor approval date",
        "Aurora Launch Cobalt B rollout date",
    )


def test_multiple_verified_entities_keep_primary_and_alternate_semantics() -> None:
    strategy = SubgoalQueryStrategy()

    assert strategy.build_queries(
        "owner question", ("A", "B"), alternate_subgoal="alternate question",
    ) == (
        "A B owner question",
        "A B alternate question",
    )
