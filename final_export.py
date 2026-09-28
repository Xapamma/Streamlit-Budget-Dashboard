"""Convert standardized transactions to the project's categorized CSV format."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

import pandas as pd
from rapidfuzz import fuzz, process

from cleaning_logic import (
    add_categories,
    clean_description_for_matching,
    description_merchants,
    match_description_map,
    merchants_list,
    smart_title,
)


FINAL_EXPORT_COLUMNS = [
    "date",
    "description",
    "merchant",
    "type",
    "amount",
    "main_category",
    "sub_category",
    "bank",
    "account",
]
EXCLUDED_DESCRIPTION_FRAGMENTS = (
    "transfer to sofi",
    "to checking",
    "angel funding",
    "roundup",
    "home banking transfer",
    "pymt",
    "payment to",
    "north capital",
    "internal transfer",
)


def _normalize_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def normalize_account_type(value: str | None) -> str:
    """Normalize account labels such as ``CC`` to the established output value."""
    if not value:
        return "unknown"

    normalized = _normalize_label(value)
    known_types = {
        "cc": "credit card",
        "credit": "credit card",
        "creditcard": "credit card",
        "checking": "checking",
        "savings": "savings",
        "imm": "money market",
        "mm": "money market",
        "moneymarket": "money market",
        "gold": "gold account",
        "goldaccount": "gold account",
    }
    return known_types.get(normalized, value.strip().casefold())


def normalize_bank_name(value: str) -> str:
    """Use the bank labels already present in the example categorized CSV."""
    normalized = _normalize_label(value)
    known_banks = {
        "capitalone": "capital one",
        "gwcu": "goldenwest",
        "goldenwest": "goldenwest",
        "goldenwestcreditunion": "goldenwest",
        "sofi": "sofi",
    }
    return known_banks.get(normalized, value.strip().casefold())


def _load_merchant_cache(cache_path: str | Path | None) -> Mapping[str, str]:
    path = Path(cache_path) if cache_path is not None else Path(__file__).with_name("merchant_cache.json")
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as cache_file:
        cache = json.load(cache_file)
    if not isinstance(cache, dict):
        raise ValueError("Merchant cache must contain a JSON object.")
    return cache


def _fuzzy_match(description: str, choices: list[str]) -> tuple[str | None, float]:
    if not choices:
        return None, 0

    result = process.extractOne(description, choices, scorer=fuzz.token_set_ratio)
    if result is None:
        return None, 0

    match, score, _ = result
    if score < 90 or (len(match.split()) == 1 and score < 97):
        return None, score
    if not any(word in description for word in match.lower().split()):
        return None, score

    description_words = set(description.lower().split())
    match_words = set(match.lower().split())
    if len(description_words & match_words) < 2:
        return None, score
    return match, score


def _merchant_from_description(description: str, merchant_cache: Mapping[str, str]) -> str:
    cleaned = clean_description_for_matching(description)
    known_merchant = match_description_map(cleaned)
    if known_merchant:
        return known_merchant
    if cleaned in merchant_cache:
        return merchant_cache[cleaned]

    merchant_match, merchant_score = _fuzzy_match(cleaned, merchants_list)
    cache_match, cache_score = _fuzzy_match(cleaned, list(merchant_cache))
    description_match, description_score = _fuzzy_match(cleaned, description_merchants)
    if merchant_score > 96:
        return merchant_match or smart_title(description)
    if description_score >= 96:
        return description_match or smart_title(description)
    if cache_score > 96:
        return cache_match or smart_title(description)
    return smart_title(description)


def format_final_export(
    transactions: pd.DataFrame,
    *,
    merchant_cache_path: str | Path | None = None,
) -> pd.DataFrame:
    """Create the same nine-column categorized schema as the example CSV.

    Known merchants are identified using the existing description rules and
    merchant cache. Unknown descriptions remain as the merchant label so they
    can be reviewed and categorized later; this function never calls an LLM.
    """
    required_columns = {"date", "description", "amount", "transaction_type", "bank", "account"}
    missing_columns = required_columns - set(transactions.columns)
    if missing_columns:
        raise ValueError(f"Missing standardized transaction columns: {sorted(missing_columns)}.")

    descriptions = transactions["description"].astype("string")
    excluded = pd.Series(False, index=transactions.index)
    for fragment in EXCLUDED_DESCRIPTION_FRAGMENTS:
        excluded |= descriptions.str.contains(fragment, case=False, regex=False, na=False)

    merchant_cache = _load_merchant_cache(merchant_cache_path)
    result = transactions.loc[~excluded].copy()
    if "merchant" not in result:
        result["merchant"] = result["description"].map(
            lambda description: _merchant_from_description(str(description), merchant_cache)
        )
    else:
        missing_merchants = result["merchant"].isna() | result["merchant"].astype("string").str.strip().eq("")
        result.loc[missing_merchants, "merchant"] = result.loc[
            missing_merchants, "description"
        ].map(lambda description: _merchant_from_description(str(description), merchant_cache))

    result["type"] = result["transaction_type"]
    result["bank"] = result["bank"].map(normalize_bank_name)
    result["account"] = result["account"].map(normalize_account_type)
    result = add_categories(result)
    result["date"] = pd.to_datetime(result["date"], errors="raise")
    result = result.sort_values("date", kind="stable")
    result["date"] = result["date"].dt.strftime("%m/%d/%Y")
    return result[FINAL_EXPORT_COLUMNS].copy()