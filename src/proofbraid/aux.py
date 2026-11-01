"""Auxiliary calls via Fireworks (cheap, fast, small jobs).

Role in the all-partner architecture: Fireworks is the *auxiliary*
provider. Its $25 credits are the smallest pool, so they are spent only
on tiny jobs — ranking spans, summarising a verified hypothesis — while
the $150 Meta credits pay for diffs. If FIREWORKS_API_KEY (or
FIREWORKS_MODEL) is unset, aux calls are skipped silently (return None);
an aux feature must never block the main propose loop.
"""

from __future__ import annotations

from . import providers

_AUX_SYSTEM = (
    "Summarise in one plain sentence, under 25 words, what changed and "
    "why it is safe. No preamble, no bullet points."
)


def summarize_hypothesis(task: str, files: list[str]) -> str | None:
    """One-sentence summary of a verified change via Fireworks.

    Returns None when Fireworks is not configured or the call fails.
    """
    try:
        result = providers.chat_json(
            "fireworks",
            _AUX_SYSTEM,
            f"TASK: {task}\nCHANGED FILES: {', '.join(files)}",
        )
    except providers.ProviderError:
        return None
    if isinstance(result, dict):
        for key in ("summary", "text", "content"):
            if isinstance(result.get(key), str):
                return result[key]
    return None
