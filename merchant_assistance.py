"""Optional local Ollama merchant suggestions and explicit cache approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from cleaning_logic import clean_description_for_matching

MAX_OLLAMA_SUGGESTION_ATTEMPTS = 5


def _request_ollama_json(prompt: str, model: str) -> dict:
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
                "content": prompt,
            }
        ],
    )
    message = response.get("message", {}) if isinstance(response, dict) else response.message
    content = message.get("content", "") if isinstance(message, dict) else message.content
    try:
        payload = json.loads(content)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("Ollama returned invalid JSON.") from error
    if not isinstance(payload, dict):
        raise ValueError("Ollama must return a JSON object.")
    return payload


def suggest_merchant_with_ollama(
    description: str,
    *,
    model: str = "gemma3:4b",
) -> str:
    """Ask the locally running Ollama model for a merchant name suggestion."""
    payload = _request_ollama_json(
        "Identify the merchant in this bank transaction description. "
        "Return only a JSON object with one string field named merchant. "
        "Do not infer a category or invent a merchant. If uncertain, use "
        f"the cleaned description.\n\nDescription: {description}",
        model,
    )
    merchant = payload.get("merchant") if isinstance(payload, dict) else None
    if not isinstance(merchant, str) or not merchant.strip():
        raise ValueError("Ollama did not return a merchant name.")
    return merchant.strip()


def suggest_merchant_and_category_with_ollama(
    description: str,
    amount: float,
    category_subcategories: Mapping[str, Sequence[str]],
    *,
    model: str = "gemma3:4b",
) -> dict[str, str]:
    """Suggest a merchant and valid category, retrying malformed output up to five times."""
    valid_categories = {
        main_category: list(subcategories)
        for main_category, subcategories in category_subcategories.items()
    }
    base_prompt = (
        "Identify the most likely merchant and categorize this bank transaction. "
        "Return only a JSON object with string fields merchant, main_category, and sub_category. "
        "Choose the category pair only from the allowed choices below. Use the description, "
        "merchant, and signed amount. Negative amounts are spending; positive amounts are income. "
        "If uncertain, choose General Spending / Other for spending or Income / Other Income for income.\n\n"
        f"Description: {description}\nAmount: {amount}\n"
        f"Allowed category pairs: {json.dumps(valid_categories, ensure_ascii=True)}"
    )
    fallback_main, fallback_sub = (
        ("General Spending", "Other") if amount <= 0 else ("Income", "Other Income")
    )
    last_merchant = ""
    last_error = "the response was incomplete"

    for attempt in range(MAX_OLLAMA_SUGGESTION_ATTEMPTS):
        prompt = base_prompt
        if attempt:
            prompt = (
                f"Your previous response was invalid because {last_error}. "
                "Try again. Return the required JSON fields and copy a category pair exactly "
                "from the allowed choices.\n\n"
                + base_prompt
            )
        try:
            payload = _request_ollama_json(prompt, model)
        except ValueError as error:
            last_error = str(error)
            continue

        merchant = payload.get("merchant")
        if isinstance(merchant, str) and merchant.strip():
            last_merchant = merchant.strip()
        main_category = payload.get("main_category")
        sub_category = payload.get("sub_category")
        category_is_valid = (
            isinstance(main_category, str)
            and main_category in valid_categories
            and isinstance(sub_category, str)
            and sub_category in valid_categories[main_category]
        )
        if not last_merchant:
            last_error = "the merchant field was missing or blank"
        elif category_is_valid:
            return {
                "merchant": last_merchant,
                "main_category": main_category,
                "sub_category": sub_category,
            }
        else:
            last_error = "the category pair was missing or outside the allowed choices"

    if not last_merchant:
        raise ValueError(
            f"Ollama could not identify a merchant after {MAX_OLLAMA_SUGGESTION_ATTEMPTS} attempts. "
            "Check that Ollama is running, then press Suggest merchant with Ollama to try again."
        )

    return {
        "merchant": last_merchant,
        "main_category": fallback_main,
        "sub_category": fallback_sub,
        "category_warning": (
            f"Ollama returned an invalid category after {MAX_OLLAMA_SUGGESTION_ATTEMPTS} attempts. "
            "A sign-based fallback is selected; "
            "review the category before approving."
        ),
    }


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