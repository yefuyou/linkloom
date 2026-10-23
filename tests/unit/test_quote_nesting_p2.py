"""Quote-boundary acceptance through actual Discovery; no model semantics score."""
from datetime import UTC, datetime

import pytest

from linkloom.semantic_ingestion.artifacts import RawArtifact
from linkloom.semantic_ingestion.task_discovery_veto import non_asserted
from linkloom.semantic_ingestion.text_scopes import quoted_ranges, unquoted_text
from test_task_observation_discovery import claim, setup


P2 = '''He wrote: 'cause deliver the layout after reading "'Hello'" tomorrow.' '''.rstrip()
WORK = 'deliver the layout'
LATER = 'inspect the export'


def discover(text, *work):
    artifact = RawArtifact(workspace_id='public-workspace', artifact_id='quote-p2',
        source_type='timestamped_text' if text.startswith('[') else 'manual_text',
        content=text, ingestion_time=datetime(2026, 10, 23, tzinfo=UTC))
    service, provider, registry, current = setup(artifact, [claim(w, w) for w in work])
    result = service.discover((artifact,), workspace_id=artifact.workspace_id)
    return artifact, result, provider, registry, current


def test_exact_p2_keeps_the_outer_range_and_vetoes_discovery():
    assert P2.index(WORK) == 17 and len(P2) == 70
    assert (10, 70) in quoted_ranges(P2)
    assert non_asserted(P2, [WORK], 17, 17 + len(WORK))
    _, result, provider, _, _ = discover(P2, WORK)
    assert len(provider.requests) == 1
    assert not result.valid_observations and not result.partial_observations
    assert [f.reason for f in result.failures] == ['NON_ASSERTED_TASK']


@pytest.mark.parametrize('prefix', ['', '😀 中文前缀。 '])
@pytest.mark.parametrize('quote', [
    "'deliver the layout'", '"deliver the layout"',
    "‘deliver the layout’", "“deliver the layout”", "「deliver the layout」", "『deliver the layout』",
    P2, P2.removeprefix('He wrote: '),
    '''"She said 'cause deliver the layout after reading “'Hello'” tomorrow.'"''',
    '''‘She said "'cause deliver the layout after reading 'Hello' tomorrow.'"’''',
])
def test_real_quote_is_withheld_and_independent_later_work_survives(prefix, quote):
    text = prefix + quote + '\nI will inspect the export.'
    artifact, result, _, _, _ = discover(text, WORK, LATER)
    observations = result.valid_observations + result.partial_observations
    observation, = observations
    assert observation.description == LATER
    assert observation.verify(artifact)
    assert observation.owner is observation.deadline is None
    assert observation.relation_status == 'UNCONFIRMED' and observation.authority == 'SUPPORTING_ONLY'
    for evidence in observation.field_evidence:
        assert artifact.content[evidence.char_start:evidence.char_end] == evidence.quote
    visible = unquoted_text(text)
    assert WORK not in visible and LATER in visible
    assert len(visible) == len(text) and visible.index(LATER) == text.index(LATER)


@pytest.mark.parametrize('intro', ['He wrote: ', '原文：', ''])
@pytest.mark.parametrize('opening', ["'", '"', '‘', '“', '「', '『'])
def test_unclosed_ordinary_quotes_do_not_release_work(intro, opening):
    text = intro + opening + WORK + '\nI will inspect the export.'
    _, result, _, _, _ = discover(text, WORK, LATER)
    assert not result.valid_observations and not result.partial_observations


@pytest.mark.parametrize('word', ["'til", "'cause", "'em"])
def test_explicit_incomplete_elision_quotation_is_not_silently_a_task(word):
    text = 'He wrote: ' + word + ' we ' + WORK + '\nI will inspect the export.'
    _, result, _, _, _ = discover(text, WORK, LATER)
    assert not result.valid_observations and not result.partial_observations


@pytest.mark.parametrize('text', [
    "'Kay.\nI will deliver the layout.",
    "We saw 'em earlier.\nI will deliver the layout.",
    "We're reading Mira's notes and the engineers' report.\nI will deliver the layout.",
    "We’re reading Mira’s notes and the engineers’ report.\nI will deliver the layout.",
])
def test_apostrophe_does_not_hide_separate_real_work(text):
    artifact, result, _, _, _ = discover(text, WORK)
    observation, = result.partial_observations
    assert observation.description == WORK and observation.verify(artifact)


@pytest.mark.parametrize('speaker', ['Mira', '林枫😀'])
def test_canonical_speaker_header_is_not_a_quotation_introducer(speaker):
    text = (f"[2029-06-01T09:00:00Z] {speaker}: 'Kay.\n"
            f'[2029-06-01T09:01:00Z] {speaker}: I will deliver the layout.')
    artifact, result, _, _, _ = discover(text, WORK)
    observation, = result.partial_observations
    assert observation.description == WORK and observation.verify(artifact)
    assert observation.owner is None and observation.provenance.ordinal == 1


def test_quote_introducer_inside_a_canonical_body_remains_protected():
    text = ("[2029-06-01T09:00:00Z] Mira: He wrote: 'til we deliver the layout.\n"
            '[2029-06-01T09:01:00Z] Mira: I will inspect the export.')
    _, result, _, _, _ = discover(text, WORK, LATER)
    assert not result.valid_observations and not result.partial_observations


def test_unknown_document_label_does_not_silently_become_a_speaker_header():
    _, result, _, _, _ = discover("Quote: 'til we deliver the layout.\nI will inspect the export.", WORK, LATER)
    assert not result.valid_observations and not result.partial_observations


@pytest.mark.parametrize('quote', [
    '‘We’re going to deliver the layout.’', '“We’re going to deliver the layout.”',
    '‘Mira’s notes say deliver the layout.’', '‘I’ll deliver the layout.’',
])
def test_curly_contraction_inside_real_quote_keeps_the_whole_quote(quote):
    text = quote + '\nI will inspect the export.'
    _, result, _, _, _ = discover(text, WORK, LATER)
    observation, = result.partial_observations
    assert observation.description == LATER


@pytest.mark.parametrize('text,expected_range', [
    ('‘deliver the layout.’Then I will inspect the export.', (0, 21)),
    ('He wrote: ‘deliver the layout’and paused.\nI will inspect the export.', (10, 30)),
])
def test_right_curly_closer_need_not_be_followed_by_whitespace(text, expected_range):
    assert expected_range in quoted_ranges(text)
    artifact, result, _, _, _ = discover(text, WORK, LATER)
    observation, = result.partial_observations
    assert observation.description == LATER and observation.verify(artifact)


def test_damaged_nested_quote_does_not_reinterpret_outer_quote_as_an_elision():
    text = "He wrote 'cause deliver the layout after reading “Hello tomorrow.'\nI will inspect the export."
    _, result, _, _, _ = discover(text, WORK, LATER)
    assert not result.valid_observations and not result.partial_observations


@pytest.mark.parametrize('quoted,work,expected_range', [
    ("He wrote 'cause deliver the layout'后续说明。", WORK, (9, 35)),
    ('‘完成导出封装’后续说明。', '完成导出封装', (0, 8)),
    ('“完成导出封装”后续说明。', '完成导出封装', (0, 8)),
])
def test_latin_and_han_text_do_not_turn_quote_boundaries_into_contractions(quoted, work, expected_range):
    text = quoted + '\nI will inspect the export.'
    assert expected_range in quoted_ranges(text)
    artifact, result, _, _, _ = discover(text, work, LATER)
    observation, = result.partial_observations
    assert observation.description == LATER and observation.verify(artifact)
    assert work not in unquoted_text(text)


@pytest.mark.parametrize('text', ["René's notes", "Rene\u0301's notes", "René’s notes"])
def test_latin_unicode_possessives_preserve_following_task(text):
    artifact, result, _, _, _ = discover(text + '\nI will deliver the layout.', WORK)
    observation, = result.partial_observations
    assert observation.description == WORK and observation.verify(artifact)


@pytest.mark.parametrize('quote', [
    '`deliver the layout`', '```deliver the layout```', '> deliver the layout',
    '```\ndeliver the layout\n```',
])
def test_code_and_markdown_quote_do_not_release_work(quote):
    _, result, _, _, _ = discover(quote + '\nI will inspect the export.', WORK, LATER)
    observation, = result.partial_observations
    assert observation.description == LATER


def test_unclosed_fence_protects_the_remainder():
    _, result, _, _, _ = discover('```\n' + WORK + '\nI will inspect the export.', WORK, LATER)
    assert not result.valid_observations and not result.partial_observations
