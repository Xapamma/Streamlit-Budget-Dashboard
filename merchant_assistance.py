"""Optional local Ollama merchant suggestions and explicit cache approval."""

from __future__ import annotations

import json
from pathlib import Path

from cleaning_logic import clean_description_for_matching


def suggest_merchant_with_ollama(
    description: str,
    *,
    model: str = "gemma3:4b",
) -> str:
    """Ask the locally running Ollama model for a merchant name suggestion."""
    try:
        import ollama
    except ImportError as error:
        raise RuntimeError(
            "Ollama support is optional. Install it with `uv sync --extra ai`, "
            "then retry the suggestion."
        ) from error

    response = ollama.chat(
        model=model,
        format="json",
        messages=[
            {
                "role": "user",
                "content": (
                    "Identify the merchant in this bank transaction description. "
                    "Return only a JSON object with one string field named merchant. "
                    "Do not infer a category or invent a merchant. If uncertain, use "
                    "the cleaned description.\n\nDescription: "
                    f"{description}"
                ),
            }
        ],
    )
    message = response.get("message", {}) if isinstance(response, dict) else response.message
    content = message.get("content", "") if isinstance(message, dict) else message.content
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Ollama returned an invalid merchant suggestion.") from error
    merchant = payload.get("merchant") if isinstance(payload, dict) else None
    if not isinstance(merchant, str) or not merchant.strip():
        raise ValueError("Ollama did not return a merchant name.")
    return merchant.strip()


def approve_merchant_match(
    description: str,
    merchant: str,
    *,
    cache_path: str | Path | None = None,
) -> str:
    """Save a user-approved merchant under the same normalized cache key as the app."""
    normalized_description = clean_description_for_matching(description)
    approved_merchant = merchant.strip()
    if not normalized_description:
        raise ValueError("A non-empty transaction description is required to save a match.")
    if not approved_merchant:
        raise ValueError("A non-empty merchant name is required to save a match.")

    path = Path(cache_path) if cache_path is not None else Path(__file__).with_name("merchant_cache.json")
    cache = {}
    if path.exists():
        with path.open(encoding="utf-8") as cache_file:
            cache = json.load(cache_file)
    if not isinstance(cache, dict):
        raise ValueError("Merchant cache must contain a JSON object.")

    cache[normalized_description] = approved_merchant
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return normalized_description