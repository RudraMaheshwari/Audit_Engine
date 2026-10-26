"""Token-cost computation from the model pricing map.

Cost is derived from token counts using per-model USD-per-million rates held in constants. Cached
input tokens (prompt-cache hits) are billed at the reduced 'cached_input' rate and the remaining
input tokens at the standard 'input' rate. Unknown models fall back to zero cost so a missing price
entry never breaks a run — it only under-reports until the map is updated.
"""

from src.config.constants import (
    MODEL_PRICING_PER_MILLION,
    MODEL_PRICING_TOKEN_UNIT,
    PRICE_KEY_CACHED_INPUT,
    PRICE_KEY_INPUT,
    PRICE_KEY_OUTPUT,
)


def compute_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Return the USD cost for a single call given its token counts."""
    rates = MODEL_PRICING_PER_MILLION.get((model or "").lower())
    if not rates:
        return 0.0

    input_rate = rates.get(PRICE_KEY_INPUT, 0.0)
    billable_input = max(0, int(input_tokens or 0) - int(cached_tokens or 0))
    cost = (
        billable_input * input_rate
        + int(cached_tokens or 0) * rates.get(PRICE_KEY_CACHED_INPUT, input_rate)
        + int(output_tokens or 0) * rates.get(PRICE_KEY_OUTPUT, 0.0)
    )
    return round(cost / MODEL_PRICING_TOKEN_UNIT, 6)
