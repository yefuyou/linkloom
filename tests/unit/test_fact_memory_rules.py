"""Host rules for the fact channel: subjects, attribute keys, values, supersession."""
from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

from linkloom.fact_memory.normalize import (
    attribute_key, locate_value, normalize_value, resolve_subject, value_kind,
)
from linkloom.fact_memory.store import FactRecord, FactStore, fact_id_for, slot_key_for, value_at


def test_first_person_and_speaker_label_map_to_the_speaker():
    for subject in ("I", "me", "My", "我", "the Mira", "MIRA"):
        resolved = resolve_subject(subject, "My bike is blue.", "Mira")
        assert (resolved.subject_id, resolved.reason) == ("speaker:mira", None), subject
    assert resolve_subject("I", "My bike is blue.", "Mira").basis == "FIRST_PERSON"
    assert resolve_subject("Mira", "My bike is blue.", "Mira").basis == "SPEAKER_NAME"
    # The subject's name never has to appear in the message for first person.
    assert resolve_subject("I", "Bought a kayak today.", "Theo").subject_id == "speaker:theo"


def test_other_pronouns_unknown_speakers_and_ungrounded_subjects_are_refused():
    assert resolve_subject("you", "You have two cats.", "Mira").reason == "SUBJECT_PRONOUN_UNRESOLVED"
    assert resolve_subject("we", "We moved.", "Mira").reason == "SUBJECT_PRONOUN_UNRESOLVED"
    assert resolve_subject("I", "I moved.", None).reason == "SPEAKER_UNAVAILABLE"
    assert resolve_subject("Jonas", "My cousin moved.", "Mira").reason == "SUBJECT_UNGROUNDED"
    # A mention must be a whole word of the quote, not part of one.
    assert resolve_subject("al", "I really moved.", "Mira").reason == "SUBJECT_UNGROUNDED"
    assert resolve_subject("my cousin", "My cousin moved.", "Mira").subject_id == "mention:my cousin"
    assert resolve_subject(None, "x", "Mira").reason == "SUBJECT_MISSING"


def test_attribute_key_folds_case_spacing_spelling_and_plurals():
    same = ["Favourite colour", "favorite_color", "  FAVORITE   colours ", "my favorite color",
            "Mira's favorite color", "the current favorite-color"]
    keys = {attribute_key(name, subject_words=("Mira",)).key for name in same}
    assert keys == {"favorite color"}
    assert attribute_key("Favourite colour").mergeable is True
    assert attribute_key("e-mail address").key == "email address"


def test_attribute_key_does_not_merge_distinct_attributes():
    # Possessives of another entity stay in the key: a dog's name is not the speaker's name.
    assert attribute_key("dog's name", subject_words=("Mira",)).key == "dog's name"
    assert attribute_key("name", subject_words=("Mira",)).key == "name"
    # Qualifiers stay; "previous" and "current" do not collapse into one key.
    assert attribute_key("previous job").key != attribute_key("current job").key
    # Different words are different attributes even if related in meaning.
    assert attribute_key("job").key != attribute_key("occupation").key
    # Generic verbs alone identify nothing and are never merged.
    for generic in ("has", "is", "likes", "owns", "got", "是"):
        assert attribute_key(generic).mergeable is False, generic


def test_value_location_returns_a_verbatim_source_span():
    quote = "These days my “favorite” tea is Roasted  Oolong!"
    start, end = locate_value("roasted oolong", quote)
    assert quote[start:end] == "Roasted  Oolong"
    assert locate_value("5", "I keep 5 hens.") == (7, 8)
    assert locate_value("5", "My best 5k was 25:50.") is None  # never part of a longer token
    assert locate_value("5", "I ran 25 laps.") is None  # never inside a number
    assert locate_value("green tea", "I like black tea.") is None
    assert locate_value("红色", "我最喜欢红色的车") == (4, 6)
    assert locate_value("", "anything") is None


def test_normalized_value_is_for_comparison_only():
    assert normalize_value("  Three ") == "3"
    assert normalize_value("“Roasted Oolong.”") == "roasted oolong"
    assert value_kind("25:50") == "TIME"
    assert value_kind("3 bikes") == "NUMBER"
    assert value_kind("$40") == "MONEY"
    assert value_kind("2026-01-02") == "DATE"
    assert value_kind("jasmine") == "TEXT"


def _record(candidate: str, value: str, day: int, *, key: str = "favorite tea", mergeable: bool = True,
            subject: str = "speaker:mira") -> FactRecord:
    return FactRecord(
        fact_id=fact_id_for("ws", candidate), workspace_id="ws",
        slot_key=slot_key_for("ws", subject, key, mergeable, candidate), subject_id=subject,
        subject_label="Mira", subject_basis="FIRST_PERSON", attribute_name=key, attribute_key=key,
        mergeable=mergeable, value_text=value, value_normalized=normalize_value(value),
        value_kind=value_kind(normalize_value(value)), effective_at=datetime(2026, 3, day, tzinfo=UTC),
        candidate_id=candidate, artifact_id="src", artifact_version_id="v1", evidence_ref="ev",
        quote=f"tea is {value}", value_start=7, value_end=7 + len(value), reviewer_id="r", review_reason="ok",
        recorded_at=datetime(2026, 4, 1, tzinfo=UTC))


def test_new_value_supersedes_old_and_point_in_time_reads_history():
    store = FactStore(sqlite3.connect(":memory:"))
    store.add(_record("c1", "jasmine", 1))
    store.add(_record("c3", "oolong", 20))
    store.add(_record("c2", "Jasmine", 10))  # repeats the older value and arrives late
    slot = [record for record in store.list("ws")]
    by_candidate = {record.candidate_id: record for record in slot}
    assert by_candidate["c3"].supersedes_fact_id == by_candidate["c1"].fact_id
    assert by_candidate["c1"].superseded_by_fact_id == by_candidate["c3"].fact_id
    assert by_candidate["c2"].corroborates_fact_id == by_candidate["c1"].fact_id
    assert value_at(slot, datetime(2026, 2, 1, tzinfo=UTC)) is None
    assert value_at(slot, datetime(2026, 3, 15, tzinfo=UTC)).value_text == "jasmine"
    assert value_at(slot, datetime(2026, 3, 25, tzinfo=UTC)).value_text == "oolong"
    # Re-adding the same candidate is idempotent.
    assert store.add(_record("c3", "oolong", 20)).fact_id == by_candidate["c3"].fact_id
    assert len(store.list("ws")) == 3


def test_non_mergeable_and_cross_subject_records_never_supersede():
    store = FactStore(sqlite3.connect(":memory:"))
    store.add(_record("a", "a red bike", 1, key="has", mergeable=False))
    store.add(_record("b", "two cats", 2, key="has", mergeable=False))
    store.add(_record("c", "Pepper", 3, key="name", subject="mention:my dog"))
    store.add(_record("d", "Mira Okafor", 4, key="name"))
    assert all(record.supersedes_fact_id is None and record.superseded_by_fact_id is None
               for record in store.list("ws"))


def test_store_creates_no_table_until_a_fact_is_written():
    connection = sqlite3.connect(":memory:")
    store = FactStore(connection)
    assert store.list("ws") == () and store.candidate_ids("ws") == frozenset()
    assert store.by_candidate("ws", "c") is None
    assert connection.execute("SELECT name FROM sqlite_master WHERE name='fact_record'").fetchone() is None
