"""Multi-provider model clients (OpenAI-compatible Chat Completions).

Every partner here speaks the same shape — POST /v1/chat/completions
with a Bearer key — so one generic client serves all of them. What differs
is the base URL, the key, and the model, and the *role* each plays in
Proofbraid:

- meta:     primary proposer. Hosted Muse Spark via the Meta Model API.
- baseten:  open-weights proposer. Muse Spark Open / Glimmer class models
            on Baseten's Model APIs. This is also what makes the project
            eligible for the Best Build on Meta Open Weights bonus prize
            (the infra partner verifies it runs on an eligible
            open-weight model).
- fireworks: auxiliary calls. Cheap, fast, small jobs (span ranking,
            hypothesis summaries) so the $150 Meta credits are spent on
            diffs, not chores.

Endpoints and key names are confirmed from the providers' own docs:
Baseten https://inference.baseten.co/v1 with BASETEN_API_KEY;
Fireworks https://api.fireworks.ai/inference/v1 with FIREWORKS_API_KEY.
Model IDs are NOT hard-coded for partners: Baseten models come from its
catalog (`baseten model-api list` or GET /v1/models) and Fireworks model
IDs carry the `accounts/fireworks/models/` prefix from its model library.
Set them via BASETEN_MODEL / FIREWORKS_MODEL. A missing model setting is
a clean configuration error, never a guessed ID.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

PROVIDERS = {
    "meta": {
        "base_url": os.environ.get("META_API_BASE", "https://api.meta.ai/v1"),
        "key_env": "MODEL_API_KEY",
        "model_env": "PROOFBRAID_MODEL",
        "default_model": "muse-spark-1.3",
        "role": "primary proposer (hosted Muse Spark)",
    },
    "baseten": {
        "base_url": "https://inference.baseten.co/v1",
        "key_env": "BASETEN_API_KEY",
        "model_env": "BASETEN_MODEL",
        "default_model": None,
        "role": "open-weights proposer (Muse Spark Open / Glimmer class)",
    },
    "fireworks": {
        "base_url": "https://api.fireworks.ai/inference/v1",
        "key_env": "FIREWORKS_API_KEY",
        "model_env": "FIREWORKS_MODEL",
        "default_model": None,
        "role": "auxiliary calls (cheap, fast, small jobs)",
    },
    # Any OpenAI compatible endpoint; all three settings come from env vars.
    "custom": {
        "base_url": None,
        "base_url_env": "PROOFBRAID_BASE_URL",
        "key_env": "PROOFBRAID_API_KEY",
        "model_env": "PROOFBRAID_MODEL",
        "default_model": None,
        "role": "any OpenAI compatible chat completions endpoint",
    },
}


class ProviderError(RuntimeError):
    """Missing configuration, HTTP error, or malformed response."""


# Token usage reported by the most recent successful call (OpenAI shape:
# {"prompt_tokens": n, "completion_tokens": n, "total_tokens": n}).
# Callers snapshot this right after a proposal, because a later aux call
# would overwrite it. Empty until a call succeeds.
LAST_USAGE: dict = {}


def _config(provider: str) -> tuple[str, str, str]:
    spec = PROVIDERS[provider]
    if "base_url_env" in spec:
        names = (spec["base_url_env"], spec["key_env"], spec["model_env"])
        values = [os.environ.get(name, "").strip() for name in names]
        missing = [name for name, value in zip(names, values) if not value]
        if missing:
            raise ProviderError(
                f"{provider}: {', '.join(missing)} not set. Set "
                f"{', '.join(names)} to use any OpenAI compatible endpoint."
            )
        return values[0].rstrip("/"), values[1], values[2]
    key = os.environ.get(spec["key_env"], "").strip()
    if not key:
        raise ProviderError(
            f"{provider}: {spec['key_env']} is not set. "
            f"Role: {spec['role']}."
        )
    model = os.environ.get(spec["model_env"], "").strip() or spec["default_model"]
    if not model:
        raise ProviderError(
            f"{provider}: no model set. Set {spec['model_env']} to a model "
            f"from the provider's catalog (Baseten: `baseten model-api list`; "
            f"Fireworks: its model library, IDs carry the "
            f"`accounts/fireworks/models/` prefix)."
        )
    return spec["base_url"], key, model


def chat_json(provider: str, system_prompt: str, user_prompt: str,
               timeout: int = 120) -> dict:
    """Ask a provider for a JSON object. Returns the parsed object.

    Raises ProviderError on any failure, so a caller never mistakes a
    failure for a proposal.
    """
    if provider not in PROVIDERS:
        raise ProviderError(
            f"unknown provider {provider!r}; known: {', '.join(PROVIDERS)}"
        )
    base_url, key, model = _config(provider)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ProviderError(
            f"{provider}: HTTP {exc.code}: "
            f"{exc.read().decode('utf-8', errors='replace')[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ProviderError(f"{provider}: could not reach API: {exc}") from exc
    usage = body.get("usage")
    if isinstance(usage, dict):
        LAST_USAGE.clear()
        LAST_USAGE.update(usage)
    try:
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProviderError(
            f"{provider}: unexpected response shape: {str(body)[:500]}"
        ) from exc


def provider_status() -> list[dict]:
    """Configuration status for every provider (keys are never printed)."""
    rows = []
    for name, spec in PROVIDERS.items():
        key_set = bool(os.environ.get(spec["key_env"], "").strip())
        model = os.environ.get(spec["model_env"], "").strip() or spec["default_model"]
        base_env = spec.get("base_url_env")
        base_set = not base_env or bool(os.environ.get(base_env, "").strip())
        rows.append({
            "provider": name,
            "role": spec["role"],
            "key_env": spec["key_env"],
            "key_set": key_set,
            "model": model or "(not set — pick from the provider catalog)",
            "ready": key_set and bool(model) and base_set,
        })
        if base_env:
            rows[-1].update(base_url_env=base_env, base_url_set=base_set)
    return rows
