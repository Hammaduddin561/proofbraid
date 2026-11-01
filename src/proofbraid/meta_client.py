"""Meta Model API client.

Thin compatibility wrapper around :mod:`proofbraid.providers`: the Meta
provider is the primary proposer. New code should use providers directly;
this module stays so existing callers keep working unchanged.
"""

from __future__ import annotations

from . import providers

BASE_URL = providers.PROVIDERS["meta"]["base_url"]
DEFAULT_MODEL = providers.PROVIDERS["meta"]["default_model"]


class MetaApiError(providers.ProviderError):
    """Raised for missing keys, HTTP errors, or malformed responses."""


def propose_file_contents(
    system_prompt: str,
    user_prompt: str,
    model: str = DEFAULT_MODEL,
    timeout: int = 120,
) -> dict:
    """Ask Muse Spark for new file contents as JSON: {"files": [...]}.

    Expected output: {"files": [{"path": "relative/path.py",
    "content": "full new file content"}]}. The key comes from MODEL_API_KEY
    only. Without it, raises a clear setup error (claim the hackathon's
    $150 Meta Model API credits first).
    """
    import os

    if model != DEFAULT_MODEL:
        os.environ["PROOFBRAID_MODEL"] = model
    try:
        return providers.chat_json("meta", system_prompt, user_prompt, timeout)
    except providers.ProviderError as exc:
        if "MODEL_API_KEY is not set" in str(exc) or "is not set" in str(exc):
            raise MetaApiError(
                "MODEL_API_KEY is not set. Claim the hackathon's $150 in Meta "
                "Model API credits (Cerebral Valley blast, Oct 26, 2026), put "
                "the key in your environment, and retry:\n"
                "  export MODEL_API_KEY='LLM|...'"
            ) from exc
        raise MetaApiError(str(exc)) from exc
