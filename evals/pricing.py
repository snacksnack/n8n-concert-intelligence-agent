"""Prices for the RC1-505 provider matrix, beyond the Anthropic-only snapshot.

`agent_evals.pricing` is, by its own docstring, a snapshot of the first-party
Anthropic API price list — and it should stay that way. This eval spans two
more providers (Cohere, Perplexity) and a per-search *tool* fee, none of which
belong in that table. So the extra prices live here, under the same discipline:

* a local, **dated** snapshot (`AS_OF`), not a live lookup, so two runs months
  apart stay comparable;
* **list** prices, never promotional, so a cost informs steady state;
* **raise** on an unknown model rather than silently costing a run at zero — a
  fake $0.00 reads as a finding, which is exactly RC1-254's lesson.

Anthropic models are delegated to `agent_evals.pricing`; everything else is
priced here. Perplexity's Agent API returns an authoritative `usage.cost`
(tokens *and* search fees) per call — arms prefer that and fall back to the
local estimate only when it is absent.
"""

from __future__ import annotations

from decimal import Decimal

from agent_evals import pricing as base

#: When the non-Anthropic prices below were last verified against the published
#: price lists (docs.perplexity.ai/getting-started/pricing, cohere.com/pricing).
AS_OF = "2026-10-08"

_MILLION = Decimal("1000000")

#: Per-million-token list prices for the non-Anthropic *generation* models.
#: (input_per_mtok, output_per_mtok). Anthropic ids are intentionally absent —
#: `token_cost` delegates those to the shared snapshot.
_TOKEN_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    # Perplexity Agent API, model `perplexity/sonar`.
    "perplexity/sonar": (Decimal("0.25"), Decimal("2.50")),
    # Cohere Command. TODO(RC1-505): confirm the exact model id and rate on the
    # account before the arm-2 run — the estate found Command "unpriced" once
    # (RC1-474), which is the gap this entry closes. Placeholder list price.
    "command-a-03-2025": (Decimal("2.50"), Decimal("10.00")),
}

#: Per-search tool fees, USD per single web search. A grounded-web arm pays this
#: on top of its tokens; a generation arm never searches and so never pays it.
WEB_SEARCH_FEE: dict[str, Decimal] = {
    "anthropic": Decimal("0.010"),  # $10 / 1,000 searches (Messages web_search tool)
    "perplexity": Decimal("0.0025"),  # web_search $0.0025/call (Fast Search $0.001)
}


def token_cost(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Token cost of one call, Anthropic via the shared snapshot, else local.

    Raises `agent_evals.pricing.UnknownModelPrice` for a model neither table
    knows — the same loud failure the shared module makes, for the same reason.
    """
    try:
        return base.cost_usd(model, input_tokens, output_tokens)
    except base.UnknownModelPrice:
        pass
    try:
        price_in, price_out = _TOKEN_PRICES[model]
    except KeyError as exc:
        known = ", ".join(sorted(set(base.PRICES) | set(_TOKEN_PRICES)))
        raise base.UnknownModelPrice(
            f"no price on file for {model!r} (known: {known}); add it to evals.pricing "
            "rather than letting the run record claim zero cost"
        ) from exc
    return (Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out) / _MILLION


def search_fee(provider: str, searches: int) -> Decimal:
    """Total per-search fee for `searches` invocations by `provider`.

    Zero for a provider with no per-search line (generation arms pass 0 anyway).
    """
    return WEB_SEARCH_FEE.get(provider, Decimal("0")) * Decimal(searches)


def call_cost(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    searches: int = 0,
) -> Decimal:
    """All-in cost of one call: tokens + any per-search fee."""
    return token_cost(model, input_tokens, output_tokens) + search_fee(provider, searches)
