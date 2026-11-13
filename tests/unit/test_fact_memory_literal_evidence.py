"""Literal evidence accepts source text in any language and rejects rewrites."""
import pytest
from linkloom.fact_memory.evidence import verify_value

@pytest.mark.parametrize('value,quote,sentence', [
    ('蓝色', '我喜欢蓝色。你喜欢什么？', '我喜欢蓝色。'),
    ('三次', '先说近况。我来过三次。', '我来过三次。'),
    ('a golden retriever', 'I have a golden retriever. It is friendly.', 'I have a golden retriever.'),
    ('three bikes', 'I own three bikes now.', 'I own three bikes now.'),
    ('bedroom', 'The desk is in the bedroom.', 'The desk is in the bedroom.'),
    ('Lisbon', 'Lisbon is lovely.', 'Lisbon is lovely.'),
    ('ROASTED  OOLONG', 'I prefer roasted oolong.', 'I prefer roasted oolong.'),
    ('“blue”', 'My color is "blue".', 'My color is "blue".'),
    ('ＡＢＣ', 'My model is ABC.', 'My model is ABC.'),
    ('March 3', 'I moved on March 3.', 'I moved on March 3.'),
])
def test_source_spans_pass(value, quote, sentence):
    result = verify_value(value, quote)
    assert result.reason is None
    assert result.sentence == sentence
    assert quote[result.start:result.end] == sentence
    assert result.keys == ()

@pytest.mark.parametrize('value,quote', [
    ('3 bikes', 'I own three bikes.'),
    ('$400,000', 'I was pre-approved for 400 thousand dollars.'),
    ('March 3', 'I moved on 3rd of March.'),
    ('Porto', 'I moved to Lisbon.'),
    ('Sunrise Bakery', 'I picked a bakery near the station.'),
    ('5', 'My best 5K was 25:50.'),
    ('5', 'I own 25 bikes.'),
    ('blue', 'My favorite color is green.'),
    ('3 bikes from Lisbon', 'I own 3 bikes. I moved from Lisbon.'),
    ('blue. I own', 'My color is blue. I own a bicycle.'),
])
def test_values_not_in_one_source_sentence_are_refused(value, quote):
    result = verify_value(value, quote)
    assert result.reason == 'FACT_VALUE_NOT_IN_SOURCE'
    assert result.sentence is None
    assert result.start is None and result.end is None

@pytest.mark.parametrize('value', [None, '', '  '])
def test_missing_value_is_refused(value):
    assert verify_value(value, 'I own a bicycle.').reason == 'FACT_VALUE_MISSING'
