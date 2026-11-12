"""Token prices and cost per call."""

# USD per 1M tokens: (input, output, cache read, 5-minute cache write).
# Anthropic first-party list prices, checked 2026-11-03. A suite can override
# or add models with its own "prices" block.
PRICES = {
    "claude-fable-5-2": (8.00, 40.00, 0.20, 10.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10, 2.50),
    # shortcut: Haiku 5.5 costs 5x more on prompts over 100K tokens; add that rate card if your prompts get that long.
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),
}


def cost(model, usage):
    """Dollar cost of one response, from its usage object."""
    inp, out, read, write = PRICES[model]
    tokens = lambda name: getattr(usage, name, 0) or 0
    return (
        tokens("input_tokens") * inp
        + tokens("output_tokens") * out
        + tokens("cache_read_input_tokens") * read
        + tokens("cache_creation_input_tokens") * write
    ) / 1e6
