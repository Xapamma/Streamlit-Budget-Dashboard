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
    category_hierarchy,
    clean_description_for_matching,
    description_merchants,
    match_description_map,
    merchants_list,
    smart_title,
    sub_category_map,
)
from transaction_import import STANDARD_COLUMNS


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
_HIERARCHY_SUBCATEGORIES = {
    subcategory
    for subcategories in category_hierarchy.values()
    for subcategory in subcategories
}
_FALLBACK_SUBCATEGORIES = sorted((set(sub_category_map.values()) - _HIERARCHY_SUBCATEGORIES) | {"Other"})
CATEGORY_SUBCATEGORIES = {
    **category_hierarchy,
    "General Spending": _FALLBACK_SUBCATEGORIES,
    "Other Income": _FALLBACK_SUBCATEGORIES,
}
FINAL_MAIN_CATEGORY_OPTIONS = sorted(CATEGORY_SUBCATEGORIES)
CATEGORY_PAIR_TO_LABEL = {
    (main_category, sub_category): f"{main_category} :: {sub_category}"
    for main_category, subcategories in CATEGORY_SUBCATEGORIES.items()
    for sub_category in subcategories
}
CATEGORY_LABEL_TO_PAIR = {label: pair for pair, label in CATEGORY_PAIR_TO_LABEL.items()}
CATEGORY_PAIR_OPTIONS = sorted(CATEGORY_LABEL_TO_PAIR)
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
PAYMENT_LABEL_PATTERN = re.compile(r"\b(?:payments?|pymt|pmt)\b", re.IGNORECASE)


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


def normalize_export_filename(value: str) -> str:
    """Return a safe basename with a CSV extension for the download."""
    filename = Path(value.strip()).name or "all_banks_final_categorized.csv"
    path = Path(filename)
    if path.suffix.casefold() != ".csv":
        filename = path.with_suffix(".csv").name if path.suffix else f"{filename}.csv"
    return filename


def validate_final_export_rows(
    transactions: pd.DataFrame,
) -> tuple[pd.DataFrame, str | None]:
    """Drop fully blank editor rows and validate rows before CSV download."""
    missing_columns = set(FINAL_EXPORT_COLUMNS) - set(transactions.columns)
    if missing_columns:
        raise ValueError(f"Missing final export columns: {sorted(missing_columns)}.")

    result = transactions[FINAL_EXPORT_COLUMNS].copy()
    if result.empty:
        return result, "Add at least one complete transaction before downloading."

    blank_rows = result.astype("string").apply(
        lambda column: column.str.strip().eq("").fillna(True)
    ).all(axis=1)
    result = result.loc[~blank_rows].copy()
    if result.empty:
        return result, "Add at least one complete transaction before downloading."

    problems = []
    for column in FINAL_EXPORT_COLUMNS:
        values = result[column].astype("string").str.strip()
        if column == "amount":
            amounts = pd.to_numeric(result[column], errors="coerce")
            if amounts.isna().any():
                problems.append("Amount must be a valid number on every row.")
            else:
                result[column] = amounts
        elif column == "date":
            dates = pd.to_datetime(values, format="%m/%d/%Y", errors="coerce")
            if dates.isna().any():
                problems.append("Date must use MM/DD/YYYY on every row.")
            else:
                result[column] = dates.dt.strftime("%m/%d/%Y")
        elif values.isna().any() or values.eq("").any():
            problems.append(f"{column.replace('_', ' ').title()} is required on every row.")

    if problems:
        return result, " ".join(problems)

    invalid_types = ~result["type"].isin(["debit", "credit"])
    if invalid_types.any():
        problems.append("Type must be debit or credit on every row.")

    invalid_categories = result.apply(
        lambda row: row["sub_category"] not in CATEGORY_SUBCATEGORIES.get(row["main_category"], []),
        axis=1,
    )
    if invalid_categories.any():
        problems.append("Each sub-category must belong to its selected main category.")

    if problems:
        return result, " ".join(problems)
    return result.reset_index(drop=True), None


def get_final_export_row_issues(transactions: pd.DataFrame) -> dict[int, list[str]]:
    """Return validation issues keyed by the row's current zero-based index."""
    issues_by_row: dict[int, list[str]] = {}

    def blank(value: object) -> bool:
        return pd.isna(value) or not str(value).strip()

    for index, row in transactions.iterrows():
        if all(blank(row[column]) for column in FINAL_EXPORT_COLUMNS):
            continue

        issues = []
        date_value = row["date"]
        if blank(date_value) or pd.isna(
            pd.to_datetime(str(date_value).strip(), format="%m/%d/%Y", errors="coerce")
        ):
            issues.append("Date must use MM/DD/YYYY.")

        amount = pd.to_numeric(pd.Series([row["amount"]]), errors="coerce").iloc[0]
        if pd.isna(amount):
            issues.append("Amount must be a valid number.")

        for column in (
            "description", "merchant", "type", "main_category", "sub_category", "bank", "account"
        ):
            if blank(row[column]):
                issues.append(f"{column.replace('_', ' ').title()} is required.")

        if not blank(row["type"]) and row["type"] not in ("debit", "credit"):
            issues.append("Type must be debit or credit.")

        if not blank(row["main_category"]) and not blank(row["sub_category"]):
            if row["sub_category"] not in CATEGORY_SUBCATEGORIES.get(row["main_category"], []):
                issues.append("Sub-category does not belong to the selected main category.")

        if issues:
            issues_by_row[int(index)] = issues
    return issues_by_row


def restored_rows_to_standardized(
    transactions: pd.DataFrame,
    *,
    statuses: pd.Series,
    source_files: pd.Series,
    source_rows: pd.Series,
) -> tuple[pd.DataFrame, str | None]:
    """Validate reviewed rows and convert them back to importer schema."""
    validated, error = validate_final_export_rows(transactions)
    if error:
        return pd.DataFrame(columns=STANDARD_COLUMNS), error

    restored = pd.DataFrame(index=range(len(validated)), columns=STANDARD_COLUMNS)
    restored["transaction_id"] = pd.NA
    restored["date"] = pd.to_datetime(validated["date"], format="%m/%d/%Y", errors="raise").reset_index(drop=True)
    restored["posted_date"] = pd.NaT
    restored["description"] = validated["description"].reset_index(drop=True)
    restored["amount"] = validated["amount"].reset_index(drop=True).astype("float64")
    restored["transaction_type"] = validated["type"].reset_index(drop=True)
    restored["bank_category"] = pd.NA
    restored["status"] = statuses.reset_index(drop=True)
    restored["bank"] = validated["bank"].reset_index(drop=True)
    restored["account"] = validated["account"].reset_index(drop=True)
    restored["currency"] = pd.NA
    restored["source_file"] = source_files.reset_index(drop=True)
    restored["source_row"] = source_rows.reset_index(drop=True)
    return restored, None


def complete_missing_categories(transactions: pd.DataFrame) -> pd.DataFrame:
    """Fill only blank category cells using the existing merchant rules."""
    result = transactions.copy()
    for index, row in result.iterrows():
        missing_main = pd.isna(row["main_category"]) or not str(row["main_category"]).strip()
        missing_sub = pd.isna(row["sub_category"]) or not str(row["sub_category"]).strip()
        if not (missing_main or missing_sub):
            continue
        if pd.isna(row["merchant"]) or not str(row["merchant"]).strip():
            continue
        one_transaction = pd.DataFrame(
            [{
                "merchant": row["merchant"],
                "amount": row["amount"],
                "description": row["description"],
            }]
        )
        categorized = add_categories(one_transaction).iloc[0]
        if missing_main:
            result.at[index, "main_category"] = categorized["main_category"]
        if missing_sub:
            result.at[index, "sub_category"] = categorized["sub_category"]
    return result


def get_exclusion_reason(transaction: pd.Series) -> str | None:
    """Explain why a transaction is omitted from spending totals/export."""
    status_value = transaction.get("status", "")
    status = "" if pd.isna(status_value) else str(status_value)
    if re.search(r"\bdeclined\b", status, re.IGNORECASE):
        return "Bank status is Declined."

    description_value = transaction.get("description", "")
    description = "" if pd.isna(description_value) else str(description_value)
    for fragment in EXCLUDED_DESCRIPTION_FRAGMENTS:
        if fragment in description.casefold():
            return f"Description matches the existing exclusion rule: {fragment}."

    category_value = transaction.get("bank_category", "")
    bank_category = "" if pd.isna(category_value) else str(category_value)
    if PAYMENT_LABEL_PATTERN.search(bank_category):
        return f"Bank category indicates a payment: {bank_category}."

    known_merchant = match_description_map(clean_description_for_matching(description))
    if not known_merchant and PAYMENT_LABEL_PATTERN.search(description):
        return "Description contains a payment label, likely a card-payment transfer."
    return None


def split_filtered_transactions(
    transactions: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Separate exportable rows from rows excluded by transaction rules."""
    reasons = transactions.apply(get_exclusion_reason, axis=1)
    excluded_mask = reasons.notna()
    included = transactions.loc[~excluded_mask].copy()
    excluded = transactions.loc[excluded_mask].copy()
    excluded["removal_reason"] = reasons.loc[excluded_mask]
    return included, excluded


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
    apply_exclusions: bool = True,
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

    merchant_cache = _load_merchant_cache(merchant_cache_path)
    if apply_exclusions:
        result, _ = split_filtered_transactions(transactions)
    else:
        result = transactions.copy()
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
    if result.empty:
        result["main_category"] = pd.Series(index=result.index, dtype="string")
        result["sub_category"] = pd.Series(index=result.index, dtype="string")
        result["date"] = pd.to_datetime(result["date"], errors="raise").dt.strftime("%m/%d/%Y")
        return result[FINAL_EXPORT_COLUMNS].copy()

    result = add_categories(result)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result = result.sort_values("date", kind="stable")
    result["date"] = result["date"].dt.strftime("%m/%d/%Y")
    return result[FINAL_EXPORT_COLUMNS].copy()