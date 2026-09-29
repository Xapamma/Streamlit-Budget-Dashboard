"""Load bank CSV exports into a validated, consistent transaction table."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping

import pandas as pd


class ImportFormatError(ValueError):
    """Raised when a bank CSV cannot be safely converted to the standard schema."""


def build_import_signature(
    content_digest: str,
    *,
    bank: str,
    account: str,
    column_mapping: Mapping[str, str],
    delimiter: str,
    decimal: str,
    thousands: str | None,
    positive_amounts_are_debits: bool,
) -> str:
    """Hash the file contents and every option that changes its interpretation."""
    settings = {
        "content_digest": content_digest,
        "bank": bank,
        "account": account,
        "column_mapping": dict(column_mapping),
        "delimiter": delimiter,
        "decimal": decimal,
        "thousands": thousands,
        "positive_amounts_are_debits": positive_amounts_are_debits,
    }
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


STANDARD_COLUMNS = [
    "transaction_id",
    "date",
    "posted_date",
    "description",
    "amount",
    "transaction_type",
    "bank_category",
    "status",
    "bank",
    "account",
    "currency",
    "source_file",
    "source_row",
]

_ADAPTERS = {
    "capitalone": {
        "bank": "Capital One",
        "columns": {
            "date": "Transaction Date",
            "posted_date": "Posted Date",
            "description": "Description",
            "debit": "Debit",
            "credit": "Credit",
            "bank_category": "Category",
        },
    },
    "gwcu": {
        "bank": "Goldenwest Credit Union",
        "columns": {
            "date": "Posting Date",
            "posted_date": "Effective Date",
            "description": "Description",
            "amount": "Amount",
            "transaction_id": "Transaction ID",
            "bank_category": "Type",
        },
    },
    "goldenwest": {
        "bank": "Goldenwest Credit Union",
        "columns": {
            "date": "Posting Date",
            "posted_date": "Effective Date",
            "description": "Description",
            "amount": "Amount",
            "transaction_id": "Transaction ID",
            "bank_category": "Type",
        },
    },
    "goldenwestcreditunion": {
        "bank": "Goldenwest Credit Union",
        "columns": {
            "date": "Posting Date",
            "posted_date": "Effective Date",
            "description": "Description",
            "amount": "Amount",
            "transaction_id": "Transaction ID",
            "bank_category": "Type",
        },
    },
    "sofi": {
        "bank": "SoFi",
        "columns": {
            "date": "Date",
            "description": "Description",
            "amount": "Amount",
            "bank_category": "Type",
        },
    },
}

_CANONICAL_MAPPING_KEYS = {
    "date",
    "posted_date",
    "description",
    "amount",
    "debit",
    "credit",
    "transaction_id",
    "bank_category",
    "status",
    "currency",
}


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _resolve_column(
    headers: list[str], source_name: str, *, required: bool
) -> str | None:
    matches = [header for header in headers if _normalize_name(header) == _normalize_name(source_name)]
    if len(matches) > 1:
        raise ImportFormatError(f"More than one CSV column matches {source_name!r}.")
    if not matches:
        if required:
            raise ImportFormatError(f"Required CSV column {source_name!r} was not found.")
        return None
    return matches[0]


def _parse_amounts(
    values: pd.Series,
    *,
    decimal: str,
    thousands: str | None,
    source_rows: pd.Series,
) -> pd.Series:
    raw = values.astype("string").str.strip()
    is_parenthesized = raw.str.fullmatch(r"\(.*\)", na=False)
    cleaned = raw.str.replace(r"[^0-9,\.\-+]", "", regex=True)
    if thousands:
        cleaned = cleaned.str.replace(thousands, "", regex=False)
    if decimal != ".":
        cleaned = cleaned.str.replace(decimal, ".", regex=False)
    cleaned = cleaned.mask(is_parenthesized, "-" + cleaned.str.strip("()"))
    cleaned = cleaned.mask(raw.eq(""), pd.NA)
    amounts = pd.to_numeric(cleaned, errors="coerce")
    invalid = raw.ne("") & amounts.isna()
    if invalid.any():
        bad_rows = source_rows[invalid].tolist()[:5]
        raise ImportFormatError(f"Invalid amount value on CSV row(s): {bad_rows}.")
    return amounts


def _parse_amounts_for_review(
    values: pd.Series,
    *,
    decimal: str,
    thousands: str | None,
    source_rows: pd.Series,
) -> pd.Series:
    parsed = pd.Series(float("nan"), index=values.index, dtype="float64")
    for index in values.index:
        try:
            parsed.loc[index] = _parse_amounts(
                values.loc[[index]],
                decimal=decimal,
                thousands=thousands,
                source_rows=source_rows.loc[[index]],
            ).iloc[0]
        except ImportFormatError:
            pass
    return parsed


def _infer_account(path: Path) -> str:
    filename = path.stem.casefold()
    patterns = (
        (r"(?:^|[^a-z0-9])(?:credit[ _-]?card|cc)(?:$|[^a-z0-9])", "credit card"),
        (r"(?:^|[^a-z0-9])checking(?:$|[^a-z0-9])", "checking"),
        (r"(?:^|[^a-z0-9])savings(?:$|[^a-z0-9])", "savings"),
        (r"(?:^|[^a-z0-9])gold(?:$|[^a-z0-9])", "gold account"),
        (r"(?:^|[^a-z0-9])imm(?:$|[^a-z0-9])", "money market"),
    )
    for pattern, account in patterns:
        if re.search(pattern, filename):
            return account
    return "unknown"


def load_transactions(
    csv_path: str | Path,
    *,
    bank: str | None = None,
    account: str | None = None,
    currency: str | None = None,
    column_mapping: Mapping[str, str] | None = None,
    decimal: str = ".",
    thousands: str | None = ",",
    delimiter: str = ",",
    positive_amounts_are_debits: bool = False,
) -> pd.DataFrame:
    """Read one bank CSV and return rows using ``STANDARD_COLUMNS``.

    ``column_mapping`` maps canonical field names to CSV headers, for example
    ``{"date": "Activity Date", "description": "Details", "amount": "Value"}``.
    For split debit/credit exports, map both ``debit`` and ``credit`` instead
    of ``amount``. Amounts are standardized so expenses are negative and income
    is positive. Currency is left blank unless provided or present in the CSV.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    if len(decimal) != 1 or (thousands is not None and len(thousands) != 1):
        raise ValueError("decimal and thousands separators must each be one character.")
    if decimal == thousands:
        raise ValueError("decimal and thousands separators must be different.")
    if not delimiter or "\n" in delimiter or "\r" in delimiter:
        raise ValueError("delimiter must be a non-empty character or string.")

    try:
        raw = pd.read_csv(
            path,
            dtype="string",
            encoding="utf-8-sig",
            keep_default_na=False,
            sep=delimiter,
        )
    except pd.errors.EmptyDataError as error:
        raise ImportFormatError("The CSV file is empty or has no header row.") from error
    if raw.empty and len(raw.columns) == 0:
        raise ImportFormatError("The CSV file is empty or has no header row.")

    headers = [str(header).strip() for header in raw.columns]
    raw.columns = headers

    requested_bank = bank or path.parent.name
    adapter = _ADAPTERS.get(_normalize_name(requested_bank))
    if column_mapping is not None:
        unknown_keys = set(column_mapping) - _CANONICAL_MAPPING_KEYS
        if unknown_keys:
            raise ImportFormatError(f"Unsupported mapping field(s): {sorted(unknown_keys)}.")
        mappings = dict(column_mapping)
    elif adapter is not None:
        mappings = dict(adapter["columns"])
    else:
        raise ImportFormatError(
            f"No CSV layout is registered for {requested_bank!r}. "
            "Provide column_mapping for this bank."
        )

    if "date" not in mappings or "description" not in mappings:
        raise ImportFormatError("A mapping for both 'date' and 'description' is required.")
    has_amount = "amount" in mappings
    has_split_amounts = "debit" in mappings and "credit" in mappings
    if has_amount == has_split_amounts:
        raise ImportFormatError(
            "Map either one 'amount' column or both 'debit' and 'credit' columns."
        )

    required_fields = {"date", "description"}
    if has_amount:
        required_fields.add("amount")
    else:
        required_fields.update(("debit", "credit"))

    resolved = {
        field: _resolve_column(headers, source, required=field in required_fields)
        for field, source in mappings.items()
    }

    original_source_rows = pd.Series(range(2, len(raw) + 2), index=raw.index)
    declined_rows = pd.Series(False, index=raw.index)
    if resolved.get("status"):
        declined_rows = raw[resolved["status"]].str.contains(
            r"\bdeclined\b", case=False, regex=True, na=False
        )
    declined_raw = raw.loc[declined_rows].copy()
    declined_source_rows = original_source_rows.loc[declined_rows]
    raw = raw.loc[~declined_rows].copy()

    source_rows = pd.Series(raw.index + 2, index=raw.index)
    if has_amount:
        amounts = _parse_amounts(
            raw[resolved["amount"]],
            decimal=decimal,
            thousands=thousands,
            source_rows=source_rows,
        )
    else:
        debit_values = _parse_amounts(
            raw[resolved["debit"]],
            decimal=decimal,
            thousands=thousands,
            source_rows=source_rows,
        )
        credit_values = _parse_amounts(
            raw[resolved["credit"]],
            decimal=decimal,
            thousands=thousands,
            source_rows=source_rows,
        )
        debit_present = debit_values.notna()
        credit_present = credit_values.notna()
        invalid_split = debit_present.eq(credit_present)
        if invalid_split.any():
            bad_rows = source_rows[invalid_split].tolist()[:5]
            raise ImportFormatError(
                "Each split-amount row must have exactly one debit or credit value; "
                f"check CSV row(s): {bad_rows}."
            )
        amounts = -debit_values.abs().fillna(0) + credit_values.abs().fillna(0)

    if positive_amounts_are_debits:
        amounts = -amounts

    if has_amount:
        declined_amounts = _parse_amounts_for_review(
            declined_raw[resolved["amount"]],
            decimal=decimal,
            thousands=thousands,
            source_rows=declined_source_rows,
        )
    else:
        declined_debits = _parse_amounts_for_review(
            declined_raw[resolved["debit"]],
            decimal=decimal,
            thousands=thousands,
            source_rows=declined_source_rows,
        )
        declined_credits = _parse_amounts_for_review(
            declined_raw[resolved["credit"]],
            decimal=decimal,
            thousands=thousands,
            source_rows=declined_source_rows,
        )
        valid_split = declined_debits.notna() ^ declined_credits.notna()
        declined_amounts = (
            -declined_debits.abs().fillna(0) + declined_credits.abs().fillna(0)
        ).where(valid_split)
    if positive_amounts_are_debits:
        declined_amounts = -declined_amounts

    date_values = pd.to_datetime(raw[resolved["date"]].str.strip(), errors="coerce")
    invalid_dates = date_values.isna()
    if invalid_dates.any():
        bad_rows = source_rows[invalid_dates].tolist()[:5]
        raise ImportFormatError(f"Missing or invalid date on CSV row(s): {bad_rows}.")

    descriptions = raw[resolved["description"]].str.strip()
    invalid_descriptions = descriptions.eq("")
    if invalid_descriptions.any():
        bad_rows = source_rows[invalid_descriptions].tolist()[:5]
        raise ImportFormatError(f"Missing description on CSV row(s): {bad_rows}.")

    normalized_bank = adapter["bank"] if adapter is not None else requested_bank
    declined_review = pd.DataFrame(index=declined_raw.index)
    declined_review["transaction_id"] = (
        declined_raw[resolved["transaction_id"]].replace("", pd.NA)
        if resolved.get("transaction_id")
        else pd.NA
    )
    declined_review["date"] = pd.to_datetime(
        declined_raw[resolved["date"]].str.strip(), errors="coerce"
    )
    declined_review["posted_date"] = (
        pd.to_datetime(declined_raw[resolved["posted_date"]].str.strip(), errors="coerce")
        if resolved.get("posted_date")
        else pd.NaT
    )
    declined_review["description"] = declined_raw[resolved["description"]].str.strip()
    declined_review["amount"] = declined_amounts.astype("float64")
    declined_review["transaction_type"] = declined_amounts.map(
        lambda amount: "credit" if amount > 0 else "debit" if amount < 0 else "unknown"
    )
    declined_review["bank_category"] = (
        declined_raw[resolved["bank_category"]].replace("", pd.NA)
        if resolved.get("bank_category")
        else pd.NA
    )
    declined_review["status"] = (
        declined_raw[resolved["status"]].replace("", pd.NA)
        if resolved.get("status")
        else pd.NA
    )
    declined_review["bank"] = normalized_bank
    declined_review["account"] = account or _infer_account(path)
    declined_review["currency"] = (
        declined_raw[resolved["currency"]].replace("", pd.NA)
        if resolved.get("currency")
        else currency
    )
    declined_review["source_file"] = path.name
    declined_review["source_row"] = declined_source_rows

    result = pd.DataFrame(index=raw.index)
    result["transaction_id"] = (
        raw[resolved["transaction_id"]].replace("", pd.NA)
        if resolved.get("transaction_id")
        else pd.NA
    )
    result["date"] = date_values
    result["posted_date"] = (
        pd.to_datetime(raw[resolved["posted_date"]].str.strip(), errors="coerce")
        if resolved.get("posted_date")
        else pd.NaT
    )
    result["description"] = descriptions
    result["amount"] = amounts.astype("float64")
    result["transaction_type"] = amounts.map(
        lambda amount: "credit" if amount > 0 else "debit" if amount < 0 else "unknown"
    )
    result["bank_category"] = (
        raw[resolved["bank_category"]].replace("", pd.NA)
        if resolved.get("bank_category")
        else pd.NA
    )
    result["status"] = (
        raw[resolved["status"]].replace("", pd.NA)
        if resolved.get("status")
        else pd.NA
    )
    result["bank"] = normalized_bank
    result["account"] = account or _infer_account(path)
    result["currency"] = (
        raw[resolved["currency"]].replace("", pd.NA)
        if resolved.get("currency")
        else currency
    )
    result["source_file"] = path.name
    result["source_row"] = source_rows

    result = result[STANDARD_COLUMNS].reset_index(drop=True)
    result.attrs["declined_rows_dropped"] = int(declined_rows.sum())
    result.attrs["declined_transactions"] = declined_review[STANDARD_COLUMNS].reset_index(drop=True)
    return result


def load_transaction_files(
    csv_paths: list[str | Path], **options: object
) -> pd.DataFrame:
    """Load and combine multiple files that use the same bank layout/options."""
    if not csv_paths:
        raise ValueError("At least one CSV path is required.")
    frames = [load_transactions(path, **options) for path in csv_paths]
    return pd.concat(frames, ignore_index=True)


def remove_source_rows(
    transactions: pd.DataFrame,
    *,
    source_column: str,
    source_names: set[str],
) -> pd.DataFrame:
    """Remove session rows owned by uploads that are no longer present."""
    if transactions.empty or source_column not in transactions:
        return transactions.copy().reset_index(drop=True)
    return transactions.loc[~transactions[source_column].isin(source_names)].reset_index(drop=True)


def replace_source_rows(
    existing: pd.DataFrame,
    replacement: pd.DataFrame,
    *,
    source_column: str,
    source_name: str,
) -> pd.DataFrame:
    """Replace all prior rows for a filename with its newly processed rows."""
    retained = remove_source_rows(
        existing,
        source_column=source_column,
        source_names={source_name},
    )
    return pd.concat([retained, replacement], ignore_index=True)