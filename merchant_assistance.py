"""AI merchant suggestions (via the user's selected provider) and app help.

The merchant/category master mapping files are treated as read-only seed data.
This module never writes them from the app; suggestions apply only to the
current Streamlit session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping, Sequence

from ai_providers import AISettings, chat, chat_json

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
        "only when no listed spending category is supported; every suggestion will be reviewed before applying.\n\n"
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


def suggest_merchant_categories_batch(
    transactions: Sequence[Mapping[str, str | float]],
    category_subcategories: Mapping[str, Sequence[str]],
    *,
    settings: AISettings | None,
) -> dict[str, dict[str, str]]:
    """Return validated suggestions for a batch of transactions in one provider request."""
    if not transactions:
        raise ValueError("At least one transaction is required for a category suggestion.")

    transaction_ids: set[str] = set()
    prompt_transactions = []
    for transaction in transactions:
        transaction_id = str(transaction.get("id", "")).strip()
        description = str(transaction.get("description", "")).strip()
        amount = transaction.get("amount")
        if not transaction_id or transaction_id in transaction_ids:
            raise ValueError("Each transaction must have a unique, non-empty ID.")
        if not description:
            raise ValueError("Each transaction must have a non-empty description.")
        if not isinstance(amount, (int, float)):
            raise ValueError("Each transaction must have a numeric amount.")
        transaction_ids.add(transaction_id)
        prompt_transactions.append(
            {"id": transaction_id, "description": description, "amount": amount}
        )

    valid_categories = {
        main_category: list(subcategories)
        for main_category, subcategories in category_subcategories.items()
    }
    prompt = (
        "Categorize every bank transaction below and identify its most likely merchant. "
        "Return only one JSON object with a suggestions array. Each array item must contain "
        "the exact input id and string fields merchant, main_category, and sub_category. "
        "Return exactly one item for every input transaction, without omissions or duplicates. "
        "Choose category pairs only from the allowed choices. Negative amounts are spending; "
        "positive amounts are income. Use the most specific category supported by the details. "
        "Use General Spending / Other only when no listed spending category is supported.\n\n"
        f"Transactions: {json.dumps(prompt_transactions, ensure_ascii=True)}\n"
        f"Allowed category pairs: {json.dumps(valid_categories, ensure_ascii=True)}"
    )
    payload = chat_json(settings, prompt)
    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list):
        raise ValueError("The AI response must contain a suggestions array.")

    results: dict[str, dict[str, str]] = {}
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            raise ValueError("Each AI suggestion must be a JSON object.")
        transaction_id = suggestion.get("id")
        merchant = suggestion.get("merchant")
        main_category = suggestion.get("main_category")
        sub_category = suggestion.get("sub_category")
        if (
            not isinstance(transaction_id, str)
            or transaction_id not in transaction_ids
            or transaction_id in results
        ):
            raise ValueError("The AI returned an unknown or duplicate transaction ID.")
        if not isinstance(merchant, str) or not merchant.strip():
            raise ValueError("The AI returned a suggestion without a merchant.")
        if (
            not isinstance(main_category, str)
            or main_category not in valid_categories
            or not isinstance(sub_category, str)
            or sub_category not in valid_categories[main_category]
        ):
            raise ValueError("The AI returned a category outside the allowed choices.")
        results[transaction_id] = {
            "merchant": merchant.strip(),
            "main_category": main_category,
            "sub_category": sub_category,
        }

    if set(results) != transaction_ids:
        raise ValueError("The AI did not return a suggestion for every transaction.")
    return results


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
Import defaults for built-in or custom banks come from read-only seed formats; the app
never saves formats or mappings from the UI.

Review: edit the transaction preview, search is case-insensitive and typo-tolerant,
and hidden search results are still included in the export. The preview and download are
sorted oldest to newest by transaction date, including after a date edit; invalid dates
stay at the bottom for correction. Check dates, signs, amounts,
merchants, and categories. For a transfer not caught by the rules, set its main category
to Transfer; the app fills N/A and moves it to the excluded review list. Excluded transfers,
payments, and declined transactions can be reviewed, and valid excluded transactions can
be restored. Fix validation issues before downloading.

Categories: known merchant rules and the read-only master merchant mapping are applied first.
Unresolved transactions can be sent together to the user-selected AI provider with one request.
Review each suggestion individually, edit its merchant and category, then apply the choice
to that transaction. Changes stay in the current session and are never saved to the master
merchant/category mapping; the user must download the CSV to keep their edits.

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