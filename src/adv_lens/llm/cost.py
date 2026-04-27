"""Per-model pricing table (USD per million tokens).

Numbers track Anthropic's published Claude pricing as of 2026-04. Update
here when pricing changes; one source of truth for the audit table and
any cost-aware routing logic that lands later.
"""

from __future__ import annotations

# (input_per_mtok_usd, output_per_mtok_usd)
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.80, 4.0),
    # Add older / preview model IDs here as needed.
}


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """Return the estimated USD cost for one Anthropic call.

    Returns 0.0 when the model isn't in the table — better to log a zero than
    to crash the audit write on a fresh model release. Update the table.
    """
    pricing = _PRICING_USD_PER_MTOK.get(model)
    if pricing is None:
        return 0.0
    p_in, p_out = pricing
    return (prompt_tokens / 1_000_000) * p_in + (completion_tokens / 1_000_000) * p_out
