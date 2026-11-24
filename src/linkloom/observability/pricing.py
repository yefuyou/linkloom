"""Token rates used only to estimate the cost of recorded usage for observation.

They never limit, reserve or reject anything: telemetry multiplies the usage a Provider reported by these rates.
"""
INPUT_USD_PER_MILLION = 1.0
OUTPUT_USD_PER_MILLION = 5.0


def estimate_cost_usd(input_tokens, output_tokens):
    """Estimated cost of one request from reported tokens, or None when either count is unknown.

    Cached and reasoning tokens are sub-counts already inside input/output and are not priced again.
    """
    if input_tokens is None or output_tokens is None:
        return None
    return (input_tokens * INPUT_USD_PER_MILLION + output_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000
