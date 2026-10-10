"""AI merchant suggestions (via the user's selected provider) and explicit cache approval."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from ai_providers import AISettings, chat, chat_json
from cleaning_logic import clean_description_for_matching

MAX_SUGGESTION_ATTEMPTS = 5


def suggest_merchant_and_category(
    description: str,
    amount: float,
    category_subcategories: Mapping[str, Sequence[str]],
    *,
    settings: AISettings | None,
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
        "Choose the most specific category supported by the transaction details. Use General Spending / Other "
        "only when no listed spending category is supported; every suggestion will be reviewed before saving.\n\n"
        f"Description: {description}\nAmount: {amount}\n"
        f"Allowed category pairs: {json.dumps(valid_categories, ensure_ascii=True)}"
    )
    fallback_main, fallback_sub = (
        ("General Spending", "Other") if amount <= 0 else ("Income", "Other Income")
    )
    last_merchant = ""
    last_error = "the response was incomplete"

    for attempt in range(MAX_SUGGESTION_ATTEMPTS):
        prompt = base_prompt
        if attempt:
            prompt = (
                f"Your previous response was invalid because {last_error}. "
                "Try again. Return the required JSON fields and copy a category pair exactly "
                "from the allowed choices.\n\n"
                + base_prompt
            )
        try:
            payload = chat_json(settings, prompt)
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
            f"The AI could not identify a merchant after {MAX_SUGGESTION_ATTEMPTS} attempts. "
            "Check your AI settings, then press Suggest merchant to try again."
        )

    return {
        "merchant": last_merchant,
        "main_category": fallback_main,
        "sub_category": fallback_sub,
        "category_warning": (
            f"The AI returned an invalid category after {MAX_SUGGESTION_ATTEMPTS} attempts. "
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


APP_HELP_GUIDE = """You help people use a local Streamlit bank-statement importer.
Answer only questions about this app and its workflow. Be concise, friendly, and give
clear numbered steps when useful. If the guide does not answer a question, say so
instead of inventing behavior.

Workflow: upload one or more bank CSV files; select the statement, bank, and account;
map date, description, and amount (or debit and credit) columns; choose amount sign,
delimiter, and number format; optionally map transaction status; then standardize.
The bank-format expander saves reusable local defaults for built-in or custom banks.

Review: edit the transaction preview, search is case-insensitive and typo-tolerant,
and hidden search results are still included in the export. The preview and download are
sorted oldest to newest by transaction date, including after a date edit; invalid dates
stay at the bottom for correction. Check dates, signs, amounts,
merchants, and categories. For a transfer not caught by the rules, set its main category
to Transfer; the app fills N/A and moves it to the excluded review list. Excluded transfers,
payments, and declined transactions can be reviewed, and valid excluded transactions can
be restored. Fix validation issues before downloading.

Categories: known merchant rules and the local merchant cache are applied first.
Unresolved merchants can be sent to the user-selected AI provider only when the user requests a
suggestion. The merchant and category remain editable; only approval updates the
preview and cache. The cache editor supports search, add, edit, delete, and save.

Budgeting: choose a month, then enter income line by line under Income: Paychecks,
Dividends, Refunds, CC Rewards, Other Income, and Savings / Other Withdrawals. The total
income and unallocated amount update automatically. Categorized transactions for the
selected month prefill missing targets, but existing limits and removed lines are preserved.
A main-category limit rises automatically when subcategory plans or selected-month spending
exceed it. Use the savings allocation button to assign positive remaining income to Savings
& Investments; the page warns when the overall plan is above or below income. Budgets are
separate for each selected month.

Privacy: this help chat receives only the user's question and recent help-chat turns.
It cannot see uploaded files, transactions, or the merchant cache. Do not ask users to
paste account numbers, transaction descriptions, or other financial details. The chat
and its history are kept only in the current Streamlit session."""


def ask_app_help(
    question: str,
    history: Sequence[Mapping[str, str]] = (),
    *,
    settings: AISettings | None,
) -> str:
    """Answer an app-usage question without including transaction data."""
    clean_question = question.strip()
    if not clean_question:
        raise ValueError("Enter a question about using the app.")

    messages = [{"role": "system", "content": APP_HELP_GUIDE}]
    for message in history[-8:]:
        role = message.get("role")
        content = message.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": clean_question})

    return chat(settings, messages).strip()


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