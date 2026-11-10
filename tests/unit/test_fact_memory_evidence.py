"""Value evidence (literal source span in a full sentence) and time-ordered fact queries."""
from __future__ import annotations

from datetime import UTC, datetime
import json

from linkloom.agents.model_adapter import ModelAction, ModelResponse, ModelUsage
from linkloom.fact_memory.ask import PROMPT, answer, relevant_records
from linkloom.fact_memory.evidence import sentences, verify_value
from linkloom.fact_memory.normalize import normalize_value
from linkloom.fact_memory.store import FactRecord, fact_id_for, slot_key_for

QUOTE = ("Quick update on my hobbies. I've added seventeen new postcards to my collection since I started again! "
         "Mrs. Okafor from the club sent two of them from Lisbon on 3rd of March, 2026.")


def test_evidence_is_the_full_verbatim_sentence():
    result = verify_value("seventeen new postcards", QUOTE)
    assert result.reason is None
    assert result.sentence == "I've added seventeen new postcards to my collection since I started again!"
    assert QUOTE[result.start:result.end] == result.sentence
    # An abbreviation does not end a sentence.
    assert [QUOTE[a:b] for a, b in sentences(QUOTE)][-1].startswith("Mrs. Okafor")


def test_rewritten_numbers_and_dates_are_not_source_evidence():
    assert verify_value("3 bikes", "I own three bikes now.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("twenty-five dollars", "It cost $25 in the end.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("$400,000", "We were pre-approved for 400 thousand dollars.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("2,500 steps", "I walked 2500 steps.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("March 3", "She sent them on 3rd of March, 2026.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("2026-03-03", "She sent them on March 3rd, 2026.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("met twice", "We have met 2 times so far.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("25:50", "My best 5K was 25:50 last week.").reason is None


def test_reworded_value_is_rejected_even_when_numbers_and_names_agree():
    result = verify_value("two postcards sent from Lisbon by Mrs. Okafor", QUOTE)
    assert result.reason == "FACT_VALUE_NOT_IN_SOURCE" and result.keys == ()
    assert result.sentence is None


def test_values_not_found_in_one_source_sentence_are_rejected():
    assert verify_value("18 postcards since restarting", QUOTE).reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("postcards from Porto", QUOTE).reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("sent on March 4", QUOTE).reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("sent on March 3, 2025", QUOTE).reason == "FACT_VALUE_NOT_IN_SOURCE"
    assert verify_value("5", "My best 5K was 25:50.").reason == "FACT_VALUE_NOT_IN_SOURCE"
    # An opening name that the sentence does not contain is not dropped silently.
    assert verify_value("Sunrise Bakery", "I finally picked a bakery near the station.").reason \
        == "FACT_VALUE_NOT_IN_SOURCE"
    # Pieces from two sentences cannot be combined into one source value.
    assert verify_value("17 postcards from Lisbon", QUOTE).reason == "FACT_VALUE_NOT_IN_SOURCE"


def test_plain_values_are_verified_only_by_the_source_span():
    for vague, expected in (("a lot of fun", None), ("in the bedroom", None),
                            ("really happy about it", "FACT_VALUE_NOT_IN_SOURCE"),
                            ("最喜欢的颜色", "FACT_VALUE_NOT_IN_SOURCE")):
        assert verify_value(vague, "It was a lot of fun in the bedroom.").reason == expected
        assert verify_value(vague, vague + ".").reason is None
    assert verify_value(None, QUOTE).reason == "FACT_VALUE_MISSING"
    assert verify_value("Pros: lots of shared memories", "Pros: lots of shared memories.").reason is None
    assert verify_value("Lisbon", "We moved to Lisbon in spring.").reason is None
    assert verify_value("Lisbon", "Lisbon is lovely.").reason is None


def test_query_matching_folds_plural_and_verb_suffixes():
    from linkloom.fact_memory.ask import tokens
    assert tokens("Which sauces did I buy?") == tokens("which sauce did I buy")
    assert tokens("postcards collected") == ["postcard", "collect"]


def _fact(candidate: str, attribute: str, value: str, month: int, sentence: str) -> FactRecord:
    key = attribute.casefold()
    return FactRecord(
        fact_id=fact_id_for("ws", candidate), workspace_id="ws",
        slot_key=slot_key_for("ws", "speaker:mira", key, True, candidate), subject_id="speaker:mira",
        subject_label="Mira", subject_basis="FIRST_PERSON", attribute_name=attribute, attribute_key=key,
        mergeable=True, value_text=value, value_normalized=normalize_value(value), value_kind="TEXT",
        effective_at=datetime(2026, month, 1, tzinfo=UTC), candidate_id=candidate, artifact_id="src",
        artifact_version_id="v1", evidence_ref="ev", quote=sentence, evidence_sentence=sentence,
        evidence_start=0, evidence_end=len(sentence), key_content="[]", reviewer_id="r", review_reason="ok",
        recorded_at=datetime(2026, 9, 1, tzinfo=UTC))


NEWER_LAST = (
    _fact("c2", "preferred tea", "Roasted Oolong", 5, "These days my preferred tea is Roasted Oolong."),
    _fact("c1", "favorite tea", "Darjeeling", 3, "My favorite tea is Darjeeling."),
    _fact("c3", "dog's name", "Pepper", 4, "My dog is called Pepper."),
)


def test_same_meaning_under_two_attribute_names_returns_the_newer_fact():
    question = "Which tea do I like best?"
    result = answer(question, datetime(2026, 6, 1, tzinfo=UTC), NEWER_LAST, None)
    assert result["status"] == "ANSWERED" and result["answer"] == "Roasted Oolong"
    selected = relevant_records(question, NEWER_LAST, datetime(2026, 6, 1, tzinfo=UTC))
    assert [record.value_text for record, _ in selected] == ["Darjeeling", "Roasted Oolong"]  # oldest first
    # Asked about a past point in time, only records at or before it are offered.
    past = answer(question, datetime(2026, 4, 1, tzinfo=UTC), NEWER_LAST, None)
    assert past["answer"] == "Darjeeling"


def test_model_receives_the_subjects_relevant_facts_sorted_by_time_with_the_rules():
    seen = []

    class Adapter:
        def complete(self, request):
            seen.append(request.user_input)
            records = json.loads(request.user_input.split("Records: ", 1)[1])
            return ModelResponse(action=ModelAction.final(json.dumps(
                {"status": "ANSWERED", "answer": records[-1]["value"], "fact_ids": [records[-1]["id"]]})),
                usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2))

    result = answer("Which tea do I like best?", datetime(2026, 6, 1, tzinfo=UTC), NEWER_LAST, Adapter)
    assert result["answer"] == "Roasted Oolong" and result["cited"][0].candidate_id == "c2"
    prompt = seen[0]
    records = json.loads(prompt.split("Records: ", 1)[1])
    assert [item["effective_at"][:7] for item in records] == ["2026-03", "2026-05"]
    assert {item["attribute"] for item in records} == {"favorite tea", "preferred tea"}
    assert "the most recent record wins" in prompt and "use only records at or before that point" in prompt
    assert "the most recent record wins" in PROMPT
