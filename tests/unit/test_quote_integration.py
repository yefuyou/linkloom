"""Joint regressions added without changing either PR's existing expectations."""
import pytest

from linkloom.semantic_ingestion.text_scopes import quoted_ranges, unquoted_text
from test_quote_nesting_p2 import discover


def test_pr21_preserved_same_ascii_failure_through_normal_discovery():
    text = "同事说：'outer says '安排部署任务' outer task remains inside'。当前任务已安排。"
    artifact, result, provider, _, _ = discover(text, '安排部署任务', '当前任务已安排')
    assert len(provider.requests) == 1
    observation, = result.partial_observations
    assert observation.description == '当前任务已安排' and observation.verify(artifact)
    assert [failure.reason for failure in result.failures] == ['NON_ASSERTED_TASK']


@pytest.mark.parametrize('delimiter', ['"', "'", '“', '「'])
def test_escaped_opening_delimiter_does_not_mask_later_work(delimiter):
    text = '\\' + delimiter + 'literal punctuation.\nI will deliver the layout.'
    assert quoted_ranges(text) == []
    assert unquoted_text(text) == text


@pytest.mark.parametrize('slashes', [1, 2, 3, 4])
def test_escape_parity_preserves_source_offsets(slashes):
    text = '\\' * slashes + '"deliver the layout"\nI will inspect the export.'
    visible = unquoted_text(text)
    assert len(visible) == len(text)
    if slashes % 2:
        # The first mark is literal; the final unmatched mark stays conservative.
        assert 'deliver the layout' in visible and 'inspect the export' not in visible
    else:
        assert 'deliver the layout' not in visible and 'inspect the export' in visible


@pytest.mark.parametrize('quote', [
    "'The engineers' report says deliver the layout'",
    "'The engineers' Report says deliver the layout'",
    '‘y’all says deliver the layout’',
    '‘the o’clock report says deliver the layout’',
    '‘rock’n’roll report says deliver the layout’',
])
def test_intra_quote_apostrophes_do_not_release_claims(quote):
    text = quote + '\nI will inspect the export.'
    artifact, result, _, _, _ = discover(text, 'deliver the layout', 'inspect the export')
    observation, = result.partial_observations
    assert observation.description == 'inspect the export' and observation.verify(artifact)
    assert 'deliver the layout' not in unquoted_text(text)
