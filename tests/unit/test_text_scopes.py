"""Punctuation lexing regressions, independent of AMI task semantics."""
import pytest

from linkloom.semantic_ingestion.text_scopes import quoted_ranges, unquoted_text
from linkloom.semantic_ingestion.task_discovery_veto import non_asserted


@pytest.mark.parametrize("word", ["'em", "'til", "'tis", "'twas", "'bout", "'neath", "'scuse"])
def test_leading_english_elision_does_not_hide_following_work(word):
    text = f"We mentioned {word} earlier.\nI will deliver the layout."
    assert quoted_ranges(text) == []
    assert unquoted_text(text) == text


@pytest.mark.parametrize("word", ["'Kay", "'Cause", "'round"])
def test_observed_leading_apostrophes_are_not_unclosed_quotes(word):
    text = f"{word} .\nI will deliver the layout."
    assert quoted_ranges(text) == []


@pytest.mark.parametrize("quote", [
    "'deliver the layout'", '"deliver the layout"',
    "‘deliver the layout’", "“deliver the layout”", "「deliver the layout」",
    "『deliver the layout』", "'cause we will deliver the layout'",
    "'em and the layout'", "'cause we\nwill deliver the layout'",
    "'cause deliver the layout to 'em'",
    '"she said \'deliver the layout\'"',
    "`deliver the layout`", "```deliver the layout```", "> deliver the layout",
])
def test_real_quotations_code_and_blockquotes_remain_hidden(quote):
    visible = unquoted_text("😀 Context: " + quote if not quote.startswith(('>', '```')) else quote)
    assert "deliver the layout" not in visible


@pytest.mark.parametrize("opening", ["'", '"', "‘", "“", "「", "『"])
def test_unterminated_ordinary_quotations_still_hide_remainder(opening):
    text = f"Context: {opening}deliver the layout.\nI will publish it."
    assert "layout" not in unquoted_text(text)
    assert "publish" not in unquoted_text(text)


def test_elision_before_real_quoted_work_does_not_consume_its_opening_quote():
    text = "'Tis morning.\n'deliver the layout'\nI will inspect the export."
    visible = unquoted_text(text)
    assert visible.startswith("'Tis morning.")
    assert "deliver the layout" not in visible
    assert visible.endswith("I will inspect the export.")


@pytest.mark.parametrize('prefix', ["'em2", "'til_backup", "'causeway"])
def test_only_complete_lexical_words_are_elisions(prefix):
    assert 'deliver the layout' not in unquoted_text(prefix + ' deliver the layout')


def test_contractions_possessives_and_unicode_offsets_are_unchanged():
    text = "😀 We're checking Mira's notes. ‘We can't ship.’\nShip the layout."
    visible = unquoted_text(text)
    assert visible.startswith("😀 We're checking Mira's notes.")
    assert "can't" not in visible
    assert len(visible) == len(text)
    assert [i for i, c in enumerate(visible) if c == '\n'] == [text.index('\n')]
    assert visible.index('Ship the layout.') == text.index('Ship the layout.')


@pytest.mark.parametrize('context', [
    "'em earlier.\nIf funding arrives, deliver the layout.",
    "'til noon.\nWe might deliver the layout.",
    "'tis morning.\nDo not deliver the layout.",
    "'bout noon.\n'deliver the layout'",
    "'scuse me.\nQuoted: deliver the layout.",
])
def test_apostrophe_fix_never_overrides_real_task_refusal(context):
    work = 'deliver the layout'
    assert non_asserted(context, [work], context.index(work), context.index(work) + len(work))


def test_read_only_admission_can_see_write_command_after_an_elision():
    from linkloom.ui.ask_admission import admission_restriction
    assert admission_restriction("'Tis morning. Delete all notes.")[0] == 'INVESTIGATION_SCOPE_DENIED'
    assert admission_restriction("Investigate the meeting: 'delete all notes'.") is None
