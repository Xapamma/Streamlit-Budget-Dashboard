"""Guided Streamlit interface for standardizing bank statement CSVs."""

from __future__ import annotations

import csv
import hashlib
import importlib
import io
import traceback
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

from bank_profiles import load_bank_profiles
from dashboard_data import sort_rows_by_date
from cleaning_logic import clean_description_for_matching

import final_export

if not all(
    hasattr(final_export, helper_name)
    for helper_name in ("split_duplicate_transactions",)
):
    importlib.reload(final_export)

from final_export import (
    CATEGORY_SUBCATEGORIES,
    CATEGORY_LABEL_TO_PAIR,
    CATEGORY_PAIR_OPTIONS,
    CATEGORY_PAIR_TO_LABEL,
    FINAL_EXPORT_COLUMNS,
    FINAL_MAIN_CATEGORY_OPTIONS,
    complete_missing_categories,
    edited_row_problem,
    format_edit_problem_counts,
    format_final_export,
    get_final_export_row_issues,
    load_category_cache,
    load_merchant_cache,
    normalize_account_type,
    normalize_bank_name,
    normalize_export_filename,
    make_transfer_review_rows,
    restored_rows_to_standardized,
    split_duplicate_transactions,
    split_filtered_transactions,
    validate_final_export_rows,
)
from cleaning_logic import clean_description_for_matching
from fuzzy_search import fuzzy_match_indices
from transaction_import import (
    ImportFormatError,
    STANDARD_COLUMNS,
    build_import_signature,
    load_transactions,
    remove_source_rows,
    replace_source_rows,
)


BANK_OPTIONS = [
    "Auto-detect from filename",
    "Capital One",
    "Goldenwest Credit Union",
    "SoFi",
    "US Bank",
    "Robinhood",
    "Other / new bank",
]
# Placeholder labels used in the one-by-one flagged-transaction review flow.
# Categories start blank so a reviewer must explicitly choose them instead of
# silently accepting the first dropdown option.
MAIN_CATEGORY_PLACEHOLDER = "Select a main category…"
SUB_CATEGORY_PLACEHOLDER = "Select a sub-category…"
ACCOUNT_OPTIONS = [
    "Select an account type",
    "Checking",
    "Savings",
    "Credit card",
    "Money market",
    "Gold account",
    "Other",
]
DELIMITER_OPTIONS = {
    "Auto-detect": None,
    "Comma (,)": ",",
    "Semicolon (;)": ";",
    "Tab": "\t",
    "Pipe (|)": "|",
}
NUMBER_FORMATS = {
    "1,234.56 (US/UK)": (".", ","),
    "1.234,56 (many European countries)": (",", "."),
    "1 234,56 (space thousands)": (",", " "),
    "1,234 (decimal comma, no thousands separator)": (",", None),
}
DROPPED_REVIEW_COLUMNS = FINAL_EXPORT_COLUMNS + [
    "removal_reason",
    "status",
    "source_file",
    "source_row",
    "restore",
]
EDITABLE_SOURCE_COLUMNS = ["_source_file", "_source_row", "_category_reviewed"]
EDITABLE_COLUMNS = FINAL_EXPORT_COLUMNS + EDITABLE_SOURCE_COLUMNS
EDIT_APPLY_ALL = "All matching"
EDIT_APPLY_ONLY_THIS_ROW = "Only this row"


def _shorten_cell(value: object, limit: int) -> str:
    """Return a truncated display string with an ellipsis for long values."""
    text = "" if value is None or pd.isna(value) else str(value)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def _record_table_view(click_key: str) -> None:
    """ButtonColumn callback: remember the clicked row's full details."""
    click = st.session_state.get(click_key)
    lookup = st.session_state.get("_table_view_full")
    if click is None or lookup is None:
        return
    row = int(click.get("row", 0)) if hasattr(click, "get") else int(getattr(click, "row", 0))
    if row < len(lookup):
        st.session_state["_table_viewed"] = {
            "row": row,
            "date": lookup.iloc[row]["date"],
            "description": lookup.iloc[row]["description"],
            "merchant": lookup.iloc[row]["merchant"],
        }


def _show_full_description() -> None:
    """Show the full description/merchant for the clicked table row."""
    _record_table_view("table_view_desc_click")


def _show_full_merchant() -> None:
    """Show the full description/merchant for the clicked table row."""
    _record_table_view("table_view_merch_click")
HEADER_SUGGESTIONS = {
    "date": ("date", "transaction date", "posting date", "effective date", "activity date"),
    "posted_date": ("posted date", "effective date", "date posted", "posting date"),
    "description": ("description", "transaction description", "details", "memo", "name"),
    "amount": ("amount", "transaction amount", "value"),
    "debit": ("debit", "withdrawal", "outflow"),
    "credit": ("credit", "deposit", "inflow"),
    "transaction_id": ("transaction id", "id", "reference number", "reference"),
    "bank_category": ("category", "transaction category", "type"),
    "status": ("status", "transaction status"),
    "currency": ("currency", "currency code"),
}


def normalize_name(value: str) -> str:
    return "".join(character.lower() for character in value if character.isalnum())


def detect_bank(filename: str) -> str:
    normalized = normalize_name(filename)
    for token, bank in (
        ("capitalone", "Capital One"),
        ("gwcu", "Goldenwest Credit Union"),
        ("goldenwest", "Goldenwest Credit Union"),
        ("sofi", "SoFi"),
        ("usbank", "US Bank"),
        ("robinhood", "Robinhood"),
    ):
        if token in normalized:
            return bank
    return "Other / new bank"


def detect_delimiter(content: bytes) -> str:
    sample = content[:8192].decode("utf-8-sig", errors="replace")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def suggested_header(headers: list[str], field: str) -> str | None:
    normalized_headers = {normalize_name(header): header for header in headers}
    for suggestion in HEADER_SUGGESTIONS[field]:
        match = normalized_headers.get(normalize_name(suggestion))
        if match is not None:
            return match
    return None


def column_picker(
    label: str,
    headers: list[str],
    *,
    field: str,
    widget_key: str,
    optional: bool = False,
    default_value: str | None = None,
) -> str | None:
    suggested = suggested_header(headers, field)
    placeholder = "Not used" if optional else "Choose a column"
    options = [placeholder, *headers]
    selected_default = default_value if default_value in headers else suggested
    index = headers.index(selected_default) + 1 if selected_default else 0
    selection = st.selectbox(label, options, index=index, key=widget_key)
    return None if selection == placeholder else selection


def uploaded_csv_preview(content: bytes, delimiter: str) -> pd.DataFrame:
    return pd.read_csv(
        io.BytesIO(content),
        sep=delimiter,
        dtype="string",
        keep_default_na=False,
        nrows=8,
        encoding="utf-8-sig",
    )


def make_dropped_review_rows(
    transactions: pd.DataFrame,
    reasons: list[str] | pd.Series,
) -> pd.DataFrame:
    if transactions.empty:
        return pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)

    source = transactions.copy().sort_values("date", kind="stable")
    reason_values = pd.Series(reasons, index=transactions.index).loc[source.index].tolist()
    source_files = source["source_file"].tolist() if "source_file" in source else [""] * len(source)
    source_rows = source["source_row"].tolist() if "source_row" in source else [pd.NA] * len(source)
    statuses = source["status"].tolist() if "status" in source else [pd.NA] * len(source)
    review = format_final_export(source, apply_exclusions=False)
    review["removal_reason"] = reason_values
    review["status"] = statuses
    review["source_file"] = source_files
    review["source_row"] = source_rows
    review["restore"] = False
    return review[DROPPED_REVIEW_COLUMNS]


st.title("Bank Statement Import")
st.caption("Turn bank CSV exports into one consistent transaction table.")
try:
    bank_profiles = load_bank_profiles()
except (OSError, ValueError) as error:
    bank_profiles = {}
    st.warning(f"Could not load saved bank formats: {error}")

uploaded_files = st.file_uploader(
    "Add bank statement CSV files",
    type=["csv"],
    accept_multiple_files=True,
    help="Files are processed in this app session and are not sent to an external service.",
)

if "standardized_transactions" not in st.session_state:
    st.session_state.standardized_transactions = pd.DataFrame(columns=STANDARD_COLUMNS)
if "file_import_signatures" not in st.session_state:
    st.session_state.file_import_signatures = {}
if "dropped_transactions" not in st.session_state:
    st.session_state.dropped_transactions = pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
if "dropped_editor_revision" not in st.session_state:
    st.session_state.dropped_editor_revision = 0
if "export_editor_revision" not in st.session_state:
    st.session_state.export_editor_revision = 0
if "review_expanded" not in st.session_state:
    st.session_state.review_expanded = False
if "session_merchant_cache" not in st.session_state:
    st.session_state.session_merchant_cache = {}
if "session_category_cache" not in st.session_state:
    st.session_state.session_category_cache = {}
if "editable_transactions" not in st.session_state:
    current_transactions = st.session_state.standardized_transactions
    initial_export = (
        format_final_export(current_transactions)
        if not current_transactions.empty
        else pd.DataFrame(columns=FINAL_EXPORT_COLUMNS)
    )
    initial_export["_source_file"] = ""
    initial_export["_source_row"] = pd.NA
    approved_category_cache = {**load_category_cache(), **st.session_state.session_category_cache}
    initial_export["_category_reviewed"] = [
        main_category != "General Spending"
        or clean_description_for_matching(str(description)) in approved_category_cache
        for main_category, description in zip(
            initial_export["main_category"], initial_export["description"]
        )
    ]
    st.session_state.editable_transactions = initial_export[EDITABLE_COLUMNS]
elif not set(EDITABLE_SOURCE_COLUMNS).issubset(st.session_state.editable_transactions.columns):
    st.session_state.editable_transactions = st.session_state.editable_transactions.copy()
    if "_source_file" not in st.session_state.editable_transactions.columns:
        st.session_state.editable_transactions["_source_file"] = ""
    if "_source_row" not in st.session_state.editable_transactions.columns:
        st.session_state.editable_transactions["_source_row"] = pd.NA
    if "_category_reviewed" not in st.session_state.editable_transactions.columns:
        approved_category_cache = {**load_category_cache(), **st.session_state.session_category_cache}
        st.session_state.editable_transactions["_category_reviewed"] = (
            st.session_state.editable_transactions.apply(
                lambda row: row["main_category"] != "General Spending"
                or clean_description_for_matching(str(row["description"]))
                in approved_category_cache,
                axis=1,
            )
        )
    st.session_state.editable_transactions = st.session_state.editable_transactions[EDITABLE_COLUMNS]

st.session_state.editable_transactions["_category_reviewed"] = st.session_state.editable_transactions[
    "_category_reviewed"
].map(
    lambda value: False
    if pd.isna(value)
    else value.strip().casefold() in {"true", "1", "yes"}
    if isinstance(value, str)
    else bool(value)
).astype(bool)

if st.session_state.get("export_editor_schema_version") != 5:
    st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
    st.session_state.export_editor_revision += 1
    st.session_state.export_editor_schema_version = 5

with st.sidebar:
    st.header("Imported data")
    current_data = st.session_state.standardized_transactions
    st.metric("Imported rows", len(current_data))
    if not current_data.empty:
        st.metric("Banks", current_data["bank"].nunique())
    if st.button("Clear imported data", width='stretch'):
        st.session_state.standardized_transactions = pd.DataFrame(columns=STANDARD_COLUMNS)
        st.session_state.editable_transactions = pd.DataFrame(columns=EDITABLE_COLUMNS)
        st.session_state.dropped_transactions = pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
        st.session_state.file_import_signatures = {}
        st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
        st.session_state.pop(f"dropped_editor_{st.session_state.dropped_editor_revision}", None)
        st.session_state.export_editor_revision += 1
        st.session_state.dropped_editor_revision += 1
        st.rerun()

    source_names = set()
    for frame, column in (
        (current_data, "source_file"),
        (st.session_state.editable_transactions, "_source_file"),
        (st.session_state.dropped_transactions, "source_file"),
    ):
        if column in frame.columns:
            source_names.update(frame[column].dropna().astype(str).loc[lambda values: values != ""])
    if source_names:
        with st.expander("Remove one statement's data", expanded=False):
            statement_to_remove = st.selectbox(
                "Statement",
                sorted(source_names),
                key="statement_to_remove",
            )
            if st.button("Remove selected statement data"):
                st.session_state.standardized_transactions = remove_source_rows(
                    st.session_state.standardized_transactions,
                    source_column="source_file",
                    source_names={statement_to_remove},
                )
                st.session_state.editable_transactions = remove_source_rows(
                    st.session_state.editable_transactions,
                    source_column="_source_file",
                    source_names={statement_to_remove},
                )
                st.session_state.dropped_transactions = remove_source_rows(
                    st.session_state.dropped_transactions,
                    source_column="source_file",
                    source_names={statement_to_remove},
                )
                st.session_state.file_import_signatures.pop(statement_to_remove, None)
                st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                st.session_state.pop(f"dropped_editor_{st.session_state.dropped_editor_revision}", None)
                st.session_state.export_editor_revision += 1
                st.session_state.dropped_editor_revision += 1
                st.session_state.pop("statement_to_remove", None)
                st.rerun()

    

if uploaded_files:
    st.subheader("1. Choose a statement")
    selected_file = st.selectbox(
        "Statement file",
        uploaded_files,
        format_func=lambda uploaded: uploaded.name,
        key="selected_upload",
    )
    content = selected_file.getvalue()
    uploaded_name_counts = pd.Series([uploaded.name for uploaded in uploaded_files]).value_counts()
    duplicate_name = uploaded_name_counts.get(selected_file.name, 0) > 1
    if duplicate_name:
        st.error("Two uploaded files have the same name. Remove or rename one so each statement can be tracked safely.")
    file_digest = hashlib.sha256(content).hexdigest()
    detected_bank = detect_bank(selected_file.name)
    built_in_banks = [
        name
        for name in BANK_OPTIONS
        if name not in ("Auto-detect from filename", "Other / new bank")
    ]
    custom_bank_names = sorted(
        name for name in bank_profiles if name not in built_in_banks
    )
    bank_options = [
        "Auto-detect from filename",
        *built_in_banks,
        *custom_bank_names,
        "Other / new bank",
    ]
    detected_bank_choice = detected_bank if detected_bank in bank_options else BANK_OPTIONS[-1]
    bank_key = f"bank_{file_digest[:12]}"
    bank_index = bank_options.index(detected_bank_choice)
    bank_choice = st.selectbox(
        "Bank or credit union",
        bank_options,
        index=bank_index,
        key=bank_key,
    )
    needs_custom_bank = bank_choice == "Other / new bank"
    bank_name = (
        st.text_input(
            "Enter the bank name",
            key=f"custom_bank_{file_digest[:12]}",
            help="Known-bank import defaults are applied automatically from saved formats.",
        ).strip()
        if needs_custom_bank
        else detected_bank
        if bank_choice == "Auto-detect from filename"
        else bank_choice
    )
    bank_profile = bank_profiles.get(bank_name, {})
    profile_scope = normalize_name(bank_name or bank_choice) or "newbank"
    detected_delimiter = detect_delimiter(content)
    delimiter_names = list(DELIMITER_OPTIONS)
    detected_label = next(
        (label for label, value in DELIMITER_OPTIONS.items() if value == detected_delimiter),
        "Comma (,)",
    )
    delimiter_label = st.selectbox(
        "Values are separated by",
        delimiter_names,
        index=delimiter_names.index(
            bank_profile.get("delimiter")
            if bank_profile.get("delimiter") in delimiter_names
            else "Auto-detect"
        ),
        key=f"delimiter_{file_digest[:12]}_{profile_scope}",
        help=f"The detected separator is {detected_label}.",
    )
    delimiter = DELIMITER_OPTIONS[delimiter_label] or detected_delimiter

    try:
        preview = uploaded_csv_preview(content, delimiter)
        headers = [str(header).strip() for header in preview.columns]
        preview.columns = headers
        if not headers:
            st.error("This file does not appear to have a CSV header row.")
            st.stop()
    except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as error:
        st.error(f"Could not read this CSV: {error}")
        st.stop()

    with st.expander("Preview the first rows", expanded=False):
        st.dataframe(preview, width='stretch', hide_index=True)

    st.subheader("2. Identify the account")
    account_default = str(bank_profile.get("account", ""))
    normalized_account_default = normalize_account_type(account_default) if account_default else ""
    account_default_index = next(
        (
            index
            for index, option in enumerate(ACCOUNT_OPTIONS)
            if option != "Other" and normalize_account_type(option) == normalized_account_default
        ),
        ACCOUNT_OPTIONS.index("Other") if account_default and normalized_account_default else 0,
    )
    account_choice = st.selectbox(
        "Account type",
        ACCOUNT_OPTIONS,
        index=account_default_index,
        key=f"account_{file_digest[:12]}_{profile_scope}",
    )
    account_name = (
        st.text_input(
            "Enter the account type",
            value=account_default,
            key=f"custom_account_{file_digest[:12]}_{profile_scope}",
        ).strip()
        if account_choice == "Other"
        else None
        if account_choice == "Select an account type"
        else normalize_account_type(account_choice)
    )

    st.subheader("3. Match the columns")
    st.caption("Choose which column in this statement matches each field. Suggested matches are preselected when possible.")
    key_prefix = f"{file_digest[:12]}_{profile_scope}"
    profile_mapping = bank_profile.get("mapping", {})
    if not isinstance(profile_mapping, dict):
        profile_mapping = {}
    date_column = column_picker(
        "Transaction date",
        headers,
        field="date",
        widget_key=f"date_{key_prefix}",
        default_value=profile_mapping.get("date"),
    )
    description_column = column_picker(
        "Description",
        headers,
        field="description",
        widget_key=f"description_{key_prefix}",
        default_value=profile_mapping.get("description"),
    )
    amount_mode_options = ["One signed amount column", "Separate debit and credit columns"]
    default_amount_mode = bank_profile.get("amount_mode", amount_mode_options[0])
    amount_mode = st.radio(
        "How are transaction amounts shown?",
        amount_mode_options,
        index=amount_mode_options.index(default_amount_mode)
        if default_amount_mode in amount_mode_options
        else 0,
        horizontal=True,
        key=f"amount_mode_{key_prefix}",
    )
    positive_amounts_are_debits = bool(bank_profile.get("positive_amounts_are_debits", False))
    if amount_mode == "One signed amount column":
        sign_convention = st.radio(
            "Amount sign convention",
            [
                "Negative = money out; positive = money in",
                "Positive = money out; negative = money in",
            ],
            index=1 if positive_amounts_are_debits else 0,
            key=f"amount_sign_{key_prefix}",
            horizontal=True,
        )
        positive_amounts_are_debits = sign_convention.startswith("Positive")
    else:
        st.caption("Debit values become negative expenses; credit values become positive deposits.")

    mapping: dict[str, str] = {}
    if date_column:
        mapping["date"] = date_column
    if description_column:
        mapping["description"] = description_column
    if amount_mode == "One signed amount column":
        amount_column = column_picker(
            "Amount",
            headers,
            field="amount",
            widget_key=f"amount_{key_prefix}",
            default_value=profile_mapping.get("amount"),
        )
        if amount_column:
            mapping["amount"] = amount_column
    else:
        left, right = st.columns(2)
        with left:
            debit_column = column_picker(
                "Debit / money out",
                headers,
                field="debit",
                widget_key=f"debit_{key_prefix}",
                default_value=profile_mapping.get("debit"),
            )
        with right:
            credit_column = column_picker(
                "Credit / money in",
                headers,
                field="credit",
                widget_key=f"credit_{key_prefix}",
                default_value=profile_mapping.get("credit"),
            )
        if debit_column:
            mapping["debit"] = debit_column
        if credit_column:
            mapping["credit"] = credit_column

    with st.expander("Optional columns", expanded=False):
        optional_fields = (
            ("posted_date", "Posted date"),
            ("transaction_id", "Transaction ID"),
            ("bank_category", "Bank's category or transaction type"),
            ("status", "Transaction status"),
            ("currency", "Currency"),
        )
        for field, label in optional_fields:
            source_column = column_picker(
                label,
                headers,
                field=field,
                widget_key=f"{field}_{key_prefix}",
                optional=True,
                default_value=profile_mapping.get(field),
            )
            if source_column:
                mapping[field] = source_column

    number_format_label = st.selectbox(
        "Number format",
        list(NUMBER_FORMATS),
        index=list(NUMBER_FORMATS).index(bank_profile["number_format"])
        if bank_profile.get("number_format") in NUMBER_FORMATS
        else 0,
        key=f"number_format_{key_prefix}",
        help="Choose the decimal and thousands separators used in the amount columns.",
    )
    decimal_separator, thousands_separator = NUMBER_FORMATS[number_format_label]

    current_signature = build_import_signature(
        file_digest,
        bank=bank_name,
        account=account_name or "",
        column_mapping=mapping,
        delimiter=delimiter,
        decimal=decimal_separator,
        thousands=thousands_separator,
        positive_amounts_are_debits=positive_amounts_are_debits,
    )
    previous_signature = st.session_state.file_import_signatures.get(selected_file.name)
    already_current = previous_signature == current_signature
    replacing_existing = previous_signature is not None and not already_current
    if already_current:
        st.info("This file is already imported with these settings.")
    elif replacing_existing:
        st.info("Settings changed. Submitting will replace this file's existing transactions.")

    if st.button(
        "Update this file's import" if replacing_existing else "Standardize and add statement",
        type="primary",
        disabled=already_current or duplicate_name,
        width='stretch',
    ):
        if not bank_name:
            st.error("Enter a bank name before importing this statement.")
        elif not account_name:
            st.error("Select or enter the account type before importing this statement.")
        elif not date_column or not description_column:
            st.error("Select both a transaction date column and a description column.")
        elif "amount" not in mapping and not {"debit", "credit"}.issubset(mapping):
            st.error("Select an amount column, or select both debit and credit columns.")
        else:
            suffix = Path(selected_file.name).suffix or ".csv"
            temp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
                    temp_file.write(content)
                    temp_path = Path(temp_file.name)
                standardized = load_transactions(
                    temp_path,
                    bank=bank_name,
                    account=account_name,
                    column_mapping=mapping,
                    decimal=decimal_separator,
                    thousands=thousands_separator,
                    delimiter=delimiter,
                    positive_amounts_are_debits=positive_amounts_are_debits,
                )
                standardized["source_file"] = selected_file.name
                declined = getattr(
                    standardized,
                    "_declined_transactions",
                    pd.DataFrame(columns=STANDARD_COLUMNS),
                )
                declined = declined.copy()
                if not declined.empty:
                    declined["source_file"] = selected_file.name
                exportable, filtered = split_filtered_transactions(standardized)
                existing_export = st.session_state.editable_transactions
                if "_source_file" in existing_export.columns:
                    existing_export = existing_export.loc[
                        existing_export["_source_file"]
                        .astype("string")
                        .fillna("")
                        .ne(selected_file.name)
                    ]
                exportable, duplicates = split_duplicate_transactions(
                    exportable,
                    existing_export,
                )
                if not duplicates.empty:
                    filtered = pd.concat([filtered, duplicates], ignore_index=True)
                new_export = format_final_export(exportable).reset_index(drop=True)
                new_export["_source_file"] = selected_file.name
                new_export["_source_row"] = (
                    exportable.sort_values("date", kind="stable")["source_row"].reset_index(drop=True)
                    if "source_row" in exportable
                    else pd.Series([pd.NA] * len(new_export), dtype="object")
                )
                new_export["_category_reviewed"] = new_export["main_category"].ne(
                    "General Spending"
                )
                approved_category_cache = {**load_category_cache(), **st.session_state.session_category_cache}
                if approved_category_cache:
                    cached_descriptions = new_export["description"].map(
                        lambda value: clean_description_for_matching(str(value))
                    )
                    new_export["_category_reviewed"] |= cached_descriptions.isin(
                        approved_category_cache
                    )
                review_parts = []
                if not filtered.empty:
                    filter_reasons = filtered["removal_reason"].tolist()
                    review_parts.append(
                        make_dropped_review_rows(
                            filtered.drop(columns="removal_reason"), filter_reasons
                        )
                    )
                if not declined.empty:
                    review_parts.append(
                        make_dropped_review_rows(
                            declined,
                            ["Bank status is Declined."] * len(declined),
                        )
                    )
                declined_rows = standardized.attrs.get("declined_rows_dropped", 0)
                new_review = (
                    pd.concat(review_parts, ignore_index=True)
                    if review_parts
                    else pd.DataFrame(columns=DROPPED_REVIEW_COLUMNS)
                )
                st.session_state.standardized_transactions = replace_source_rows(
                    st.session_state.standardized_transactions,
                    standardized,
                    source_column="source_file",
                    source_name=selected_file.name,
                )
                st.session_state.editable_transactions = replace_source_rows(
                    st.session_state.editable_transactions,
                    new_export,
                    source_column="_source_file",
                    source_name=selected_file.name,
                )
                st.session_state.dropped_transactions = replace_source_rows(
                    st.session_state.dropped_transactions,
                    new_review,
                    source_column="source_file",
                    source_name=selected_file.name,
                )
                if review_parts:
                    st.session_state.pop(
                        f"dropped_editor_{st.session_state.dropped_editor_revision}", None
                    )
                    st.session_state.dropped_editor_revision += 1
                st.session_state.file_import_signatures[selected_file.name] = current_signature
                st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                st.session_state.export_editor_revision += 1
                excluded_count = declined_rows + len(filtered)
                declined_message = (
                    f" Excluded {excluded_count:,} declined, transfer, or payment transaction(s)."
                    if excluded_count
                    else ""
                )
                st.success(
                    f"Processed {len(standardized):,} transactions from {selected_file.name}."
                    f"{declined_message}"
                )
            except (ImportFormatError, OSError, ValueError) as error:
                st.error(f"This statement could not be standardized: {error}")
                if (
                    "truth value of a" in str(error)
                    or "Columns must be same length as key" in str(error)
                    or "Wrong number of items passed" in str(error)
                ):
                    detail = "".join(traceback.format_exc())
                    log_path = Path(tempfile.gettempdir()) / "standardize_errors.log"
                    try:
                        with log_path.open("a", encoding="utf-8") as log_file:
                            log_file.write(
                                f"\n=== {selected_file.name} at {pd.Timestamp.now()} ===\n{detail}"
                            )
                    except OSError:
                        pass
                    with st.expander("See the failing step for this file", expanded=False):
                        st.code(detail)
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

if (
    not st.session_state.standardized_transactions.empty
    or not st.session_state.dropped_transactions.empty
):
    transactions = st.session_state.standardized_transactions
    st.subheader("4. Review, edit and download")
    st.info(
        "Before downloading, check dates, signs and amounts, and confirm merchants and categories. "
        "Change merchants and categories directly in the table or in the review list below. "
        "Reviewed merchant and category choices are saved as mappings, so matching descriptions "
        "in future statements follow them automatically and skip the review list. "
        "Transactions under General Spending must be updated to a real category in the review panel. "
        "Review transfers carefully: if a transfer was not caught by the rules, set its main category "
        "to Transfer. The app fills N/A and moves it to the excluded transaction review list."
    )
    st.caption("Transactions are shown oldest to newest. Editing a date reorders the table; type, account, bank and category are selection-only except for category choices.")
    editable = sort_rows_by_date(
        complete_missing_categories(st.session_state.editable_transactions)
    ).reset_index(drop=True)
    st.session_state.editable_transactions = editable
    editor_data = editable.copy()
    editor_data.insert(0, "row_number", range(1, len(editor_data) + 1))
    editor_data["sub_category"] = [
        CATEGORY_PAIR_TO_LABEL.get((str(main), str(sub)), "")
        for main, sub in zip(editable["main_category"], editable["sub_category"])
    ]
    editor_data["_apply_to"] = EDIT_APPLY_ALL
    search_query = st.text_input(
        "Search transactions",
        placeholder="Description, merchant, category, amount...",
        key="transaction_search",
    ).strip()
    previous_search_query = st.session_state.get("_previous_transaction_search")
    if previous_search_query != search_query:
        if previous_search_query is not None:
            st.session_state.export_editor_revision += 1
        st.session_state._previous_transaction_search = search_query
    if search_query:
        searchable_rows = [
            " ".join(row)
            for row in editor_data.astype("string").fillna("").to_numpy(dtype=str)
        ]
        matching_rows = fuzzy_match_indices(search_query, searchable_rows)
        visible_editor_data = editor_data.iloc[matching_rows].copy()
        st.caption(f"Showing {len(visible_editor_data):,} of {len(editor_data):,} transactions.")
    else:
        visible_editor_data = editor_data
    account_options = sorted(
        set(editable["account"].dropna().astype(str))
        | {"checking", "savings", "credit card", "money market", "gold account"}
    )
    bank_options = sorted(
        set(editable["bank"].dropna().astype(str))
        | {normalize_bank_name(bank) for bank in transactions["bank"].dropna().astype(str)}
    )
    edit_mode = st.session_state.get("editor_edit_mode", False)
    save_clicked = False

    toolbar_cols = st.columns([1, 2, 5])
    with toolbar_cols[0]:
        if edit_mode:
            if st.button("Save changes", type="primary"):
                save_clicked = True
        else:
            if st.button("Edit table"):
                st.session_state.editor_edit_mode = True
                st.rerun()
    if edit_mode:
        st.caption(
            "Editing in progress \u2014 nothing is saved until you press **Save changes**. "
            "Choose a category in the **Sub-category (Main :: Sub)** column; Main category "
            "is derived from it automatically, so they can never disagree. By default "
            "**Apply to** is **All matching**, so a category change updates every "
            "transaction with the same description; set it to **Only this row** to make "
            "the edit a one-time occurrence. General Spending isn't offered in the table."
        )

    if edit_mode:
        edited_editor_data = st.data_editor(
            visible_editor_data,
            key=f"export_editor_{st.session_state.export_editor_revision}",
            num_rows="fixed" if search_query else "dynamic",
            width="stretch",
            hide_index=True,
            disabled=False,
            column_config={
                "row_number": st.column_config.NumberColumn("Row", disabled=True, width=56),
                "date": st.column_config.TextColumn("Date (MM/DD/YYYY)", width=100, required=True),
                "description": st.column_config.TextColumn("Description", width=180, required=True),
                "merchant": st.column_config.TextColumn("Merchant", width=160, required=True),
                "type": st.column_config.SelectboxColumn(
                    "Type", options=["debit", "credit"], width=60, required=True
                ),
                "amount": st.column_config.NumberColumn("Amount", format="$%.2f", width=80, required=True),
                "main_category": None,
                "sub_category": st.column_config.SelectboxColumn(
                    "Sub-category (Main :: Sub)",
                    options=[label for label in CATEGORY_PAIR_OPTIONS if not label.startswith("General Spending")],
                    width=190,
                    required=True,
                ),
                "bank": st.column_config.SelectboxColumn("Bank", options=bank_options, width=80, required=True),
                "account": st.column_config.SelectboxColumn("Account", options=account_options, width=80, required=True),
                "_apply_to": st.column_config.SelectboxColumn(
                    "Apply to",
                    options=[EDIT_APPLY_ALL, EDIT_APPLY_ONLY_THIS_ROW],
                    default=EDIT_APPLY_ALL,
                    width=110,
                    help=(
                        "\"All matching\" applies a category change to every transaction with the "
                        "same description. \"Only this row\" makes it a one-time edit that changes "
                        "just this transaction."
                    ),
                ),
                "_category_reviewed": None,
                "_source_file": None,
                "_source_row": None,
            },
        )
        if search_query and not edited_editor_data.empty:
            all_editor_data = editor_data.set_index("row_number")
            all_editor_data.update(edited_editor_data.set_index("row_number"))
            edited_editor_data = all_editor_data.reset_index()
        elif search_query:
            edited_editor_data = editor_data

        if not save_clicked:
            # Drafting: edits stay in the widget and are NOT committed. Downstream
            # review, validation, and download all use the last-saved data, so no
            # errors flash during editing and the screen does not re-sort/reload.
            edited_transactions = editable[FINAL_EXPORT_COLUMNS].copy()
            edited_source_metadata = editable[EDITABLE_SOURCE_COLUMNS].copy().reset_index(drop=True)
            editor_row_numbers = editor_data["row_number"].reset_index(drop=True)
            pair_mismatch = pd.Series(False, index=edited_transactions.index)
        else:
            # Save pressed: commit the editor contents to the session.
            original_editor_data = editor_data.set_index("row_number")
            editor_row_numbers = edited_editor_data["row_number"].reset_index(drop=True)
            edited_source_metadata = edited_editor_data[EDITABLE_SOURCE_COLUMNS].reset_index(drop=True)
            apply_flags = (
                edited_editor_data["_apply_to"].fillna(EDIT_APPLY_ALL).astype(str)
                == EDIT_APPLY_ONLY_THIS_ROW
            )
            edited_transactions = edited_editor_data.drop(
                columns=["row_number", "_apply_to", *EDITABLE_SOURCE_COLUMNS]
            ).copy()
# The composite "Main :: Sub" column is the single category input, so
            # Main category is always derived from it and can never disagree with
            # the sub-category the user picked.
            category_pairs = edited_transactions["sub_category"].map(CATEGORY_LABEL_TO_PAIR)
            edited_transactions["main_category"] = category_pairs.map(
                lambda pair: pair[0] if isinstance(pair, tuple) else pd.NA
            )
            edited_transactions["sub_category"] = category_pairs.map(
                lambda pair: pair[1] if isinstance(pair, tuple) else pd.NA
            )
            pair_mismatch = pd.Series(False, index=edited_transactions.index)

            reviewed_categories = edited_source_metadata["_category_reviewed"].fillna(False).astype(bool)
            reviewed_categories.loc[
                edited_transactions["main_category"].astype(str) != "General Spending"
            ] = True
            edited_source_metadata["_category_reviewed"] = reviewed_categories
            transfer_mask = (
                edited_transactions["main_category"].astype("string").str.strip().eq("Transfer").fillna(False)
            )
            transfers_moved = bool(transfer_mask.any())
            edited_transactions.loc[transfer_mask, "sub_category"] = CATEGORY_PAIR_TO_LABEL[("Transfer", "N/A")]

            # Propagate a category pair to every row that shares the same description
            # so bulk category edits stay consistent within the session. Rows set to
            # "Only this row" are one-time edits and are excluded both as sources and
            # as targets of that propagation.
            for row_position, row in edited_transactions.iterrows():
                if row["main_category"] == "Transfer":
                    continue
                if apply_flags.iloc[row_position]:
                    continue
                editor_row_number = editor_row_numbers.iloc[row_position]
                if pd.isna(editor_row_number) or editor_row_number not in original_editor_data.index:
                    continue
                original_row = original_editor_data.loc[editor_row_number]
                old_pair = CATEGORY_LABEL_TO_PAIR.get(str(original_row["sub_category"]))
                new_pair = (str(row["main_category"]), str(row["sub_category"]))
                was_reviewed = original_row["_category_reviewed"]
                is_reviewed = edited_source_metadata.at[row_position, "_category_reviewed"]
                was_reviewed = False if pd.isna(was_reviewed) else bool(was_reviewed)
                is_reviewed = False if pd.isna(is_reviewed) else bool(is_reviewed)
                if not is_reviewed or (old_pair == new_pair and was_reviewed):
                    continue
                description = str(row["description"])
                normalized_description = clean_description_for_matching(description)
                if not normalized_description:
                    continue
                # Category choices update this session's transactions only; the master
                # category mapping is read-only and is never modified from the app.
                matching_rows = edited_transactions["description"].map(
                    lambda value: clean_description_for_matching(str(value))
                ).eq(normalized_description)
                matching_rows = matching_rows & ~apply_flags
                edited_transactions.loc[matching_rows, "main_category"] = new_pair[0]
                edited_transactions.loc[matching_rows, "sub_category"] = new_pair[1]
                edited_source_metadata.loc[matching_rows, "_category_reviewed"] = True
                pair_mismatch.loc[matching_rows] = False

            edited_transactions = edited_transactions[FINAL_EXPORT_COLUMNS]
# Persist mappings the user actively changed in the main editor so future
            # imports reuse them and matching descriptions leave the review queue.
            change_rows = []
            for row_position, row in edited_transactions.iterrows():
                if apply_flags.iloc[row_position]:
                    continue
                editor_row_number = editor_row_numbers.iloc[row_position]
                if pd.isna(editor_row_number) or editor_row_number not in original_editor_data.index:
                    continue
                original_row = original_editor_data.loc[editor_row_number]
                merchant_changed = str(original_row["merchant"]) != str(row["merchant"])
                current_label = CATEGORY_PAIR_TO_LABEL.get(
                    (str(row["main_category"]), str(row["sub_category"])), ""
                )
                category_changed = str(original_row["sub_category"]) != current_label
                was_reviewed = (
                    False if pd.isna(original_row["_category_reviewed"])
                    else bool(original_row["_category_reviewed"])
                )
                is_reviewed = bool(edited_source_metadata.at[row_position, "_category_reviewed"])
                if merchant_changed or category_changed or (is_reviewed and not was_reviewed):
                    change_rows.append(
                        {
                            "description": row["description"],
                            "merchant": row["merchant"],
                            "main_category": row["main_category"],
                            "sub_category": row["sub_category"],
                        }
                    )
            if change_rows:
                for change_row in change_rows:
                    cleaned_desc = clean_description_for_matching(str(change_row["description"]))
                    if cleaned_desc:
                        st.session_state.session_merchant_cache[cleaned_desc] = str(change_row["merchant"])
                        st.session_state.session_category_cache[cleaned_desc] = {
                            "main_category": change_row["main_category"],
                            "sub_category": change_row["sub_category"],
                        }
            edited_transactions = edited_transactions.reset_index(drop=True)
            if transfers_moved:
                transfer_rows = make_transfer_review_rows(
                    edited_transactions.loc[transfer_mask].reset_index(drop=True),
                    source_files=edited_source_metadata.loc[transfer_mask, "_source_file"],
                    source_rows=edited_source_metadata.loc[transfer_mask, "_source_row"],
                )
                st.session_state.dropped_transactions = pd.concat(
                    [st.session_state.dropped_transactions, transfer_rows], ignore_index=True
                )
                st.session_state.pop(
                    f"dropped_editor_{st.session_state.dropped_editor_revision}", None
                )
                st.session_state.dropped_editor_revision += 1
                edited_transactions = edited_transactions.loc[~transfer_mask].reset_index(drop=True)
                edited_source_metadata = edited_source_metadata.loc[~transfer_mask].reset_index(drop=True)
                editor_row_numbers = editor_row_numbers.loc[~transfer_mask].reset_index(drop=True)
                pair_mismatch = pair_mismatch.loc[~transfer_mask].reset_index(drop=True)
                st.info(f"Moved {int(transfer_mask.sum())} manually marked transfer(s) to the excluded transaction review list.")

            date_order = sort_rows_by_date(edited_transactions).index
            date_order_changed = not date_order.equals(edited_transactions.index)
            if date_order_changed:
                edited_transactions = edited_transactions.loc[date_order].reset_index(drop=True)
                edited_source_metadata = edited_source_metadata.loc[date_order].reset_index(drop=True)
                editor_row_numbers = editor_row_numbers.loc[date_order].reset_index(drop=True)
                pair_mismatch = pair_mismatch.loc[date_order].reset_index(drop=True)

            st.session_state.editable_transactions = pd.concat(
                [edited_transactions, edited_source_metadata], axis=1
            )[EDITABLE_COLUMNS]
            if save_clicked:
                st.session_state.editor_edit_mode = False
                st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                st.session_state.export_editor_revision += 1
                st.rerun()
            if transfers_moved or date_order_changed:
                st.session_state.pop(f"export_editor_{st.session_state.export_editor_revision}", None)
                st.session_state.export_editor_revision += 1
                st.rerun()
    else:
        # Read-only table: compact columns so the whole table fits without
        # side-scrolling. Description and Merchant are clickable buttons so the
        # full text is available on demand.
        view_frame = visible_editor_data.copy()
        view_frame = view_frame.drop(
            columns=["main_category", "_category_reviewed", "_source_file", "_source_row", "_apply_to"]
        )
        full_lookup = editor_data.loc[
            view_frame.index, ["row_number", "date", "description", "merchant"]
        ].reset_index(drop=True)
        st.session_state["_table_view_full"] = full_lookup
        view_frame["description"] = [_shorten_cell(value, 26) for value in view_frame["description"]]
        view_frame["merchant"] = [_shorten_cell(value, 20) for value in view_frame["merchant"]]
        st.dataframe(
            view_frame,
            width="stretch",
            hide_index=True,
            column_config={
                "row_number": st.column_config.NumberColumn("Row", width=56),
                "date": st.column_config.TextColumn("Date (MM/DD/YYYY)", width=100),
                "description": st.column_config.ButtonColumn(
                    "Description",
                    type="tertiary",
                    alignment="left",
                    width=180,
                    on_click=_show_full_description,
                    key="table_view_desc_click",
                ),
                "merchant": st.column_config.ButtonColumn(
                    "Merchant",
                    type="tertiary",
                    alignment="left",
                    width=160,
                    on_click=_show_full_merchant,
                    key="table_view_merch_click",
                ),
                "type": st.column_config.TextColumn("Type", width=60),
                "amount": st.column_config.NumberColumn("Amount", format="$%.2f", width=80),
                "sub_category": st.column_config.TextColumn("Sub-category (Main :: Sub)", width=190),
                "bank": st.column_config.TextColumn("Bank", width=80),
                "account": st.column_config.TextColumn("Account", width=80),
            },
        )
        viewed = st.session_state.get("_table_viewed")
        if viewed:
            with st.container(border=True):
                st.markdown(
                    f"**Row {int(viewed.get('row', 0)) + 1}** \u00b7 "
                    f"Date: {viewed.get('date', '')}"
                )
                st.markdown(f"**Full description:** {viewed.get('description', '')}")
                st.markdown(f"**Full merchant:** {viewed.get('merchant', '')}")
                if st.button("Close details"):
                    st.session_state.pop("_table_viewed", None)
                    st.rerun()
        # Downstream review/validation/download use the committed (saved) data.
        edited_transactions = editable[FINAL_EXPORT_COLUMNS].copy()
        edited_source_metadata = editable[EDITABLE_SOURCE_COLUMNS].copy().reset_index(drop=True)
        editor_row_numbers = editor_data["row_number"].reset_index(drop=True)
        pair_mismatch = pd.Series(False, index=edited_transactions.index)


    candidate_rows = [
        index
        for index, row in st.session_state.editable_transactions.iterrows()
        if not bool(row["_category_reviewed"])
    ]
    with st.expander(
        f"Transactions needing review · {len(candidate_rows)} unresolved",
        expanded=st.session_state.review_expanded,
    ):
        st.caption(
            "Set the merchant and category for each transaction below. Categories start "
            "blank so you choose them explicitly — a selection is never pre-filled. "
            "Mappings are stored for this session only — they are not saved to disk and "
            "expire when you close the browser."
        )

        if not candidate_rows:
            st.info("No unresolved transactions need review.")
            st.session_state.pop("review_queue_total", None)
        else:
            # --- One-by-one flagged transaction review ---
            if "flag_review_cursor" not in st.session_state:
                st.session_state.flag_review_cursor = 0

            cursor = st.session_state.flag_review_cursor
            if cursor >= len(candidate_rows):
                cursor = 0
                st.session_state.flag_review_cursor = 0

            current_idx = candidate_rows[cursor]
            row = st.session_state.editable_transactions.loc[current_idx]

            # The counter reflects saved work: Next/Previous only change which
            # transaction is shown, so progress advances only when Apply is pressed
            # (which shrinks the queue). Recompute the baseline when data changes.
            if st.session_state.get("review_queue_total", 0) < len(candidate_rows):
                st.session_state.review_queue_total = len(candidate_rows)
            review_total = st.session_state.get("review_queue_total", len(candidate_rows))
            reviewed_so_far = max(review_total - len(candidate_rows), 0)

            progress_text = f"{reviewed_so_far} of {review_total} reviewed"
            st.progress(reviewed_so_far / max(review_total, 1), text=progress_text)

            # Exclude "General Spending" from the dropdown — it is a default fallback,
            # not a legitimate category choice for the review flow. Start the flagged
            # category inputs on placeholder options so the reviewer must explicitly
            # choose a category instead of accepting the first option by accident.
            review_main_options = [
                MAIN_CATEGORY_PLACEHOLDER,
                *[c for c in FINAL_MAIN_CATEGORY_OPTIONS if c != "General Spending"],
            ]

            with st.container(border=True):
                if str(row["main_category"]) == "General Spending":
                    st.warning(
                        "This transaction is currently set to **General Spending**, "
                        "the default fallback. Choose a real category below before applying."
                    )
                info_cols = st.columns([2, 2, 1, 1])
                with info_cols[0]:
                    st.markdown(f"**Description**  \n{row['description']}")
                with info_cols[1]:
                    st.markdown(
                        f"**Merchant**  \n{row['merchant'] if pd.notna(row['merchant']) else ''}"
                    )
                with info_cols[2]:
                    st.markdown(f"**Date**  \n{row['date']}")
                with info_cols[3]:
                    st.markdown(f"**Amount**  \n{row['amount']}")

                merchant_input = st.text_input(
                    "Merchant",
                    value=row['merchant'] if pd.notna(row['merchant']) else "",
                    key=f"flag_merchant_{current_idx}",
                )

                cat_cols = st.columns(2)
                with cat_cols[0]:
                    current_main = str(row["main_category"])
                    main_category = st.selectbox(
                        "Main category",
                        review_main_options,
                        index=(
                            review_main_options.index(current_main)
                            if current_main in review_main_options
                            else 0
                        ),
                        key=f"flag_main_cat_{current_idx}",
                    )
                with cat_cols[1]:
                    if main_category in CATEGORY_SUBCATEGORIES:
                        sub_options = [
                            SUB_CATEGORY_PLACEHOLDER,
                            *sorted(CATEGORY_SUBCATEGORIES[main_category]),
                        ]
                    else:
                        sub_options = [SUB_CATEGORY_PLACEHOLDER]
                    current_sub = str(row["sub_category"])
                    sub_category = st.selectbox(
                        "Sub-category",
                        sub_options,
                        index=(
                            sub_options.index(current_sub)
                            if current_sub in sub_options
                            and main_category in CATEGORY_SUBCATEGORIES
                            else 0
                        ),
                        key=f"flag_sub_cat_{current_idx}",
                    )

            def save_current() -> None:
                """Validate and save the current transaction, then rerun so it leaves the queue."""
                if main_category == MAIN_CATEGORY_PLACEHOLDER:
                    st.error("Choose a main category before applying.")
                    return
                if sub_category == SUB_CATEGORY_PLACEHOLDER:
                    st.error("Choose a sub-category before applying.")
                    return
                problem = edited_row_problem(merchant_input, main_category, sub_category)
                if problem:
                    st.error(f"Cannot apply: {problem}. Fix it first.")
                    return
                if main_category == "General Spending":
                    st.error(
                        "**General Spending** is the default fallback category and cannot "
                        "be selected here. Choose a real category (e.g. Food & Dining, "
                        "Transportation, Entertainment, etc.) before applying."
                    )
                    return
                updated = st.session_state.editable_transactions.copy()

                resolved_sub = (
                    "N/A" if main_category == "Transfer"
                    else sub_category
                )
                updated.at[current_idx, "merchant"] = str(merchant_input).strip()
                updated.at[current_idx, "main_category"] = main_category
                updated.at[current_idx, "sub_category"] = resolved_sub
                updated.at[current_idx, "_category_reviewed"] = True

                # Store mapping in session only — no disk writes
                cleaned_desc = clean_description_for_matching(str(row["description"]))
                if cleaned_desc:
                    st.session_state.session_merchant_cache[cleaned_desc] = str(merchant_input).strip()
                    st.session_state.session_category_cache[cleaned_desc] = {
                        "main_category": main_category,
                        "sub_category": resolved_sub,
                    }

                st.session_state.editable_transactions = updated
                st.session_state.review_expanded = True
                st.rerun()

            nav_cols = st.columns([1, 2, 1])
            with nav_cols[0]:
                if cursor > 0:
                    if st.button("← Previous", key=f"flag_prev_{current_idx}"):
                        st.session_state.flag_review_cursor = cursor - 1
                        st.rerun()

            with nav_cols[2]:
                if cursor < len(candidate_rows) - 1:
                    if st.button("Next →", key=f"flag_next_{current_idx}"):
                        st.session_state.flag_review_cursor = cursor + 1
                        st.rerun()

            st.markdown("---")

            if st.button(
                f"Apply & go to next ({len(candidate_rows) - cursor - 1} remaining)",
                type="primary",
                key=f"flag_apply_{current_idx}",
            ):
                save_current()
    download_data, validation_error = validate_final_export_rows(edited_transactions)
    download_data = sort_rows_by_date(download_data).reset_index(drop=True)
    if pair_mismatch.any():
        validation_error = "Choose a sub-category whose displayed main category matches the Main category column."
    unreviewed_mask = ~edited_source_metadata["_category_reviewed"].fillna(False).astype(bool)
    if unreviewed_mask.any():
        validation_error = (
            f"Review the category for {int(unreviewed_mask.sum())} transaction(s) before downloading. "
            "Transactions under General Spending must be updated to a real category."
        )
    row_issues = get_final_export_row_issues(edited_transactions)
    for row_index in pair_mismatch[pair_mismatch].index:
        row_issues.setdefault(int(row_index), []).append(
            "Sub-category does not match the selected Main category."
        )
    for row_index in unreviewed_mask[unreviewed_mask].index:
        row_issues.setdefault(int(row_index), []).append(
            "Category needs review before download."
        )

    if row_issues:
        issue_kinds = {tuple(issues) for issues in row_issues.values()}
        only_category_review = issue_kinds == {("Category needs review before download.",)}
        if only_category_review:
            st.warning(
                f"{len(row_issues):,} transaction(s) still need a category review "
                "before downloading. They are currently under General Spending and "
                "need to be updated to a real category in the review panel."
            )
        else:
            st.error(f"{len(row_issues):,} transaction(s) need changes before downloading.")
    amounts = pd.to_numeric(download_data["amount"], errors="coerce")
    metric_columns = st.columns(3)
    metric_columns[0].metric("Transactions", f"{len(download_data):,}")
    metric_columns[1].metric("Money in", f"${amounts[amounts > 0].sum():,.2f}")
    metric_columns[2].metric("Money out", f"${abs(amounts[amounts < 0].sum()):,.2f}")
    export_filename = normalize_export_filename(
        st.text_input(
            "Export file name",
            value="all_banks_final_categorized.csv",
            help="The .csv extension is added automatically if needed.",
        )
    )
    if validation_error and not row_issues:
        if "Date must use MM/DD/YYYY" in validation_error:
            st.error(validation_error)
        else:
            st.warning(validation_error)
    st.download_button(
        "Download categorized transactions",
        data=download_data.to_csv(index=False).encode("utf-8-sig"),
        file_name=export_filename,
        mime="text/csv",
        type="primary",
        disabled=validation_error is not None,
    )
    dropped_transactions = st.session_state.dropped_transactions
    if not dropped_transactions.empty:
        with st.expander(
            f"Review {len(dropped_transactions):,} excluded transactions", expanded=False
        ):
            st.caption("Review the reason each row was excluded. Select Restore and add selected rows back to the export.")
            review_editor = st.data_editor(
                dropped_transactions,
                key=f"dropped_editor_{st.session_state.dropped_editor_revision}",
                num_rows="fixed",
                width='stretch',
                hide_index=True,
                column_config={
                    "date": st.column_config.TextColumn("Date (MM/DD/YYYY)"),
                    "description": st.column_config.TextColumn("Description"),
                    "merchant": st.column_config.TextColumn("Merchant"),
                    "amount": st.column_config.NumberColumn("Amount", format="$%.2f"),
                    "removal_reason": st.column_config.TextColumn("Why it was removed", disabled=True),
                    "status": st.column_config.TextColumn("Status", disabled=True),
                    "source_file": st.column_config.TextColumn("Source file", disabled=True),
                    "source_row": st.column_config.NumberColumn("Source row", disabled=True),
                    "restore": st.column_config.CheckboxColumn("Restore", default=False),
                },
            )
            if st.button("Add selected transactions back", type="primary"):
                selected_indices = review_editor.index[review_editor["restore"].fillna(False).astype(bool)]
                if len(selected_indices) == 0:
                    st.info("Select at least one transaction to restore.")
                else:
                    selected_rows = review_editor.loc[selected_indices]
                    restored, restore_error = restored_rows_to_standardized(
                        selected_rows[FINAL_EXPORT_COLUMNS],
                        statuses=selected_rows["status"],
                        source_files=selected_rows["source_file"],
                        source_rows=selected_rows["source_row"],
                    )
                    if restore_error:
                        st.error(f"Cannot restore selected transactions yet: {restore_error}")
                    else:
                        st.session_state.standardized_transactions = pd.concat(
                            [st.session_state.standardized_transactions, restored], ignore_index=True
                        )
                        st.session_state.editable_transactions = pd.concat(
                            [
                                st.session_state.editable_transactions,
                                pd.concat(
                                    [
                                        selected_rows[FINAL_EXPORT_COLUMNS].reset_index(drop=True),
                                        selected_rows[["source_file", "source_row"]]
                                        .rename(columns={"source_file": "_source_file", "source_row": "_source_row"})
                                        .reset_index(drop=True),
                                        pd.DataFrame(
                                            {
                                                "_category_reviewed": selected_rows[
                                                    "main_category"
                                                ].ne("General Spending").to_numpy()
                                            }
                                        ),
                                    ],
                                    axis=1,
                                ),
                            ],
                            ignore_index=True,
                        )
                        st.session_state.dropped_transactions = review_editor.drop(
                            index=selected_indices
                        ).reset_index(drop=True)
                        st.session_state.pop(
                            f"dropped_editor_{st.session_state.dropped_editor_revision}", None
                        )
                        st.session_state.dropped_editor_revision += 1
                        st.session_state.pop(
                            f"export_editor_{st.session_state.export_editor_revision}", None
                        )
                        st.session_state.export_editor_revision += 1
                        st.success(f"Restored {len(restored):,} transaction(s).")
                        st.rerun()
else:
    st.info("Add a statement above to see standardized transactions here.")