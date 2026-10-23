"""Regression contracts for conservative quote masking in the AMI canary."""

import pytest

from linkloom.semantic_ingestion.task_discovery_veto import non_asserted
from linkloom.semantic_ingestion.text_scopes import quoted_ranges, unquoted_text


def _non_asserted(text: str, phrase: str) -> bool:
    start = text.index(phrase)
    return non_asserted(text, (phrase,), start, start + len(phrase))


@pytest.mark.parametrize(
    "text",
    [
        "Don't forget the deployment task.",
        "I'm responsible for the deployment task.",
        "I'll finish the deployment task.",
        "Let's review the deployment task.",
        "The team's deployment task remains active.",
    ],
)
def test_ordinary_english_apostrophes_remain_assertable(text):
    assert quoted_ranges(text) == []
    assert unquoted_text(text) == text
    assert not _non_asserted(text, "deployment task")


@pytest.mark.parametrize("spoken", ["'round", "'Cause", "'cause", "'Kay", "'kay"])
def test_spoken_leading_apostrophes_do_not_open_an_unclosed_quote(spoken):
    text = f"{spoken} the living room; the deployment task remains assigned."
    assert quoted_ranges(text) == []
    assert not _non_asserted(text, "deployment task")


@pytest.mark.parametrize(
    ("opening", "word", "closing"),
    [("'", "round", "'"), ("‘", "cause", "’")],
)
def test_paired_spoken_words_are_real_quotes(opening, word, closing):
    text = (
        f"同事说：{opening}{word}，安排部署任务{closing}。"
        "当前任务已安排。"
    )
    assert _non_asserted(text, "安排部署任务")
    assert not _non_asserted(text, "当前任务已安排")


@pytest.mark.parametrize('text', ["'round'", '‘cause’'])
def test_single_paired_word_is_still_quoted(text):
    assert (0, len(text)) in quoted_ranges(text)


@pytest.fixture
def historical_clay_model_fragment():
    """Exact AMI source lines that triggered the original P2 over-mask."""
    return (
        "ami-nxt/v1 meeting=ES2005c speaker=A "
        "id=ES2005c.A.dialog-act.dharshi.237 start=2038.88 end=2039.09\n"
        "'Kay .\n"
        "ami-nxt/v1 meeting=ES2005c speaker=C "
        "id=ES2005c.C.dialog-act.dharshi.303 start=2039.16 end=2047.13\n"
        "and what I would like you guys to do is work on giving me a model in clay .\n"
    )


def test_historical_clay_model_fragment_keeps_following_assertion_visible(
    historical_clay_model_fragment,
):
    assert quoted_ranges(historical_clay_model_fragment) == []
    assert not _non_asserted(historical_clay_model_fragment, "model in clay")


def test_nested_paired_quotes_mask_the_inner_claim_but_preserve_outer_text():
    text = (
        "同事说：“The inner report says ‘安排部署任务’ and \"安排第二任务\"。” "
        "当前任务已安排。"
    )
    ranges = quoted_ranges(text)
    outer = (text.index("“"), text.index("”") + 1)
    inner_single = (text.index("‘"), text.index("’") + 1)
    inner_double = (text.index("\""), text.index("\"", text.index("\"") + 1) + 1)
    assert outer in ranges
    assert inner_single in ranges
    assert inner_double in ranges
    assert _non_asserted(text, "安排部署任务")
    assert _non_asserted(text, "安排第二任务")
    assert not _non_asserted(text, "当前任务已安排")


def test_same_ascii_quote_pairs_support_a_nested_quoted_claim():
    text = "同事说：'outer says '安排部署任务' outer task remains inside'。当前任务已安排。"
    ranges = quoted_ranges(text)
    assert _non_asserted(text, "安排部署任务")
    assert _non_asserted(text, "outer task")
    assert not _non_asserted(text, "当前任务已安排")
    assert (text.index("'"), text.rindex("'") + 1) in ranges


def test_ascii_quote_closing_after_whitespace_remains_a_close():
    text = "同事说：'安排部署任务 '。当前任务已安排。"
    assert _non_asserted(text, "安排部署任务")
    assert not _non_asserted(text, "当前任务已安排")


def test_unclosed_quote_masks_to_end_conservatively():
    text = "同事说：“不要安排部署任务，仍在讨论。当前任务已安排。"
    assert (text.index("“"), len(text)) in quoted_ranges(text)
    assert _non_asserted(text, "当前任务已安排")


def test_real_quoted_refusal_is_rejected_while_unquoted_unknown_stays_unknown():
    quoted = "同事说：“不要安排部署任务。”"
    assert _non_asserted(quoted, "安排部署任务")

    unknown = "部署任务的负责人尚未确定。"
    assert not _non_asserted(unknown, "部署任务")
