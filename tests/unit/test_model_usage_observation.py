import pytest

from linkloom.agents.model_adapter import ModelUsage, ValidationError
from linkloom.agents.providers import gemini_api


def test_new_usage_fields_default_to_none_and_stay_out_of_old_serialization():
    usage = ModelUsage(input_tokens=1, output_tokens=2, total_tokens=3, duration_ms=4)
    assert usage.cached_input_tokens is usage.reasoning_tokens is usage.provider_reported_cost_usd is None
    assert usage.to_dict() == dict(input_tokens=1, output_tokens=2, total_tokens=3, duration_ms=4)
    assert ModelUsage.from_dict(usage.to_dict()) == usage


def test_reported_sub_counts_and_cost_round_trip():
    usage = ModelUsage(input_tokens=10, output_tokens=5, cached_input_tokens=4, reasoning_tokens=2,
                       provider_reported_cost_usd=0.01)
    assert ModelUsage.from_dict(usage.to_dict()) == usage
    with pytest.raises(ValidationError):
        ModelUsage(provider_reported_cost_usd=-1)
    with pytest.raises(ValidationError):
        ModelUsage(cached_input_tokens=-1)


def test_gemini_maps_cache_and_thoughts_only_when_present():
    reported = gemini_api._extract_usage({"usage_metadata": dict(
        prompt_token_count=10, candidates_token_count=5, total_token_count=17,
        cached_content_token_count=4, thoughts_token_count=2)}, 1.0)
    assert (reported.cached_input_tokens, reported.reasoning_tokens) == (4, 2)
    assert reported.output_tokens == 7 and reported.total_tokens == 17
    bare = gemini_api._extract_usage({"usage_metadata": dict(
        prompt_token_count=10, candidates_token_count=5, total_token_count=15)}, 1.0)
    assert bare.cached_input_tokens is None and bare.reasoning_tokens is None
    assert gemini_api._extract_usage({}, 1.0).input_tokens is None
