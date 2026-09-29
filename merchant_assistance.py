"""Optional local Ollama merchant suggestions and explicit cache approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

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
    cache = load_merchant_cache(path)
    cache[normalized_description] = approved_merchant
    save_merchant_cache(cache, path)
    return normalized_description


def load_merchant_cache(cache_path: str | Path | None = None) -> dict[str, str]:
    path = Path(cache_path) if cache_path is not None else Path(__file__).with_name("merchant_cache.json")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as cache_file:
        cache = json.load(cache_file)
    if not isinstance(cache, dict):
        raise ValueError("Merchant cache must contain a JSON object.")
    return {str(key): str(value) for key, value in cache.items()}


def save_merchant_cache(
    cache: Mapping[str, str],
    cache_path: str | Path | None = None,
) -> None:
    """Replace the local cache after validating editable keys and merchant names."""
    cleaned = {}
    for key, value in cache.items():
        description_key = str(key).strip()
        merchant = str(value).strip()
        if not description_key or not merchant:
            raise ValueError("Cache descriptions and merchant names cannot be blank.")
        if description_key in cleaned:
            raise ValueError(f"Duplicate cache description: {description_key}.")
        cleaned[description_key] = merchant

    path = Path(cache_path) if cache_path is not None else Path(__file__).with_name("merchant_cache.json")
    path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def merge_merchant_cache_edits(
    existing: Mapping[str, str],
    replaced_keys: set[str],
    edited_entries: list[tuple[str, str]],
) -> dict[str, str]:
    """Apply edits to visible cache keys while preserving every hidden entry."""
    revised = {key: value for key, value in existing.items() if key not in replaced_keys}
    for raw_key, raw_merchant in edited_entries:
        key = clean_description_for_matching(raw_key)
        merchant = raw_merchant.strip()
        if not key and not merchant:
            continue
        if not key or not merchant:
            raise ValueError("Each cache row needs both a description key and a merchant.")
        if key in revised:
            raise ValueError(f"The normalized description '{key}' appears more than once.")
        revised[key] = merchant
    return revised